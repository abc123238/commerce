"""Langfuse 回调工厂（可插拔）：不可用时不阻塞主链路。

Langfuse v4 回调通过 trace_context 将 trace_id 关联到我们的 HTTP trace_id，
实现「HTTP -> agent run -> 每步 span」的全链路追踪。
"""
from app.config import settings


def make_langfuse_callbacks(run_id: str, user_id: int | None = None) -> list | None:
    if not settings.langfuse_enabled:
        return None
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    from langfuse.langchain import CallbackHandler

    handler = CallbackHandler(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        trace_context={"trace_id": run_id},
    )
    # v4 的 session/user 需在 Langfuse 控制台侧通过 trace_id 关联；
    # 此处已在 run_id 之外把 user_id 作为日志字段输出（见 chat 接口日志）。
    return [handler]
