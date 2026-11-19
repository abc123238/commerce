"""订单状态流转事件：漏斗与履约时长的唯一事实来源。

orders 表只保留当前状态与各阶段时间戳；本表记录每次状态跃迁，
occurred_at 用业务发生时间（而非写入时间），保证停留时长可复现。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OrderStatusEvent(Base):
    __tablename__ = "order_status_events"
    __table_args__ = (
        # 当前状态机每个目标状态最多出现一次；作为惰性物流推进的第二道幂等防线
        UniqueConstraint("order_id", "to_status", name="uq_order_status_events_order_to"),
        Index("ix_order_status_events_order_time", "order_id", "occurred_at"),
        Index("ix_order_status_events_status_time", "to_status", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20))
    event: Mapped[str] = mapped_column(String(30))

    # 业务发生时间：物流推进时等于 paid_at + 固定偏移，与 orders.shipped_at 对齐
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # api / agent / logistics_lazy / seed / backfill
    source: Mapped[str] = mapped_column(String(20), default="api")
    actor_user_id: Mapped[int | None] = mapped_column(nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
