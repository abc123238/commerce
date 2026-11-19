"""手动或外部 cron 触发经营数据预聚合刷新。

用法：
    python -m scripts.refresh_analytics_agg
    python -m scripts.refresh_analytics_agg --start-date 2026-01-01 --end-date 2026-01-31
"""
import argparse
import asyncio
import json
from datetime import date

from app.db.session import SessionLocal
from app.services import analytics_agg_service


async def amain(start: date | None, end: date | None) -> None:
    async with SessionLocal() as db:
        result = await analytics_agg_service.refresh_daily_agg(db, start, end)
    print(json.dumps(result, ensure_ascii=False, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="刷新经营数据预聚合表")
    parser.add_argument("--start-date", type=date.fromisoformat, default=None)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None)
    args = parser.parse_args()
    asyncio.run(amain(args.start_date, args.end_date))


if __name__ == "__main__":
    main()
