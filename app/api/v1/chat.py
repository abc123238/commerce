"""AI 客服接口：SSE 流式返回 agent 运行过程与最终回答（需登录）。

事件流：
- step    ：agent 每步审计（route/retrieve/reason/execute_tool/answer）
- answer  ：最终回答 + 引用
- done    ：流结束，携带 HTTP trace_id 供全链路关联
- error   ：服务异常
"""
import json

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_current_user
from app.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.core.trace import get_trace_id
from app.db.models.user import User
from app.db.session import get_db
from app.observability.langfuse import make_langfuse_callbacks
from app.schemas.chat import ChatRequest

logger = get_logger("chat")

router = APIRouter(prefix="/chat", tags=["chat"])


def _sse(data: dict) -> dict:
    return {"event": "message", "data": json.dumps(data, ensure_ascii=False)}


@router.post("")
@limiter.limit(f"{settings.rate_limit_chat}/minute")
async def chat(
    request: Request,
    body: ChatRequest,
    db=Depends(get_db),
    user: User = Depends(get_current_user),
) -> EventSourceResponse:
    trace_id = get_trace_id()
    history = [turn.model_dump() for turn in body.history]
    logger.info(
        "chat_start",
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
            logger.error("chat_error", run_id=trace_id, user_id=user.id, exc=str(exc)[:300])
            yield _sse({"type": "error", "message": "服务处理失败，请稍后重试"})
        finally:
            # 客户端断开（GeneratorExit）或正常结束都会走到这里：
            # astream 生成器随之关闭，LangGraph 会取消仍在运行的 LLM 调用。
            logger.info("sse_closed", run_id=trace_id, user_id=user.id)

    return EventSourceResponse(
        event_generator(),
        ping=15,  # 心跳注释帧：避免 nginx/代理因长静默超时切断流
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 反 nginx/代理缓冲，保证流式即时
            "Connection": "keep-alive",
        },
    )
