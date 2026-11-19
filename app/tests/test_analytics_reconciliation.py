"""预聚合对账：聚合表必须与实时指标层逐日/逐品类完全一致。"""
from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from app.db.models import OrderStatus
from app.services import analytics_agg_service, analytics_service
from app.tests.factories import (
    create_category,
    create_order,
    create_product,
    create_user,
)

pytestmark = pytest.mark.integration

D1 = date(2026, 2, 1)
D2 = date(2026, 2, 2)
D3 = date(2026, 2, 3)


def _dt(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour, 0), tzinfo=timezone.utc)


async def _seed(db):
    user = await create_user(db)
    electronics = await create_category(db, "电子产品")
    books = await create_category(db, "图书")
    p1 = await create_product(db, electronics, "100.00", name="耳机")
    p2 = await create_product(db, books, "50.00", name="图书A")

    await create_order(db, user, [(p1, 2)], status=OrderStatus.PAID, paid_at=_dt(D1))
    await create_order(db, user, [(p2, 1)], status=OrderStatus.SHIPPED, paid_at=_dt(D1))
    await create_order(db, user, [(p1, 1)], status=OrderStatus.COMPLETED, paid_at=_dt(D2))
    await create_order(db, user, [(p2, 3)], status=OrderStatus.PAID, paid_at=_dt(D3))
    await db.commit()
    return user, p1, p2


def _as_map(rows) -> dict:
    return {row.day: (row.revenue, row.orders, row.items) for row in rows}


async def test_aggregates_match_live_metrics(clean_db):
    await _seed(clean_db)

    result = await analytics_agg_service.refresh_daily_agg(clean_db, D1, D3)
    assert result["days"] == 3
    assert result["category_rows"] == 4  # D1 两个品类 + D2 一个 + D3 一个

    live_daily = _as_map(await analytics_service.sales_by_day(clean_db, D1, D3))
    agg_daily = {
        row.stat_date: (row.revenue, row.order_count, row.item_count)
        for row in await analytics_agg_service.read_daily_agg(clean_db, D1, D3)
    }
    assert agg_daily == live_daily
    assert agg_daily[D1] == (Decimal("250.00"), 2, 3)

    live_category = {
        (row.day, row.category_id): (row.revenue, row.orders, row.items)
        for row in await analytics_service.daily_category_sales(clean_db, D1, D3)
    }
    agg_category = {
        (row.stat_date, row.category_id): (
            row.revenue,
            row.order_count,
            row.item_count,
        )
        for row, _name in await analytics_agg_service.read_category_agg(
            clean_db, D1, D3
        )
    }
    assert agg_category == live_category


async def test_refresh_is_idempotent_and_updates_new_orders(clean_db):
    user, p1, _ = await _seed(clean_db)
    await analytics_agg_service.refresh_daily_agg(clean_db, D1, D3)
    before = {
        row.stat_date: (row.revenue, row.order_count, row.item_count)
        for row in await analytics_agg_service.read_daily_agg(clean_db, D1, D3)
    }

    await analytics_agg_service.refresh_daily_agg(clean_db, D1, D3)
    after = {
        row.stat_date: (row.revenue, row.order_count, row.item_count)
        for row in await analytics_agg_service.read_daily_agg(clean_db, D1, D3)
    }
    assert after == before

    await create_order(
        clean_db, user, [(p1, 1)], status=OrderStatus.PAID, paid_at=_dt(D2)
    )
    await clean_db.commit()
    await analytics_agg_service.refresh_daily_agg(clean_db, D1, D3)

    updated = {
        row.stat_date: (row.revenue, row.order_count, row.item_count)
        for row in await analytics_agg_service.read_daily_agg(clean_db, D1, D3)
    }
    assert updated[D2][0] == before[D2][0] + Decimal("100.00")
    assert updated[D2][1] == before[D2][1] + 1
