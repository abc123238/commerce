"""购物车接口（需登录）。"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.cart import CartItemAdd, CartItemUpdate, CartOut
from app.services import cart_service

router = APIRouter(prefix="/cart", tags=["cart"])


@router.get("", response_model=CartOut)
async def get_cart(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CartOut:
    cart = await cart_service.get_cart(db, user.id)
    return cart_service.build_cart_out(cart)


@router.post("/items", response_model=CartOut, status_code=status.HTTP_201_CREATED)
async def add_item(
    payload: CartItemAdd,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CartOut:
    cart = await cart_service.add_item(db, user.id, payload.product_id, payload.quantity)
    return cart_service.build_cart_out(cart)


@router.patch("/items/{product_id}", response_model=CartOut)
async def update_quantity(
    product_id: int,
    payload: CartItemUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CartOut:
    cart = await cart_service.update_quantity(db, user.id, product_id, payload.quantity)
    return cart_service.build_cart_out(cart)


@router.delete("/items/{product_id}", response_model=CartOut)
async def remove_item(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CartOut:
    cart = await cart_service.remove_item(db, user.id, product_id)
    return cart_service.build_cart_out(cart)
