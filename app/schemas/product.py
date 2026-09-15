"""商品与分类相关模型。"""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class CategoryOut(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class ProductOut(BaseModel):
    id: int
    category_id: int
    name: str
    description: str
    price: Decimal
    stock: int
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProductListOut(BaseModel):
    items: list[ProductOut]
    total: int


class ProductCreate(BaseModel):
    category_id: int
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    price: Decimal = Field(gt=0)
    stock: int = Field(ge=0)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    price: Decimal | None = Field(default=None, gt=0)
    stock: int | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, pattern="^(on|off)$")
