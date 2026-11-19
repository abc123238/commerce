"""经营数据预聚合：按日期区间幂等重算日/品类汇总。

刷新策略是「删除区间 + 重新插入」而不是 upsert：
- 能清理「某天已没有数据但旧聚合行仍在」的脏行；
- 计算与实时指标层复用同一函数，口径天然一致；
- 事务级 advisory lock 防并发刷新互相覆盖。
"""
import time
from datetime import date

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.analytics_agg import DailyCategorySalesAgg, DailySalesAgg
from app.db.models.order import Order
from app.db.models.product import Category
from app.services import analytics_service

_LOCK_KEY = "daily_sales_agg_refresh"


async def _resolve_range(
    db: AsyncSession, start: date | None, end: date | None
) -> tuple[date, date]:
    if start is not None and end is not None:
        return start, end
    lo, hi = (
        await db.execute(
            select(
                func.min(func.date(Order.paid_at)),
                func.max(func.date(Order.paid_at)),
            ).where(Order.paid_at.is_not(None))
        )
    ).one()
    if lo is None:
        today = analytics_service.today_utc()
        return start or today, end or today
    return start or lo, end or hi


async def refresh_daily_agg(
    db: AsyncSession, start: date | None = None, end: date | None = None
) -> dict:
    started = time.perf_counter()
    start, end = await _resolve_range(db, start, end)
    if start > end:
        raise ValueError("start_date 不能晚于 end_date")

    # 事务级锁：同一事务提交/回滚时自动释放，无需 TTL 续期
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))").bindparams(key=_LOCK_KEY)
    )

    daily = await analytics_service.sales_by_day(db, start, end)
    categories = await analytics_service.daily_category_sales(db, start, end)

    await db.execute(
        delete(DailySalesAgg).where(
            DailySalesAgg.stat_date >= start, DailySalesAgg.stat_date <= end
        )
    )
    await db.execute(
        delete(DailyCategorySalesAgg).where(
            DailyCategorySalesAgg.stat_date >= start,
            DailyCategorySalesAgg.stat_date <= end,
        )
    )
    db.add_all(
        [
            DailySalesAgg(
                stat_date=row.day,
                revenue=row.revenue,
                order_count=row.orders,
                item_count=row.items,
            )
            for row in daily
        ]
    )
    db.add_all(
        [
            DailyCategorySalesAgg(
                stat_date=row.day,
                category_id=row.category_id,
                revenue=row.revenue,
                order_count=row.orders,
                item_count=row.items,
            )
            for row in categories
        ]
    )
    await db.commit()

    return {
        "start_date": start,
        "end_date": end,
        "days": len(daily),
        "category_rows": len(categories),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


async def read_daily_agg(
    db: AsyncSession, start: date, end: date
) -> list[DailySalesAgg]:
    return list(
        await db.scalars(
            select(DailySalesAgg)
            .where(DailySalesAgg.stat_date >= start, DailySalesAgg.stat_date <= end)
            .order_by(DailySalesAgg.stat_date)
        )
    )


async def read_category_agg(
    db: AsyncSession, start: date, end: date
) -> list[tuple[DailyCategorySalesAgg, str]]:
    rows = await db.execute(
        select(DailyCategorySalesAgg, Category.name)
        .join(Category, Category.id == DailyCategorySalesAgg.category_id)
        .where(
            DailyCategorySalesAgg.stat_date >= start,
            DailyCategorySalesAgg.stat_date <= end,
        )
        .order_by(DailyCategorySalesAgg.stat_date, DailyCategorySalesAgg.category_id)
    )
    return list(rows.all())
