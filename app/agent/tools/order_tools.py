"""订单查询工具：读取真实订单表与模拟物流轨迹，仅限当前登录用户（权限内数据）。"""
from fastapi import HTTPException
from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.order import Order, status_cn
from app.services import logistics_service


def build_order_tools(db: AsyncSession, user_id: int) -> list:
    """工厂：将 db 会话与当前用户绑定到工具闭包。"""

    @tool
    async def query_order(order_no: str | None = None, limit: int = 3) -> str:
        """查询当前用户的订单信息。不传 order_no 时返回最近订单列表。

        Args:
            order_no: 订单号（可选，如 ORD 开头的编号）
            limit: 最多返回的订单条数，默认 3
        """
        query = (
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.user_id == user_id)
            .order_by(Order.id.desc())
        )
        if order_no:
            query = query.where(Order.order_no == order_no)
        else:
            query = query.limit(limit)
        orders = list(await db.scalars(query))
        if not orders:
            return f"未找到用户 {user_id} 的任何订单"
        lines = []
        for order in orders:
            items = "、".join(f"{it.product_name} x{it.quantity}" for it in order.items) or "无"
            tracking = f"，物流单号={order.tracking_no}" if order.tracking_no else ""
            lines.append(
                f"订单 {order.order_no}：状态={status_cn(order.status)}，金额={order.total} 元，"
                f"商品=[{items}]{tracking}"
            )
        return "\n".join(lines)

    @tool
    async def query_tracking(order_no: str | None = None) -> str:
        """查询订单的物流轨迹（模拟物流，仅限当前用户的订单）。

        调用会顺带把订单状态幂等推进到已发货/已完成，无需额外操作。
        未支付的订单没有物流信息。

        Args:
            order_no: 订单号；不传则查询最近一笔已支付订单的物流
        """
        try:
            if order_no:
                info = await logistics_service.get_tracking(db, order_no, user_id)
            else:
                info = await logistics_service.get_latest_tracking(db, user_id)
        except HTTPException as exc:
            return f"查询物流失败：{exc.detail}。"
        if info is None:
            return "未找到可查询物流的订单：请先确认订单号，或确认是否已下单支付。"
        return logistics_service.format_tracking_text(info)

    return [query_order, query_tracking]
