"""购物车业务逻辑。"""
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.cart import Cart, CartItem
from app.db.models.product import Product
from app.schemas.cart import CartItemOut, CartOut


async def _load_cart(db: AsyncSession, user_id: int) -> Cart:
    # populate_existing：会话内对 items 增删改后，同会话再次加载时强制覆盖
    # 已缓存的集合状态，否则 expire_on_commit=False 下会返回过期的空 items。
    cart = await db.scalar(
        select(Cart)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
        .where(Cart.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    if cart is None:
        cart = Cart(user_id=user_id)
        db.add(cart)
        await db.commit()
        await db.refresh(cart)
    return cart


async def get_cart(db: AsyncSession, user_id: int) -> Cart:
    return await _load_cart(db, user_id)


async def add_item(db: AsyncSession, user_id: int, product_id: int, quantity: int) -> Cart:
    product = await db.scalar(
        select(Product).where(Product.id == product_id, Product.status == "on")
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "商品不存在或已下架")
    if product.stock < quantity:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"商品 {product.name} 库存不足")

    cart = await _load_cart(db, user_id)
    item = await db.scalar(
        select(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product_id)
    )
    if item:
        if product.stock < item.quantity + quantity:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"商品 {product.name} 库存不足")
        item.quantity += quantity
    else:
        db.add(CartItem(cart_id=cart.id, product_id=product_id, quantity=quantity))
    await db.commit()
    return await _load_cart(db, user_id)


async def update_quantity(db: AsyncSession, user_id: int, product_id: int, quantity: int) -> Cart:
    cart = await _load_cart(db, user_id)
    item = await db.scalar(
        select(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product_id)
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "购物车中无此商品")
    product = await db.get(Product, product_id)
    if product is None or product.stock < quantity:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "商品库存不足")
    item.quantity = quantity
    await db.commit()
    return await _load_cart(db, user_id)


async def remove_item(db: AsyncSession, user_id: int, product_id: int) -> Cart:
    cart = await _load_cart(db, user_id)
    item = await db.scalar(
        select(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product_id)
    )
    if item:
        await db.delete(item)
        await db.commit()
    return await _load_cart(db, user_id)


def build_cart_out(cart: Cart) -> CartOut:
    items = [
        CartItemOut(
            product_id=item.product_id,
            name=item.product.name,
            unit_price=item.product.price,
            quantity=item.quantity,
            subtotal=item.product.price * item.quantity,
        )
        for item in cart.items
    ]
    total = sum((i.subtotal for i in items), Decimal("0"))
    return CartOut(items=items, total=total)
