"""结构化日志：生产输出 JSON 单行，调试输出可读文本。

trace_id 由中间件通过 structlog.contextvars 绑定，处理器自动并入每条日志。
"""
import logging
import sys

import structlog


def setup_logging(debug: bool = False) -> None:
    # Windows 下 stdout 默认按 GBK 编码，会破坏 JSON 日志中的中文，统一为 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    renderer = (
        structlog.dev.ConsoleRenderer()
        if debug
        else structlog.processors.JSONRenderer(ensure_ascii=False)
    )

    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if debug else logging.INFO
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 兜底：第三方库的 stdlib 日志也输出到 stdout，避免被吞
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)


def get_logger(name: str = "app") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)



