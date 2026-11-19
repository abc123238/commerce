"""订单相关模型。"""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class OrderItemOut(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal

    model_config = ConfigDict(from_attributes=True)


class OrderOut(BaseModel):
    id: int
    order_no: str
    status: str
    total: Decimal
    created_at: datetime
    items: list[OrderItemOut] = Field(default_factory=list)
    paid_at: datetime | None = None
    shipped_at: datetime | None = None
    completed_at: datetime | None = None
    carrier: str | None = None
    tracking_no: str | None = None

    model_config = ConfigDict(from_attributes=True)


class OrderListOut(BaseModel):
    items: list[OrderOut]
    total: int


class TrackingNodeOut(BaseModel):
    name: str
    time: datetime
    reached: bool

    model_config = ConfigDict(from_attributes=True)


class TrackingOut(BaseModel):
    """物流轨迹：nodes 含未到达的节点（reached=False），由前端渲染为灰色待发生。"""

    order_no: str
    status: str
    carrier: str | None = None
    tracking_no: str | None = None
    paid_at: datetime | None = None
    nodes: list[TrackingNodeOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
