"""商品与分类的业务逻辑。"""
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.product import Category, Product
from app.schemas.product import ProductCreate, ProductUpdate


async def list_categories(db: AsyncSession) -> list[Category]:
    return list(await db.scalars(select(Category).order_by(Category.id)))


async def create_category(db: AsyncSession, name: str) -> Category:
    existing = await db.scalar(select(Category).where(Category.name == name))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "分类已存在")
    category = Category(name=name)
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


async def list_products(
    db: AsyncSession,
    page: int = 1,
    size: int = 20,
    category_id: int | None = None,
    keyword: str | None = None,
) -> tuple[list[Product], int]:
    where = [Product.status == "on"]
    if category_id is not None:
        where.append(Product.category_id == category_id)
    if keyword:
        where.append(Product.name.ilike(f"%{keyword}%"))

    total = await db.scalar(select(func.count()).select_from(Product).where(*where)) or 0
    query = (
        select(Product)
        .where(*where)
        .order_by(Product.id)
        .offset((page - 1) * size)
        .limit(size)
    )
    products = list(await db.scalars(query))
    return products, total


async def get_product(db: AsyncSession, product_id: int) -> Product:
    product = await db.scalar(
        select(Product).where(Product.id == product_id, Product.status == "on")
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "商品不存在或已下架")
    return product


async def create_product(db: AsyncSession, data: ProductCreate) -> Product:
    if await db.get(Category, data.category_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "分类不存在")
    product = Product(**data.model_dump())
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product


async def update_product(db: AsyncSession, product_id: int, data: ProductUpdate) -> Product:
    product = await db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "商品不存在")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(product, key, value)
    await db.commit()
    await db.refresh(product)
    return product
