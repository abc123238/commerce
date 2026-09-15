"""购物车相关模型。"""
from decimal import Decimal

from pydantic import BaseModel, Field


class CartItemAdd(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1, le=99)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=99)


class CartItemOut(BaseModel):
    product_id: int
    name: str
    unit_price: Decimal
    quantity: int
    subtotal: Decimal


class CartOut(BaseModel):
    items: list[CartItemOut]
    total: Decimal
