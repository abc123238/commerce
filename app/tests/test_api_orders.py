"""订单接口集成测试：下单/支付/取消状态机 + 库存扣减与回补（需要 Postgres）。"""
import asyncio
import uuid
from decimal import Decimal

import pytest

from app.db.models.order import OrderStatus
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
