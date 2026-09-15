"""Prometheus 指标：HTTP / agent / 检索 / 工具 / 限流。

指标在模块导入时注册到默认 Registry，由 app.main 挂载 /metrics 端点，
供 prometheus 抓取。带 label 的计数器用 labels 字段区分维度。

多进程模式：gunicorn 多 worker 下由 gunicorn.conf.py 预设
prometheus_multiproc_dir，/metrics 用 MultiProcessCollector 合并各 worker。
单进程（uvicorn 开发 / pytest）走默认注册表，行为不变。
"""
import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

_multiproc_dir = os.environ.get("prometheus_multiproc_dir")
if _multiproc_dir:
    from prometheus_client import multiprocess

    _registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(_registry)
else:
    _registry = None

# HTTP：请求计数与耗时分布（按方法/路径/状态码）
HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP 请求总数", ["method", "path", "status"]
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP 请求耗时（秒）", ["method", "path"]
)

# Agent：运行次数（按意图）
AGENT_RUNS = Counter("agent_runs_total", "Agent 运行次数", ["intent"])

# 检索与工具
RETRIEVALS = Counter("retrievals_total", "混合检索次数")
TOOL_CALLS = Counter("tool_calls_total", "工具调用次数", ["tool"])

# 限流
RATE_LIMITED = Counter("rate_limited_total", "被限流拒绝的请求数")


def render_metrics() -> tuple[bytes, str]:
    """返回 (payload, content_type)，供 /metrics 端点直接响应。"""
    if _registry is not None:
        return generate_latest(_registry), CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST
