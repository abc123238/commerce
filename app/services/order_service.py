"""订单业务：下单（事务+库存扣减）、支付、取消、查询。

事务由依赖注入的 session 管理（autobegin），配合 SELECT ... FOR UPDATE
行锁保证并发下库存扣减的一致性；任一步抛异常时 get_db 的 close() 回滚整笔。
"""
import secrets
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.cart import Cart
from app.db.models.order import Order, OrderItem, OrderStatus, Payment
from app.db.models.product import Product
from app.services import order_event_service

# 单笔订单最大商品种类数：防止对话下单被塞入超长清单
_MAX_KINDS = 10


def _new_order_no() -> str:
    return "ORD" + secrets.token_hex(8).upper()


def _new_transaction_no() -> str:
    return "PAY" + secrets.token_hex(16).upper()


async def get_order_by_no(db: AsyncSession, order_no: str, user_id: int | None = None) -> Order:
    query = select(Order).options(selectinload(Order.items)).where(Order.order_no == order_no)
    if user_id is not None:
        query = query.where(Order.user_id == user_id)
    order = await db.scalar(query)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    return order


async def get_order_by_id(db: AsyncSession, order_id: int) -> Order:
    order = await db.scalar(
        select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    return order


async def _lock_products(db: AsyncSession, wanted: dict[int, int]) -> dict[int, Product]:
    """按 product_id 升序 SELECT ... FOR UPDATE 并校验在售 / 库存。

    升序获取锁是多用户并发下单不互相死锁的关键，两条下单路径共用此函数。
    校验放在锁内：并发下只有拿到锁后才读到的库存才是可信的。
    """
    locked: dict[int, Product] = {}
    for product_id in sorted(wanted):
        product = await db.scalar(
            select(Product).where(Product.id == product_id).with_for_update()
        )
        if product is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"商品 {product_id} 不存在")
        if product.status != "on":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"商品 {product.name} 已下架")
        if product.stock < wanted[product_id]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"商品 {product.name} 库存不足")
        locked[product_id] = product
    return locked


async def create_order_from_cart(
    db: AsyncSession, user_id: int, *, source: str = "api"
) -> Order:
    # 先锁购物车行：同一购物车的并发下单会被串行化。
    # 后到的请求阻塞到前一事务提交（items 已删除），重读后看到空购物车 → 400，
    # 避免「读旧快照 → 重复下单 → 库存双扣」。
    cart = await db.scalar(
        select(Cart)
        .options(selectinload(Cart.items))
        .where(Cart.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if cart is None or not cart.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "购物车为空")

    # 事务由依赖注入的 session 管理（autobegin），末尾统一 commit 保证原子性：
    # 任一步抛异常时，get_db 的 close() 会回滚整笔事务。
    locked = await _lock_products(db, {it.product_id: it.quantity for it in cart.items})

    order = Order(order_no=_new_order_no(), user_id=user_id, total=0)
    db.add(order)
    await db.flush()

    total = 0
    for item in cart.items:
        product = locked[item.product_id]
        product.stock -= item.quantity
        total += product.price * item.quantity
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name=product.name,
                quantity=item.quantity,
                unit_price=product.price,
            )
        )
    order.total = total

    for item in list(cart.items):
        await db.delete(item)

    await order_event_service.record_event(
        db,
        order_id=order.id,
        to_status=OrderStatus.CREATED,
        occurred_at=datetime.now(timezone.utc),
        source=source,
        actor_user_id=user_id,
    )
    await db.commit()
    return await get_order_by_id(db, order.id)


