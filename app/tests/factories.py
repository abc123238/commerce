"""集成测试用的最小数据工厂（不写入事件，事件相关用例直接调用 service）。"""
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import Cart, Category, Order, OrderItem, OrderStatus, Product, User


async def create_user(db: AsyncSession, role: str = "customer") -> User:
    user = User(
        email=f"u_{uuid4().hex[:8]}@e.com",
        password_hash=hash_password("T@123456"),
        role=role,
    )
    db.add(user)
    await db.flush()
    db.add(Cart(user_id=user.id))
    await db.flush()
    return user


async def create_category(db: AsyncSession, name: str | None = None) -> Category:
    category = Category(name=name or f"cat_{uuid4().hex[:8]}")
    db.add(category)
    await db.flush()
    return category


async def create_product(
    db: AsyncSession,
    category: Category,
    price: str = "100.00",
    stock: int = 100,
    name: str | None = None,
) -> Product:
    product = Product(
        category_id=category.id,
        name=name or f"p_{uuid4().hex[:8]}",
        description="",
        price=Decimal(str(price)),
        stock=stock,
        status="on",
    )
    db.add(product)
    await db.flush()
    return product


async def create_order(
    db: AsyncSession,
    user: User,
    items: list[tuple[Product, int]],
    *,
    status: str = OrderStatus.PAID,
    paid_at: datetime | None = None,
    created_at: datetime | None = None,
    cancelled_at: datetime | None = None,
    shipped_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> Order:
    total = sum((Decimal(str(p.price)) * q for p, q in items), Decimal("0"))
    kwargs = dict(
        order_no=f"ORD{uuid4().hex[:12].upper()}",
        user_id=user.id,
        total=total,
        status=status,
        paid_at=paid_at,
        cancelled_at=cancelled_at,
        shipped_at=shipped_at,
        completed_at=completed_at,
    )
    if created_at is not None:
        kwargs["created_at"] = created_at
    order = Order(**kwargs)
    db.add(order)
    await db.flush()
    for product, quantity in items:
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name=product.name,
                quantity=quantity,
                unit_price=product.price,
            )
        )
    await db.flush()
    return order
