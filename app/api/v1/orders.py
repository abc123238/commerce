"""订单接口（需登录）：下单 / 列表 / 详情 / 模拟支付 / 取消。"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.order import OrderListOut, OrderOut
from app.services import order_service

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OrderOut:
    return await order_service.create_order_from_cart(db, user.id)


@router.get("", response_model=OrderListOut)
async def list_orders(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OrderListOut:
    items, total = await order_service.list_orders(db, user.id, page, size)
    return OrderListOut(items=items, total=total)


@router.get("/{order_no}", response_model=OrderOut)
async def get_order(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OrderOut:
    return await order_service.get_order_by_no(db, order_no, user.id)


@router.post("/{order_no}/pay", response_model=OrderOut)
async def pay_order(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OrderOut:
    return await order_service.pay_order(db, order_no, user.id)


@router.post("/{order_no}/cancel", response_model=OrderOut)
async def cancel_order(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OrderOut:
    return await order_service.cancel_order(db, order_no, user.id)
