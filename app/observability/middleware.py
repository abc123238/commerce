"""HTTP 可观测中间件：trace_id 全链路贯穿 + 请求日志。

流程：解析/生成 X-Trace-ID -> 写入 ContextVar 与 structlog 上下文 ->
响应头回传 X-Trace-ID，便于 curl 观测全链路。
"""
import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import get_logger
from app.core.trace import new_trace_id, set_trace_id
from app.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS

logger = get_logger("http")


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-ID") or new_trace_id()
        set_trace_id(trace_id)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.error("request_error", method=request.method, path=request.url.path)
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Trace-ID"] = trace_id
        HTTP_REQUESTS.labels(
            method=request.method, path=request.url.path, status=response.status_code
        ).inc()
        HTTP_LATENCY.labels(
            method=request.method, path=request.url.path
        ).observe(duration_ms / 1000)
        logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        return response
