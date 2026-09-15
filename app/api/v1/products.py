"""商品接口：浏览（公开）+ 管理（仅 admin）。"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.db.session import get_db
from app.schemas.product import (
    CategoryCreate,
    CategoryOut,
    ProductCreate,
    ProductListOut,
    ProductOut,
    ProductUpdate,
)
from app.services import product_service

router = APIRouter(tags=["products"])


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(db: AsyncSession = Depends(get_db)) -> list:
    return await product_service.list_categories(db)


@router.post(
    "/categories",
    response_model=CategoryOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_category(
    payload: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_roles("admin")),
) -> CategoryOut:
    return await product_service.create_category(db, payload.name)


@router.get("/products", response_model=ProductListOut)
async def list_products(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    category_id: int | None = None,
    keyword: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> ProductListOut:
    items, total = await product_service.list_products(db, page, size, category_id, keyword)
    return ProductListOut(items=items, total=total)


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: int, db: AsyncSession = Depends(get_db)
) -> ProductOut:
    return await product_service.get_product(db, product_id)


@router.post(
    "/products",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_product(
    payload: ProductCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_roles("admin")),
) -> ProductOut:
    return await product_service.create_product(db, payload)


@router.patch("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_roles("admin")),
) -> ProductOut:
    return await product_service.update_product(db, product_id, payload)
