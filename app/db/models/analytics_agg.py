"""经营数据预聚合表：从订单事实表派生的日/品类汇总。

只作为读侧加速与口径固化，不承载写入；刷新逻辑见
app/services/analytics_agg_service.py。
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DailySalesAgg(Base):
    __tablename__ = "daily_sales_agg"

    stat_date: Mapped[date] = mapped_column(Date, primary_key=True)
    revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    order_count: Mapped[int] = mapped_column(default=0)
    item_count: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DailyCategorySalesAgg(Base):
    __tablename__ = "daily_category_sales_agg"
    __table_args__ = (
        UniqueConstraint(
            "stat_date", "category_id", name="uq_daily_category_sales_date_category"
        ),
        Index("ix_daily_category_sales_date", "stat_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stat_date: Mapped[date] = mapped_column(Date)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    order_count: Mapped[int] = mapped_column(default=0)
    item_count: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
