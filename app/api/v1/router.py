"""v1 API 路由汇总。"""
from fastapi import APIRouter

from app.api.v1 import analytics, auth, cart, chat, orders, products

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(products.router)
api_router.include_router(cart.router)
api_router.include_router(orders.router)
api_router.include_router(chat.router)
api_router.include_router(analytics.router)
