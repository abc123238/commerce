"""gunicorn 生产配置：多 worker + Prometheus 多进程计数。

主进程先设置 PROMETHEUS_MULTIPROC_DIR（小写 prometheus_multiproc_dir），
worker 内 prometheus_client 据此把指标写入独立文件；/metrics 用
MultiProcessCollector 合并各 worker 计数。child_exit 清理死亡 worker 文件。
"""
import os

import prometheus_client.multiprocess

# 必须在使用 gunicorn 的场景用绝对/固定目录，且对每个 worker 可写
_multiproc_dir = os.environ.get("prometheus_multiproc_dir", "/tmp/prometheus_metrics")
os.environ["prometheus_multiproc_dir"] = _multiproc_dir
os.makedirs(_multiproc_dir, exist_ok=True)

# 4 worker，Uvicorn 异步
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
bind = "0.0.0.0:8000"
timeout = 60
graceful_timeout = 30


def child_exit(server, worker):
    """worker 退出/重启时标记其指标文件死亡，避免 /metrics 报陈旧数据。"""
    prometheus_client.multiprocess.mark_process_dead(worker.pid)
