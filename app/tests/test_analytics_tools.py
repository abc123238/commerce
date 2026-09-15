"""经营分析工具集成测试（需要 Postgres）：基于真实订单数据聚合。

每个用例先清空业务表再种数据，保证断言不受历史测试污染。
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.agent.tools.analytics_tools import build_analytics_tools
from app.core.security import hash_password
from app.db.models import (
    Cart,
    CartItem,
    Category,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    Product,
    User,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def clean_db(db_session):
    # 按外键依赖顺序清理：支付->订单项->订单->购物车项->购物车->商品->分类->用户
    for model in (Payment, OrderItem, Order, CartItem, Cart, Product, Category, User):
        await db_session.execute(delete(model))
    await db_session.commit()
    return db_session


async def _seed(db_session) -> tuple[str, Product, Product]:
    user = User(email=f"an_{uuid4().hex[:8]}@e.com", password_hash=hash_password("T@123456"))
    db_session.add(user)
    await db_session.flush()
    db_session.add(Cart(user_id=user.id))
    cat = Category(name=f"cat_{uuid4().hex[:8]}")
    db_session.add(cat)
    await db_session.flush()
    p1 = Product(category_id=cat.id, name=f"商品A_{uuid4().hex[:6]}", description="", price="100.00", stock=10, status="on")
    p2 = Product(category_id=cat.id, name=f"商品B_{uuid4().hex[:6]}", description="", price="50.00", stock=10, status="on")
    db_session.add_all([p1, p2])
    await db_session.flush()
    now = datetime.now(timezone.utc)
    o1 = Order(order_no=f"ORD{uuid4().hex[:12].upper()}", user_id=user.id, total=200, status=OrderStatus.PAID, paid_at=now)
    o2 = Order(order_no=f"ORD{uuid4().hex[:12].upper()}", user_id=user.id, total=50, status=OrderStatus.PAID, paid_at=now)
    db_session.add_all([o1, o2])
    await db_session.flush()
    db_session.add_all(
        [
            OrderItem(order_id=o1.id, product_id=p1.id, product_name=p1.name, quantity=2, unit_price="100.00"),
            OrderItem(order_id=o2.id, product_id=p2.id, product_name=p2.name, quantity=1, unit_price="50.00"),
        ]
    )
    await db_session.commit()
    return cat.name, p1, p2


def _tools(db_session) -> dict:
    return {t.name: t for t in build_analytics_tools(db_session)}


async def test_query_sales_summary_by_category(clean_db):
    cat_name, _, _ = await _seed(clean_db)
    tools = _tools(clean_db)
    summary = await tools["query_sales_summary"].ainvoke(
        {"start_date": "2026-01-01", "end_date": "2030-01-01", "group_by": "category"}
    )
    assert cat_name in summary
    assert "¥250.00" in summary and "2 单" in summary


async def test_query_sales_summary_by_day(clean_db):
    await _seed(clean_db)
    tools = _tools(clean_db)
    today = datetime.now(timezone.utc).date().isoformat()
    summary = await tools["query_sales_summary"].ainvoke(
        {"start_date": today, "end_date": today, "group_by": "day"}
    )
    assert "¥250.00" in summary  # 200 + 50


async def test_query_sales_summary_empty_range(clean_db):
    await _seed(clean_db)
    tools = _tools(clean_db)
    summary = await tools["query_sales_summary"].ainvoke(
        {"start_date": "2019-01-01", "end_date": "2019-01-02", "group_by": "day"}
    )
    assert "无已支付订单数据" in summary


async def test_current_date(clean_db):
    tools = _tools(clean_db)
    r = await tools["current_date"].ainvoke({})
    assert len(r) == 10 and r[4] == "-" and r[7] == "-"


async def test_calc_growth(clean_db):
    tools = _tools(clean_db)
    r = await tools["calc_growth"].ainvoke({"base_value": "100", "current_value": "150"})
    assert "+50.0%" in r
    r0 = await tools["calc_growth"].ainvoke({"base_value": "0", "current_value": "10"})
    assert "无法计算" in r0


async def test_top_products_ordered_by_revenue(clean_db):
    _, p1, p2 = await _seed(clean_db)
    tools = _tools(clean_db)
    r = await tools["top_products"].ainvoke({"limit": 5})
    assert p1.name in r and p2.name in r
    assert r.index(p1.name) < r.index(p2.name)  # A 销售额更高，应排更前
