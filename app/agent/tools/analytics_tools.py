"""经营分析工具：读真实订单数据做聚合、环比计算与畅销榜。

业务口径与聚合 SQL 统一收敛到 app/services/analytics_service.py，
工具只负责参数解析与中文文本格式化，避免 API 与 Agent 各有一套口径。
"""
from datetime import datetime, timezone
from decimal import Decimal

from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import analytics_service


def _money(v) -> str:
    return f"¥{Decimal(v):.2f}"


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
        start, end = analytics_service.default_range()
        if start_date:
            start = analytics_service.parse_day(start_date)
        if end_date:
            end = analytics_service.parse_day(end_date)

        if group_by == "category":
            rows = await analytics_service.sales_by_category(db, start, end)
            if not rows:
                return f"{start} ~ {end} 区间无已支付订单数据"
            return "\n".join(
                f"{row.name}：销售额 {_money(row.revenue)}，{row.orders} 单"
                for row in rows
            )

        rows = await analytics_service.sales_by_day(db, start, end)
        if not rows:
            return f"{start} ~ {end} 区间无已支付订单数据"
        return "\n".join(
            f"{row.day}：销售额 {_money(row.revenue)}，{row.orders} 单" for row in rows
        )

    @tool
    async def calc_growth(base_value: str, current_value: str) -> str:
        """计算两个数值的增长率（如环比/同比）：(当前-基准)÷基准×100%。

        参数为纯数字字符串（可带小数，如 "1299.50"）。
        """
        try:
            base, current = Decimal(base_value), Decimal(current_value)
        except Exception:
            return "数值解析失败，请传入数字字符串"
        rate = analytics_service.growth_rate(base, current)
        if rate is None:
            return "基准值为 0，无法计算增长率"
        return f"增长率 {rate:+.1f}%（基准 {_money(base)} → 当前 {_money(current)}）"

    @tool
    async def top_products(limit: int = 5) -> str:
        """按销售额返回畅销商品 Top N（仅统计已支付订单）。"""
        rows = await analytics_service.top_products(db, limit=limit)
        if not rows:
            return "暂无已支付订单数据"
        lines = [
            f"{i + 1}. {row.name}：{_money(row.revenue)}"
            for i, row in enumerate(rows)
        ]
        return "畅销榜（按销售额）：\n" + "\n".join(lines)

    return [current_date, query_sales_summary, calc_growth, top_products]
