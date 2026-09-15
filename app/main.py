"""FastAPI 应用入口：应用工厂 + 中间件 + 路由注册 + 前端静态托管。"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.router import api_router
from app.config import settings
from app.core.logging import setup_logging
from app.core.rate_limit import limiter
from app.observability.metrics import RATE_LIMITED, render_metrics
from app.observability.middleware import TraceMiddleware

# 前端构建产物目录（frontend 执行 vite build 后生成）。不存在时跳过挂载，纯 API 模式不受影响。
STATIC_DIR = Path(__file__).resolve().parent / "static"

setup_logging(debug=settings.debug)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description="生产级电商平台 + AI 增强（客服 / 经营分析智能体）",
        version="0.2.0",
        debug=settings.debug,
    )

    # 可观测：trace_id 全链路贯穿
    app.add_middleware(TraceMiddleware)

    # 限流：429 + Retry-After（计数器打点后交给 slowapi 默认处理）
    app.state.limiter = limiter

    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        RATE_LIMITED.inc()
        return _rate_limit_exceeded_handler(request, exc)

    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        # 不返回 env 等内部信息，避免无鉴权端点泄露部署上下文
        return {"status": "ok"}

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    async def metrics() -> Response:
        payload, content_type = render_metrics()
        return Response(content=payload, media_type=content_type)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    # 静态托管放最后注册：/api、/health、/metrics 等已匹配的路径不受影响，
    # 其余路径交给前端（SPA 用 hash 路由，无刷新丢失问题）。
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()
