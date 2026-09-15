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

    model_config = ConfigDict(from_attributes=True)


class OrderListOut(BaseModel):
    items: list[OrderOut]
    total: int
