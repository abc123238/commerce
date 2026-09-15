"""经营分析接口：SSE 流式返回分析 agent 的步骤与最终报告（需登录）。

复用 LangGraph 图与 build_agent，但强制 intent=analytics：
跳过 RAG 检索，直接走 查销售数据 → 计算增长 → 出报告 的多步工具链路。
"""
import json

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.api.deps import require_roles
from app.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.core.trace import get_trace_id
from app.db.models.user import User
from app.db.session import get_db
from app.observability.langfuse import make_langfuse_callbacks
from app.schemas.chat import ChatRequest

logger = get_logger("analytics")

router = APIRouter(prefix="/analytics", tags=["analytics"])


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
