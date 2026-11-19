"""订单状态事件：写点、幂等、惰性物流时间与漏斗（需要 Postgres）。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db.models import OrderStatus
from app.services import (
    analytics_service,
    logistics_service,
    order_event_service,
    order_service,
)
from app.tests.factories import create_category, create_order, create_product, create_user

pytestmark = pytest.mark.integration


async def _seed(db):
    user = await create_user(db)
    category = await create_category(db)
    product = await create_product(db, category)
    await db.commit()
    return user, product


async def _statuses(db, order_id: int) -> list[str]:
    events = await order_event_service.list_order_events(db, order_id)
    return [event.to_status for event in events]


async def test_create_pay_write_events(clean_db):
    user, product = await _seed(clean_db)

    order = await order_service.create_order(
        clean_db, user.id, [(product.id, 1)], source="agent"
    )
    events = await order_event_service.list_order_events(clean_db, order.id)
    assert [event.to_status for event in events] == ["created"]
    assert events[0].source == "agent"

    await order_service.pay_order(
        clean_db, order.order_no, user.id, source="agent"
    )
    assert set(await _statuses(clean_db, order.id)) == {"created", "paid"}

    # 重复写入同一事件是幂等场景，不应新增行
    await order_event_service.record_event(
        clean_db, order_id=order.id, to_status=OrderStatus.PAID
    )
    await clean_db.commit()
    assert len(await _statuses(clean_db, order.id)) == 2


async def test_cancel_writes_event(clean_db):
    user, product = await _seed(clean_db)
    order = await order_service.create_order(
        clean_db, user.id, [(product.id, 1)]
    )
    await order_service.cancel_order(clean_db, order.order_no, user.id)
    assert set(await _statuses(clean_db, order.id)) == {"created", "cancelled"}


async def test_logistics_events_use_business_time_and_are_idempotent(clean_db):
    user, product = await _seed(clean_db)
    now = datetime.now(timezone.utc)
    paid_at = now - timedelta(seconds=settings.logistics_stage_seconds * 6)
    order = await create_order(
        clean_db,
        user,
        [(product, 1)],
        status=OrderStatus.PAID,
        paid_at=paid_at,
        created_at=paid_at - timedelta(minutes=5),
    )
    await clean_db.commit()

    await logistics_service.advance_order_status(clean_db, order.order_no, user.id)
    events = await order_event_service.list_order_events(clean_db, order.id)
    by_status = {event.to_status: event for event in events}
    assert by_status["shipped"].occurred_at == paid_at + timedelta(
        seconds=settings.logistics_stage_seconds
    )
    assert by_status["completed"].occurred_at == paid_at + timedelta(
        seconds=5 * settings.logistics_stage_seconds
    )
    assert by_status["shipped"].source == "logistics_lazy"

    await logistics_service.advance_order_status(clean_db, order.order_no, user.id)
    assert len(await order_event_service.list_order_events(clean_db, order.id)) == 2


async def test_funnel_counts_and_dwell(clean_db):
    user, product = await _seed(clean_db)
    base = datetime.now(timezone.utc).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    paid = base + timedelta(hours=1)
    shipped = base + timedelta(hours=2)
    completed = base + timedelta(hours=3)
    cancelled = base + timedelta(hours=1)

    o1 = await create_order(
        clean_db, user, [(product, 1)], status=OrderStatus.CREATED, created_at=base
    )
    o2 = await create_order(
        clean_db,
        user,
        [(product, 1)],
        status=OrderStatus.PAID,
        created_at=base,
        paid_at=paid,
    )
    o3 = await create_order(
        clean_db,
        user,
        [(product, 1)],
        status=OrderStatus.SHIPPED,
        created_at=base,
        paid_at=paid,
        shipped_at=shipped,
    )
    o4 = await create_order(
        clean_db,
        user,
        [(product, 1)],
        status=OrderStatus.COMPLETED,
        created_at=base,
        paid_at=paid,
        shipped_at=shipped,
        completed_at=completed,
    )
    o5 = await create_order(
        clean_db,
        user,
        [(product, 1)],
        status=OrderStatus.CANCELLED,
        created_at=base,
        cancelled_at=cancelled,
    )

    for order in (o1, o2, o3, o4, o5):
        await order_event_service.record_event(
            clean_db,
            order_id=order.id,
            to_status=OrderStatus.CREATED,
            occurred_at=base,
        )
    for order in (o2, o3, o4):
        await order_event_service.record_event(
            clean_db,
            order_id=order.id,
            to_status=OrderStatus.PAID,
            from_status=OrderStatus.CREATED,
            occurred_at=paid,
        )
    for order in (o3, o4):
        await order_event_service.record_event(
            clean_db,
            order_id=order.id,
            to_status=OrderStatus.SHIPPED,
            from_status=OrderStatus.PAID,
            occurred_at=shipped,
        )
    await order_event_service.record_event(
        clean_db,
        order_id=o4.id,
        to_status=OrderStatus.COMPLETED,
        from_status=OrderStatus.SHIPPED,
        occurred_at=completed,
    )
    await order_event_service.record_event(
        clean_db,
        order_id=o5.id,
        to_status=OrderStatus.CANCELLED,
        from_status=OrderStatus.CREATED,
        occurred_at=cancelled,
    )
    await clean_db.commit()

    result = await analytics_service.funnel(clean_db, base.date(), base.date())
    assert result.cohort == 5
    assert result.cancelled == 1
    assert [step.count for step in result.steps] == [5, 3, 2, 1]
    assert [step.rate for step in result.steps] == [1.0, 0.6, 0.4, 0.2]
    assert result.steps[1].avg_dwell_seconds == 3600.0
    assert result.steps[2].avg_dwell_seconds == 3600.0
    assert result.steps[3].avg_dwell_seconds == 3600.0
