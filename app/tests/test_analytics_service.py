"""统一指标层集成测试：口径、聚合与过滤（需要 Postgres）。"""
from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from app.db.models import OrderStatus
from app.services import analytics_service
from app.tests.factories import (
    create_category,
    create_order,
    create_product,
    create_user,
)

pytestmark = pytest.mark.integration

D1 = date(2026, 1, 1)
D2 = date(2026, 1, 2)


def _dt(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour, 0), tzinfo=timezone.utc)


async def _seed(db):
    user = await create_user(db)
    electronics = await create_category(db, "电子产品")
    books = await create_category(db, "图书")
    p1 = await create_product(db, electronics, "100.00", name="耳机")
    p2 = await create_product(db, books, "50.00", name="图书A")

    await create_order(db, user, [(p1, 2)], status=OrderStatus.PAID, paid_at=_dt(D1))
    # shipped/completed 也必须计入营收（paid_at 判据，而非 status）
    await create_order(
        db, user, [(p2, 1)], status=OrderStatus.COMPLETED, paid_at=_dt(D1)
    )
    await create_order(
        db, user, [(p1, 1)], status=OrderStatus.SHIPPED, paid_at=_dt(D2)
    )
    # 未支付 / 已取消不计入
    await create_order(db, user, [(p1, 1)], status=OrderStatus.CREATED)
    await db.commit()
    return p1, p2


async def test_sales_by_day(clean_db):
    await _seed(clean_db)
    rows = await analytics_service.sales_by_day(clean_db, D1, D2)
    by_day = {row.day: row for row in rows}
    assert by_day[D1].revenue == Decimal("250.00")
    assert by_day[D1].orders == 2
    assert by_day[D1].items == 3
    assert by_day[D2].revenue == Decimal("100.00")
    assert by_day[D2].orders == 1


async def test_sales_by_category(clean_db):
    await _seed(clean_db)
    rows = await analytics_service.sales_by_category(clean_db, D1, D2)
    by_name = {row.name: row for row in rows}
    assert by_name["电子产品"].revenue == Decimal("300.00")
    assert by_name["电子产品"].orders == 2
    assert by_name["电子产品"].items == 3
    assert by_name["图书"].revenue == Decimal("50.00")


async def test_top_products_and_totals(clean_db):
    p1, p2 = await _seed(clean_db)
    top = await analytics_service.top_products(clean_db, limit=5)
    assert [row.name for row in top] == [p1.name, p2.name]
    assert top[0].revenue == Decimal("300.00")
    assert top[0].quantity == 3

    revenue, orders = await analytics_service.revenue_and_orders(clean_db, D1, D2)
    assert revenue == Decimal("350.00")
    assert orders == 3


def test_growth_rate():
    assert analytics_service.growth_rate(Decimal("100"), Decimal("150")) == Decimal(
        "50"
    )
    assert analytics_service.growth_rate(Decimal("0"), Decimal("10")) is None
