"""订单查询工具：读取真实订单表，仅限当前登录用户（权限内数据）。"""
from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.order import Order


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
            lines.append(
                f"订单 {order.order_no}：状态={order.status}，金额={order.total} 元，"
                f"商品=[{items}]"
            )
        return "\n".join(lines)

    return [query_order]