async def create_order(
    db: AsyncSession,
    user_id: int,
    items: list[tuple[int, int]],
    *,
    source: str = "api",
) -> Order:
    """按「商品 + 数量」直接下单，不复用购物车（供对话下单使用）。

    对话场景「帮我买 2 个耳机」不应该把购物车里已有的其他商品一起下单，
    所以单独开一条路径；其余与 create_order_from_cart 一致：product_id 升序
    FOR UPDATE 锁行、校验在售与库存、扣减库存、写 OrderItem 快照
    （单价以 DB 当前价为准，不信任调用方传入的价格），末尾一次 commit。

    items: [(product_id, quantity), ...]，重复 product_id 会合并数量。
    """
    merged: dict[int, int] = {}
    for product_id, quantity in items:
        if quantity <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "商品数量必须为正整数")
        merged[product_id] = merged.get(product_id, 0) + quantity
    if not merged:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "下单商品不能为空")
    if len(merged) > _MAX_KINDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"单笔订单最多 {_MAX_KINDS} 种商品"
        )

    # 与 create_order_from_cart 的差异：这条路径没有购物车行锁可用来串行化并发，
    # 防线是上层 pending 快照的原子消费（GETDEL）+ 这里的商品行锁。
    locked = await _lock_products(db, merged)

    order = Order(order_no=_new_order_no(), user_id=user_id, total=0)
    db.add(order)
    await db.flush()

    total = Decimal("0")
    for product_id in sorted(merged):
        product = locked[product_id]
        quantity = merged[product_id]
        product.stock -= quantity
        total += product.price * quantity
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name=product.name,
                quantity=quantity,
                unit_price=product.price,
            )
        )
    order.total = total

    await order_event_service.record_event(
        db,
        order_id=order.id,
        to_status=OrderStatus.CREATED,
        occurred_at=datetime.now(timezone.utc),
        source=source,
        actor_user_id=user_id,
    )
    await db.commit()
    return await get_order_by_id(db, order.id)


async def pay_order(
    db: AsyncSession, order_no: str, user_id: int, *, source: str = "api"
) -> Order:
    # 锁订单行后再校验状态：并发支付时只有一个事务能通过 CREATED 检查，
    # 避免重复插入 Payment（payments.order_id 有唯一约束）或重复变更状态。
    order = await db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.order_no == order_no, Order.user_id == user_id)
        .with_for_update()
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    if order.status != OrderStatus.CREATED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前状态不可支付")

    now = datetime.now(timezone.utc)
    order.status = OrderStatus.PAID
    order.paid_at = now
    db.add(
        Payment(
            order_id=order.id,
            transaction_no=_new_transaction_no(),
            method="mock",
            status="succeeded",
            paid_at=now,
        )
    )
    await order_event_service.record_event(
        db,
        order_id=order.id,
        to_status=OrderStatus.PAID,
        from_status=OrderStatus.CREATED,
        occurred_at=now,
        source=source,
        actor_user_id=user_id,
    )
    await db.commit()
    await db.refresh(order)
    return order


async def cancel_order(
    db: AsyncSession, order_no: str, user_id: int, *, source: str = "api"
) -> Order:
    # 锁订单行（FOR UPDATE），状态检查放入锁内：
    # 两次并发取消时只有一个事务能看到 CREATED，库存只回补一次。
    order = await db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.order_no == order_no, Order.user_id == user_id)
        .with_for_update()
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    if order.status != OrderStatus.CREATED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅未支付订单可取消")

    # 同 create_order_from_cart：事务由依赖注入的 session 管理（autobegin），
    # 末尾统一 commit；商品行锁按 product_id 升序获取，避免并发回补库存时死锁。
    now = datetime.now(timezone.utc)
    order.status = OrderStatus.CANCELLED
    order.cancelled_at = now
    for item in sorted(order.items, key=lambda it: it.product_id):
        product = await db.scalar(
            select(Product).where(Product.id == item.product_id).with_for_update()
        )
        if product:
            product.stock += item.quantity
    await order_event_service.record_event(
        db,
        order_id=order.id,
        to_status=OrderStatus.CANCELLED,
        from_status=OrderStatus.CREATED,
        occurred_at=now,
        source=source,
        actor_user_id=user_id,
    )
    await db.commit()

    return await get_order_by_id(db, order.id)


async def list_orders(
    db: AsyncSession, user_id: int, page: int = 1, size: int = 20
) -> tuple[list[Order], int]:
    where = [Order.user_id == user_id]
    total = await db.scalar(select(func.count()).select_from(Order).where(*where)) or 0
    query = (
        select(Order)
        .options(selectinload(Order.items))
        .where(*where)
        .order_by(Order.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    orders = list(await db.scalars(query))
    return orders, total
