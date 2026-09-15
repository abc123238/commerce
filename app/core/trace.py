"""trace_id 上下文传播：贯穿 HTTP 请求 -> agent run -> 每步 span 的追踪标识。

通过 ContextVar 在同一请求的异步上下文内共享，日志与可观测中间件据此串联全链路。
"""
import contextvars
import uuid

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def new_trace_id() -> str:
    return uuid.uuid4().hex


def set_trace_id(trace_id: str) -> None:
    _trace_id.set(trace_id)


def get_trace_id() -> str:
    return _trace_id.get()
