"""经营分析接口：SSE 流式返回分析 agent 的步骤与最终报告（需登录）。

复用 LangGraph 图与 build_agent，但强制 intent=analytics：
跳过 RAG 检索，直接走 查销售数据 → 计算增长 → 出报告 的多步工具链路。
"""
import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.api.deps import require_roles
from app.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.core.trace import get_trace_id
from app.db.models.user import User
from app.db.session import get_db
from app.observability.langfuse import make_langfuse_callbacks
from app.schemas.analytics import (
    AggRefreshIn,
    AggRefreshOut,
    AggregatesOut,
    CategoryAggItem,
    DailyAggItem,
    FunnelOut,
    FunnelStepOut,
    GroupBy,
    SalesOut,
    SalesPoint,
    TopProductItem,
    TopProductsOut,
)
from app.schemas.chat import ChatRequest
from app.services import analytics_agg_service, analytics_service

logger = get_logger("analytics")

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _resolve_range(
    start_date: date | None, end_date: date | None
) -> tuple[date, date]:
    start, end = analytics_service.default_range()
    if start_date is not None:
        start = start_date
    if end_date is not None:
        end = end_date
    if start > end:
        raise HTTPException(400, "start_date 不能晚于 end_date")
    return start, end


def _sse(data: dict) -> dict:
    return {"event": "message", "data": json.dumps(data, ensure_ascii=False)}


@router.post("")
@limiter.limit(f"{settings.rate_limit_chat}/minute")
async def analytics(
    request: Request,
    body: ChatRequest,
    db=Depends(get_db),
    user: User = Depends(require_roles("admin")),
) -> EventSourceResponse:
    trace_id = get_trace_id()
    history = [turn.model_dump() for turn in body.history]
    logger.info(
        "analytics_start",
        run_id=trace_id,
        user_id=user.id,
        question=body.question[:80],
        history_len=len(history),
    )

    from app.agent.graph import build_agent

    graph, initial, config = build_agent(
        db,
        user.id,
        body.question,
        history,
        run_id=trace_id,
        callbacks=make_langfuse_callbacks(trace_id, user.id) or [],
        intent="analytics",
        user_role=user.role,
    )

    async def event_generator():
        try:
            async for update in graph.astream(initial, stream_mode="updates", config=config):
                for node, payload in update.items():
                    for step in payload.get("steps", []):
                        yield _sse(
                            {"type": "step", "node": step.get("node"), "detail": step.get("detail")}
                        )
                    if node == "answer":
                        yield _sse(
                            {
                                "type": "answer",
                                "content": payload.get("answer", ""),
                                "citations": payload.get("citations", []),
                            }
                        )
            yield _sse({"type": "done", "trace_id": trace_id})
        except Exception as exc:  # noqa: BLE001
            logger.error("analytics_error", run_id=trace_id, user_id=user.id, exc=str(exc)[:300])
            yield _sse({"type": "error", "message": "服务处理失败，请稍后重试"})
        finally:
            # 客户端断开（GeneratorExit）或正常结束都会走到这里，astream 随之关闭并取消 LLM 调用
            logger.info("sse_closed", run_id=trace_id, user_id=user.id)

    return EventSourceResponse(
        event_generator(),
        ping=15,  # 心跳注释帧：避免 nginx/代理因长静默超时切断流
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/sales", response_model=SalesOut)
async def sales(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    group_by: GroupBy = Query(default="day"),
    db=Depends(get_db),
    _user: User = Depends(require_roles("admin")),
) -> SalesOut:
    """结构化销售额/订单数：与 Agent 经营分析工具共用同一指标口径。"""
    start, end = _resolve_range(start_date, end_date)
    if group_by == "category":
        rows = await analytics_service.sales_by_category(db, start, end)
        items = [
            SalesPoint(
                category=row.name,
                revenue=row.revenue,
                orders=row.orders,
                items=row.items,
            )
            for row in rows
        ]
    else:
        rows = await analytics_service.sales_by_day(db, start, end)
        items = [
            SalesPoint(
                date=row.day,
                revenue=row.revenue,
                orders=row.orders,
                items=row.items,
            )
            for row in rows
        ]
    return SalesOut(start_date=start, end_date=end, group_by=group_by, items=items)


@router.get("/top-products", response_model=TopProductsOut)
async def top_products(
    limit: int = Query(default=5, ge=1, le=20),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db=Depends(get_db),
    _user: User = Depends(require_roles("admin")),
) -> TopProductsOut:
    rows = await analytics_service.top_products(
        db, limit=limit, start=start_date, end=end_date
    )
    items = [
        TopProductItem(name=row.name, revenue=row.revenue, quantity=row.quantity)
        for row in rows
    ]
    return TopProductsOut(items=items, total=len(items))


@router.get("/funnel", response_model=FunnelOut)
async def funnel(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db=Depends(get_db),
    _user: User = Depends(require_roles("admin")),
) -> FunnelOut:
    """订单状态漏斗：以 created_at 落入区间的订单为 cohort。"""
    start, end = _resolve_range(start_date, end_date)
    result = await analytics_service.funnel(db, start, end)
    return FunnelOut(
        cohort=result.cohort,
        cancelled=result.cancelled,
        steps=[
            FunnelStepOut(
                status=step.status,
                count=step.count,
                rate=step.rate,
                avg_dwell_seconds=step.avg_dwell_seconds,
            )
            for step in result.steps
        ],
    )


@router.post("/aggregates/refresh", response_model=AggRefreshOut)
async def refresh_aggregates(
    body: AggRefreshIn | None = None,
    db=Depends(get_db),
    _user: User = Depends(require_roles("admin")),
) -> AggRefreshOut:
    """按区间幂等重算预聚合表；缺省时覆盖全部已支付订单的日期范围。"""
    body = body or AggRefreshIn()
    try:
        result = await analytics_agg_service.refresh_daily_agg(
            db, start=body.start_date, end=body.end_date
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return AggRefreshOut(**result)


@router.get("/aggregates", response_model=AggregatesOut)
async def read_aggregates(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db=Depends(get_db),
    _user: User = Depends(require_roles("admin")),
) -> AggregatesOut:
    start, end = _resolve_range(start_date, end_date)
    daily = await analytics_agg_service.read_daily_agg(db, start, end)
    categories = await analytics_agg_service.read_category_agg(db, start, end)
    return AggregatesOut(
        start_date=start,
        end_date=end,
        daily=[
            DailyAggItem(
                stat_date=row.stat_date,
                revenue=row.revenue,
                order_count=row.order_count,
                item_count=row.item_count,
            )
            for row in daily
        ],
        categories=[
            CategoryAggItem(
                stat_date=agg.stat_date,
                category_id=agg.category_id,
                category=name,
                revenue=agg.revenue,
                order_count=agg.order_count,
                item_count=agg.item_count,
            )
            for agg, name in categories
        ],
    )
