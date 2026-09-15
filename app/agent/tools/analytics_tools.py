"""经营分析工具：读真实订单数据做聚合、环比计算与畅销榜。

只统计已支付订单（Order.status == paid），金额为 单价×数量 汇总。
与客服工具同款闭包注入 db，确保在请求会话内查询。
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from langchain_core.tools import tool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.order import Order, OrderItem, OrderStatus
from app.db.models.product import Category, Product

_REVENUE = OrderItem.unit_price * OrderItem.quantity


def _money(v) -> str:
    return f"¥{Decimal(v):.2f}"


def _parse_day(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"日期格式应为 YYYY-MM-DD，收到：{s}")


def build_analytics_tools(db: AsyncSession) -> list:
    @tool
    async def current_date() -> str:
        """返回服务器当前日期（YYYY-MM-DD），用于确定"今天/最近"的查询区间。"""
        return datetime.now(timezone.utc).date().isoformat()

    @tool
    async def query_sales_summary(
        start_date: str | None = None, end_date: str | None = None, group_by: str = "day"
    ) -> str:
        """按日或按品类汇总已支付订单的销售额与订单数。

        参数：start_date / end_date 为 YYYY-MM-DD（含边界，闭区间）；
        缺省时默认取最近 7 天（含今天）；group_by 取值 day（默认，按天）或 category（按品类）。
        """
        today = datetime.now(timezone.utc).date()
        start = _parse_day(start_date) if start_date else today - timedelta(days=6)
        end = _parse_day(end_date) if end_date else today

        def _base(stmt):
            return (
                stmt.select_from(OrderItem)
                .join(Order, Order.id == OrderItem.order_id)
                .where(
                    Order.status == OrderStatus.PAID,
                    func.date(Order.paid_at) >= start,
                    func.date(Order.paid_at) <= end,
                )
            )

        if group_by == "category":
            rows = (
                await db.execute(
                    _base(
                        select(
                            Category.name,
                            func.sum(_REVENUE),
                            func.count(func.distinct(Order.id)),
                        )
                    )
                    .join(Product, Product.id == OrderItem.product_id)
                    .join(Category, Category.id == Product.category_id)
                    .group_by(Category.name)
                )
            ).all()
            if not rows:
                return f"{start} ~ {end} 区间无已支付订单数据"
            return "\n".join(
                f"{name}：销售额 {_money(s)}，{cnt} 单" for name, s, cnt in rows
            )

        rows = (
            await db.execute(
                _base(
                    select(
                        func.date(Order.paid_at),
                        func.sum(_REVENUE),
                        func.count(func.distinct(Order.id)),
                    )
                )
                .group_by(func.date(Order.paid_at))
                .order_by(func.date(Order.paid_at))
            )
        ).all()
        if not rows:
            return f"{start} ~ {end} 区间无已支付订单数据"
        return "\n".join(f"{d}：销售额 {_money(s)}，{cnt} 单" for d, s, cnt in rows)

    @tool
    async def calc_growth(base_value: str, current_value: str) -> str:
        """计算两个数值的增长率（如环比/同比）：(当前-基准)÷基准×100%。

        参数为纯数字字符串（可带小数，如 "1299.50"）。
        """
        try:
            base, current = Decimal(base_value), Decimal(current_value)
        except Exception:
            return "数值解析失败，请传入数字字符串"
        if base == 0:
            return "基准值为 0，无法计算增长率"
        rate = (current - base) / base * 100
        return f"增长率 {rate:+.1f}%（基准 {_money(base)} → 当前 {_money(current)}）"

    @tool
    async def top_products(limit: int = 5) -> str:
        """按销售额返回畅销商品 Top N（仅统计已支付订单）。"""
        rows = (
            await db.execute(
                select(Product.name, func.sum(_REVENUE))
                .join(OrderItem, OrderItem.product_id == Product.id)
                .join(Order, Order.id == OrderItem.order_id)
                .where(Order.status == OrderStatus.PAID)
                .group_by(Product.name)
                .order_by(func.sum(_REVENUE).desc())
                .limit(max(1, min(limit, 20)))
            )
        ).all()
        if not rows:
            return "暂无已支付订单数据"
        lines = [f"{i + 1}. {name}：{_money(s)}" for i, (name, s) in enumerate(rows)]
        return "畅销榜（按销售额）：\n" + "\n".join(lines)

    return [current_date, query_sales_summary, calc_growth, top_products]
