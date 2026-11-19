"""经营数据结构化响应模型（admin 内部接口）。"""
from datetime import date as Date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

GroupBy = Literal["day", "category"]


class SalesPoint(BaseModel):
    date: Date | None = None
    category: str | None = None
    revenue: Decimal
    orders: int
    items: int


class SalesOut(BaseModel):
    start_date: Date
    end_date: Date
    group_by: GroupBy
    items: list[SalesPoint]


class TopProductItem(BaseModel):
    name: str
    revenue: Decimal
    quantity: int


class TopProductsOut(BaseModel):
    items: list[TopProductItem]
    total: int


class FunnelStepOut(BaseModel):
    status: str
    count: int
    rate: float
    avg_dwell_seconds: float | None = None


class FunnelOut(BaseModel):
    cohort: int
    cancelled: int
    steps: list[FunnelStepOut]


class AggRefreshIn(BaseModel):
    start_date: Date | None = None
    end_date: Date | None = None


class AggRefreshOut(BaseModel):
    start_date: Date
    end_date: Date
    days: int
    category_rows: int
    duration_ms: int


class DailyAggItem(BaseModel):
    stat_date: Date
    revenue: Decimal
    order_count: int
    item_count: int


class CategoryAggItem(BaseModel):
    stat_date: Date
    category_id: int
    category: str
    revenue: Decimal
    order_count: int
    item_count: int


class AggregatesOut(BaseModel):
    start_date: Date
    end_date: Date
    daily: list[DailyAggItem]
    categories: list[CategoryAggItem]
