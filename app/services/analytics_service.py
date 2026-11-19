"""统一经营指标层：唯一营收口径，供 Agent 工具与结构化 API 共用。

口径：只统计产生过支付的订单（paid_at 非空），而不是 status == paid——
订单状态会随模拟物流推进到 shipped/completed，用状态过滤会静默漏单。
金额为订单项快照的 unit_price × quantity 汇总。
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.order import Order, OrderItem, OrderStatus
from app.db.models.order_event import OrderStatusEvent
from app.db.models.product import Category, Product

PAID_FILTER = Order.paid_at.is_not(None)
REVENUE_EXPR = OrderItem.unit_price * OrderItem.quantity
_ITEM_COUNT = func.coalesce(func.sum(OrderItem.quantity), 0)
_ORDER_COUNT = func.count(func.distinct(Order.id))


@dataclass(frozen=True)
class SalesDay:
    day: date
    revenue: Decimal
    orders: int
    items: int


@dataclass(frozen=True)
class CategorySales:
    name: str
    revenue: Decimal
    orders: int
    items: int


@dataclass(frozen=True)
class DailyCategorySales:
    day: date
    category_id: int
    name: str
    revenue: Decimal
    orders: int
    items: int


@dataclass(frozen=True)
class TopProduct:
    name: str
    revenue: Decimal
    quantity: int


@dataclass(frozen=True)
class FunnelStep:
    status: str
    count: int
    rate: float
    avg_dwell_seconds: float | None = None


@dataclass(frozen=True)
class FunnelResult:
    cohort: int
    steps: list[FunnelStep]
    cancelled: int


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def default_range(days: int = 7) -> tuple[date, date]:
    today = today_utc()
    return today - timedelta(days=days - 1), today


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"日期格式应为 YYYY-MM-DD，收到：{value}")


async def sales_by_day(
    db: AsyncSession, start: date, end: date
) -> list[SalesDay]:
    rows = (
        await db.execute(
            select(
                func.date(Order.paid_at),
                func.sum(REVENUE_EXPR),
                _ORDER_COUNT,
                _ITEM_COUNT,
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                PAID_FILTER,
                func.date(Order.paid_at) >= start,
                func.date(Order.paid_at) <= end,
            )
            .group_by(func.date(Order.paid_at))
            .order_by(func.date(Order.paid_at))
        )
    ).all()
    return [
        SalesDay(day=d, revenue=revenue, orders=orders, items=items)
        for d, revenue, orders, items in rows
    ]


async def sales_by_category(
    db: AsyncSession, start: date, end: date
) -> list[CategorySales]:
    rows = (
        await db.execute(
            select(
                Category.name,
                func.sum(REVENUE_EXPR),
                _ORDER_COUNT,
                _ITEM_COUNT,
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .join(Category, Category.id == Product.category_id)
            .where(
                PAID_FILTER,
                func.date(Order.paid_at) >= start,
                func.date(Order.paid_at) <= end,
            )
            .group_by(Category.name)
            .order_by(Category.name)
        )
    ).all()
    return [
        CategorySales(name=name, revenue=revenue, orders=orders, items=items)
        for name, revenue, orders, items in rows
    ]


async def daily_category_sales(
    db: AsyncSession, start: date, end: date
) -> list[DailyCategorySales]:
    """按「天 × 品类」聚合，供 daily_category_sales_agg 预聚合表使用。"""
    rows = (
        await db.execute(
            select(
                func.date(Order.paid_at),
                Category.id,
                Category.name,
                func.sum(REVENUE_EXPR),
                _ORDER_COUNT,
                _ITEM_COUNT,
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .join(Category, Category.id == Product.category_id)
            .where(
                PAID_FILTER,
                func.date(Order.paid_at) >= start,
                func.date(Order.paid_at) <= end,
            )
            .group_by(func.date(Order.paid_at), Category.id, Category.name)
            .order_by(func.date(Order.paid_at), Category.id)
        )
    ).all()
    return [
        DailyCategorySales(
            day=day,
            category_id=category_id,
            name=name,
            revenue=revenue,
            orders=orders,
            items=items,
        )
        for day, category_id, name, revenue, orders, items in rows
    ]


async def top_products(
    db: AsyncSession, limit: int = 5, start: date | None = None, end: date | None = None
) -> list[TopProduct]:
    stmt = (
        select(
            Product.name,
            func.sum(REVENUE_EXPR),
            _ITEM_COUNT,
        )
        .select_from(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .join(Product, Product.id == OrderItem.product_id)
        .where(PAID_FILTER)
    )
    if start is not None:
        stmt = stmt.where(func.date(Order.paid_at) >= start)
    if end is not None:
        stmt = stmt.where(func.date(Order.paid_at) <= end)
    rows = (
        await db.execute(
            stmt.group_by(Product.name)
            .order_by(func.sum(REVENUE_EXPR).desc())
            .limit(max(1, min(limit, 20)))
        )
    ).all()
    return [
        TopProduct(name=name, revenue=revenue, quantity=quantity)
        for name, revenue, quantity in rows
    ]


async def revenue_and_orders(
    db: AsyncSession, start: date, end: date
) -> tuple[Decimal, int]:
    revenue, orders = (
        await db.execute(
            select(func.sum(REVENUE_EXPR), _ORDER_COUNT)
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                PAID_FILTER,
                func.date(Order.paid_at) >= start,
                func.date(Order.paid_at) <= end,
            )
        )
    ).one()
    return revenue or Decimal("0"), orders or 0


def growth_rate(base: Decimal, current: Decimal) -> Decimal | None:
    """(当前-基准)÷基准×100%；基准为 0 时无法计算，返回 None。"""
    if base == 0:
        return None
    return (current - base) / base * 100


async def funnel(db: AsyncSession, start: date, end: date) -> FunnelResult:
    """以 created_at 落在区间的订单为 cohort，统计状态到达率与停留时长。

    停留时长取相邻事件 occurred_at 之差；事件由 order_status_events 提供，
    存量数据由迁移回填，物流推进写入业务发生时间而非查询时间。
    """
    cohort_filter = (
        func.date(Order.created_at) >= start,
        func.date(Order.created_at) <= end,
    )
    cohort = (
        await db.scalar(select(func.count()).select_from(Order).where(*cohort_filter))
    ) or 0

    rows = (
        await db.execute(
            select(
                OrderStatusEvent.order_id,
                OrderStatusEvent.to_status,
                OrderStatusEvent.occurred_at,
            )
            .join(Order, Order.id == OrderStatusEvent.order_id)
            .where(*cohort_filter)
        )
    ).all()

    by_order: dict[int, dict[str, datetime]] = {}
    for order_id, to_status, occurred_at in rows:
        by_order.setdefault(order_id, {})[to_status] = occurred_at

    linear = [
        OrderStatus.CREATED,
        OrderStatus.PAID,
        OrderStatus.SHIPPED,
        OrderStatus.COMPLETED,
    ]
    counts = {status: 0 for status in linear}
    dwell: dict[str, list[float]] = {status: [] for status in linear[1:]}
    previous = {linear[i]: linear[i - 1] for i in range(1, len(linear))}

    for events in by_order.values():
        for status in linear:
            if status in events:
                counts[status] += 1
                prev = previous.get(status)
                if prev and prev in events:
                    delta = (events[status] - events[prev]).total_seconds()
                    if delta >= 0:
                        dwell[status].append(delta)

    # created 是 cohort 的定义本身，不依赖事件行是否存在（存量数据兜底）
    counts[OrderStatus.CREATED] = cohort

    steps = [
        FunnelStep(
            status=status,
            count=counts[status],
            rate=round(counts[status] / cohort, 4) if cohort else 0.0,
            avg_dwell_seconds=(
                round(sum(dwell[status]) / len(dwell[status]), 1)
                if dwell.get(status)
                else None
            ),
        )
        for status in linear
    ]
    cancelled = sum(
        1 for events in by_order.values() if OrderStatus.CANCELLED in events
    )
    return FunnelResult(cohort=cohort, steps=steps, cancelled=cancelled)
