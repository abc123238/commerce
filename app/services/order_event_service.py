"""订单状态事件的写入与治理。

事件与订单状态变更在同一事务内提交；唯一约束 (order_id, to_status)
保证惰性物流推进、重复请求、并发查询都只落一条事件。
写入使用 ON CONFLICT DO NOTHING：重复事件是幂等场景，不应让主事务失败。
"""
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.order_event import OrderStatusEvent


async def record_event(
    db: AsyncSession,
    *,
    order_id: int,
    to_status: str,
    from_status: str | None = None,
    occurred_at: datetime | None = None,
    source: str = "api",
    actor_user_id: int | None = None,
    meta: dict | None = None,
) -> None:
    stmt = (
        pg_insert(OrderStatusEvent)
        .values(
            order_id=order_id,
            from_status=from_status,
            to_status=to_status,
            event=to_status,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            source=source,
            actor_user_id=actor_user_id,
            meta=meta,
        )
        .on_conflict_do_nothing(index_elements=["order_id", "to_status"])
    )
    await db.execute(stmt)


async def delete_order_events(db: AsyncSession, order_id: int) -> None:
    await db.execute(
        delete(OrderStatusEvent).where(OrderStatusEvent.order_id == order_id)
    )


async def list_order_events(db: AsyncSession, order_id: int) -> list[OrderStatusEvent]:
    return list(
        await db.scalars(
            select(OrderStatusEvent)
            .where(OrderStatusEvent.order_id == order_id)
            .order_by(OrderStatusEvent.occurred_at)
        )
    )
