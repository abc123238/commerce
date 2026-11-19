"""订单接口集成测试：下单/支付/取消状态机 + 库存扣减与回补 + 模拟物流（需要 Postgres）。"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.models.order import Order, OrderStatus
from app.db.models.product import Category, Product

pytestmark = pytest.mark.integration

_AUTH = "/api/v1/auth"
_CART = "/api/v1/cart"
_ORDERS = "/api/v1/orders"


def _email() -> str:
    return f"ord_{uuid.uuid4().hex[:8]}@example.com"


async def _register(client) -> dict:
    r = await client.post(
        f"{_AUTH}/register", json={"email": _email(), "password": "Test@123456"}
    )
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _create_product(db_session, price: str = "99.00", stock: int = 5) -> int:
    cat = Category(name=f"cat_{uuid.uuid4().hex[:8]}")
    db_session.add(cat)
    await db_session.flush()
    product = Product(
        category_id=cat.id,
        name=f"商品_{uuid.uuid4().hex[:6]}",
        description="测试商品",
        price=price,
        stock=stock,
        status="on",
    )
    db_session.add(product)
    await db_session.commit()
    await db_session.refresh(product)
    return product.id


async def _stock_of(db_session, product_id: int) -> int:
    p = await db_session.get(Product, product_id)
    return p.stock


async def test_full_order_flow(client, db_session):
    headers = await _register(client)
    product_id = await _create_product(db_session, price="99.00", stock=5)
    assert await _stock_of(db_session, product_id) == 5

    # 加购 2 件
    add = await client.post(
        f"{_CART}/items", json={"product_id": product_id, "quantity": 2}, headers=headers
    )
    assert add.status_code == 201
    assert add.json()["items"][0]["quantity"] == 2

    # 下单：状态 created，库存扣减 5->3
    created = await client.post(f"{_ORDERS}", headers=headers)
    assert created.status_code == 201
    order = created.json()
    assert order["status"] == OrderStatus.CREATED
    assert Decimal(order["total"]) == Decimal("198.00")
    assert await _stock_of(db_session, product_id) == 3

    # 重复下单：购物车已清空 → 400
    empty = await client.post(f"{_ORDERS}", headers=headers)
    assert empty.status_code == 400

    # 支付：created -> paid
    paid = await client.post(f"{_ORDERS}/{order['order_no']}/pay", headers=headers)
    assert paid.status_code == 200
    assert paid.json()["status"] == OrderStatus.PAID

    # 已支付订单不可取消
    cancel_paid = await client.post(
        f"{_ORDERS}/{order['order_no']}/cancel", headers=headers
    )
    assert cancel_paid.status_code == 400

    # 列表与详情可见
    listing = await client.get(f"{_ORDERS}", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    detail = await client.get(f"{_ORDERS}/{order['order_no']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["order_no"] == order["order_no"]


async def test_cancel_restores_stock(client, db_session):
    headers = await _register(client)
    product_id = await _create_product(db_session, price="50.00", stock=4)
    assert await _stock_of(db_session, product_id) == 4

    await client.post(
        f"{_CART}/items", json={"product_id": product_id, "quantity": 1}, headers=headers
    )
    created = await client.post(f"{_ORDERS}", headers=headers)
    order = created.json()
    assert await _stock_of(db_session, product_id) == 3

    # 取消：created -> cancelled，库存回补 3->4
    cancelled = await client.post(
        f"{_ORDERS}/{order['order_no']}/cancel", headers=headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == OrderStatus.CANCELLED
    assert await _stock_of(db_session, product_id) == 4

    # 已取消订单不可再支付
    pay_cancelled = await client.post(
        f"{_ORDERS}/{order['order_no']}/pay", headers=headers
    )
    assert pay_cancelled.status_code == 400


async def test_order_not_found_for_other_user(client, db_session):
    headers = await _register(client)
    other = await _register(client)
    product_id = await _create_product(db_session, price="10.00", stock=2)
    await client.post(
        f"{_CART}/items", json={"product_id": product_id, "quantity": 1}, headers=headers
    )
    created = await client.post(f"{_ORDERS}", headers=headers)
    order_no = created.json()["order_no"]

    # 另一用户访问该订单 → 404（用户隔离）
    r = await client.get(f"{_ORDERS}/{order_no}", headers=other)
    assert r.status_code == 404


async def test_concurrent_cancel_restores_stock_once(client, db_session):
    """并发取消只回补一次库存：FOR UPDATE 行锁串行化，第二次取消拿到 400。"""
    headers = await _register(client)
    product_id = await _create_product(db_session, price="50.00", stock=4)
    await client.post(
        f"{_CART}/items", json={"product_id": product_id, "quantity": 1}, headers=headers
    )
    created = await client.post(f"{_ORDERS}", headers=headers)
    order_no = created.json()["order_no"]

    r1, r2 = await asyncio.gather(
        client.post(f"{_ORDERS}/{order_no}/cancel", headers=headers),
        client.post(f"{_ORDERS}/{order_no}/cancel", headers=headers),
    )
    assert sorted([r1.status_code, r2.status_code]) == [200, 400]
    # 下单扣 1（4→3），一次取消回补（3→4）；若双回补则为 5
    assert await _stock_of(db_session, product_id) == 4


async def test_concurrent_checkout_single_order(client, db_session):
    """同一购物车并发下单只生成一单、只扣一次库存：后到请求看到空购物车 → 400。"""
    headers = await _register(client)
    product_id = await _create_product(db_session, price="30.00", stock=3)
    await client.post(
        f"{_CART}/items", json={"product_id": product_id, "quantity": 2}, headers=headers
    )

    r1, r2 = await asyncio.gather(
        client.post(f"{_ORDERS}", headers=headers),
        client.post(f"{_ORDERS}", headers=headers),
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 400]
    # 库存只扣一次：3 → 1
    assert await _stock_of(db_session, product_id) == 1
    listing = await client.get(f"{_ORDERS}", headers=headers)
    assert listing.json()["total"] == 1


# ---------- 模拟物流 ----------

STAGE = settings.logistics_stage_seconds


async def _paid_order(client, db_session, headers) -> str:
    """造一笔已支付订单，返回 order_no。"""
    product_id = await _create_product(db_session, price="20.00", stock=10)
    await client.post(
        f"{_CART}/items", json={"product_id": product_id, "quantity": 1}, headers=headers
    )
    created = await client.post(f"{_ORDERS}", headers=headers)
    order_no = created.json()["order_no"]
    paid = await client.post(f"{_ORDERS}/{order_no}/pay", headers=headers)
    assert paid.status_code == 200
    return order_no


async def _backdate_paid_at(db_session, order_no: str, seconds: int) -> datetime:
    """把 paid_at 往前挪，模拟时间流逝（不用 sleep 等真实时钟）。"""
    order = await db_session.scalar(select(Order).where(Order.order_no == order_no))
    paid_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    order.paid_at = paid_at
    await db_session.commit()
    return paid_at


async def test_tracking_unpaid_order_has_no_nodes(client, db_session):
    """未支付订单没有物流：200 但轨迹为空，不写运单号。"""
    headers = await _register(client)
    product_id = await _create_product(db_session, price="20.00", stock=3)
    await client.post(
        f"{_CART}/items", json={"product_id": product_id, "quantity": 1}, headers=headers
    )
    created = await client.post(f"{_ORDERS}", headers=headers)
    order_no = created.json()["order_no"]

    r = await client.get(f"{_ORDERS}/{order_no}/tracking", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["nodes"] == []
    assert body["tracking_no"] is None
    assert body["status"] == OrderStatus.CREATED


async def test_tracking_partial_then_complete(client, db_session):
    """刚支付只有第 1 个节点到达；超过全程后推进到 completed 且轨迹走完。"""
    headers = await _register(client)
    order_no = await _paid_order(client, db_session, headers)

    fresh = (await client.get(f"{_ORDERS}/{order_no}/tracking", headers=headers)).json()
    assert fresh["status"] == OrderStatus.PAID
    assert [n["reached"] for n in fresh["nodes"]] == [True, False, False, False, False, False]

    # 时间前进到全程之后：一次查询连跳 paid -> shipped -> completed
    paid_at = await _backdate_paid_at(db_session, order_no, 5 * STAGE + 60)
    done = (await client.get(f"{_ORDERS}/{order_no}/tracking", headers=headers)).json()
    assert done["status"] == OrderStatus.COMPLETED
    assert all(n["reached"] for n in done["nodes"])
    assert done["tracking_no"].startswith("SF")
    assert done["carrier"]

    # completed_at 写的是 paid_at + 固定偏移，而非查询时刻 —— 因此可复现
    stored = await db_session.scalar(select(Order).where(Order.order_no == order_no))
    await db_session.refresh(stored)
    assert stored.shipped_at == paid_at + timedelta(seconds=STAGE)
    assert stored.completed_at == paid_at + timedelta(seconds=5 * STAGE)


async def test_tracking_advance_is_idempotent(client, db_session):
    """重复查询不会改写时间戳（状态前置判断保证每次跃迁只发生一次）。"""
    headers = await _register(client)
    order_no = await _paid_order(client, db_session, headers)
    await _backdate_paid_at(db_session, order_no, 5 * STAGE + 60)

    first = (await client.get(f"{_ORDERS}/{order_no}/tracking", headers=headers)).json()
    second = (await client.get(f"{_ORDERS}/{order_no}/tracking", headers=headers)).json()
    assert first == second


async def test_concurrent_tracking_advances_once(client, db_session):
    """并发查物流：FOR UPDATE 行锁保证时间戳只写一次，两次响应一致。"""
    headers = await _register(client)
    order_no = await _paid_order(client, db_session, headers)
    await _backdate_paid_at(db_session, order_no, 5 * STAGE + 60)

    r1, r2 = await asyncio.gather(
        client.get(f"{_ORDERS}/{order_no}/tracking", headers=headers),
        client.get(f"{_ORDERS}/{order_no}/tracking", headers=headers),
    )
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert r1.json()["status"] == OrderStatus.COMPLETED


async def test_tracking_cancelled_order(client, db_session):
    """已取消订单不推进、无轨迹。"""
    headers = await _register(client)
    product_id = await _create_product(db_session, price="20.00", stock=3)
    await client.post(
        f"{_CART}/items", json={"product_id": product_id, "quantity": 1}, headers=headers
    )
    created = await client.post(f"{_ORDERS}", headers=headers)
    order_no = created.json()["order_no"]
    await client.post(f"{_ORDERS}/{order_no}/cancel", headers=headers)

    r = await client.get(f"{_ORDERS}/{order_no}/tracking", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == OrderStatus.CANCELLED
    assert r.json()["nodes"] == []


async def test_tracking_not_found_for_other_user(client, db_session):
    """他人订单查物流 → 404，不会越权读到轨迹。"""
    headers = await _register(client)
    other = await _register(client)
    order_no = await _paid_order(client, db_session, headers)

    r = await client.get(f"{_ORDERS}/{order_no}/tracking", headers=other)
    assert r.status_code == 404
