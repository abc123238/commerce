# 多阶段构建：uv 解析依赖 -> 精简运行时（非 root + healthcheck）。
# 构建时预下载 embedding 模型，容器启动免首次联网下载。
# 注意：builder 用 python:3.12-slim + pip 安装 uv，避开 ghcr.io（国内拉取极慢）；
#       PyPI 镜像可用构建参数覆盖：--build-arg UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

# ---------- 阶段一：解析并同步依赖 ----------
FROM python:3.12-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PIP_NO_CACHE_DIR=1
ARG UV_DEFAULT_INDEX=https://pypi.org/simple
ENV UV_DEFAULT_INDEX=$UV_DEFAULT_INDEX
RUN pip install --no-cache-dir uv
WORKDIR /app

# 先只拷依赖清单，命中缓存层，避免源码变更导致全量重装
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 拷贝源码后安装项目本身
COPY . .
RUN uv sync --frozen --no-dev

# ---------- 阶段二：精简运行镜像 ----------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/app/.cache/huggingface \
    PATH="/app/.venv/bin:$PATH" \
    # Prometheus 多进程指标目录：固化在镜像级 ENV，确保 gunicorn 主/worker 进程都拿到
    prometheus_multiproc_dir=/tmp/prometheus_metrics

WORKDIR /app

COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/app ./app
COPY --from=builder /app/alembic ./alembic
COPY --from=builder /app/alembic.ini .
COPY --from=builder /app/data ./data
COPY --from=builder /app/scripts ./scripts
COPY --from=builder /app/eval ./eval
COPY --from=builder /app/examples ./examples
COPY --from=builder /app/pyproject.toml .
COPY --from=builder /app/gunicorn.conf.py .

# 预下载 embedding 模型（可跳过：--build-arg PRE_DOWNLOAD_EMBEDDING=false）
ARG PRE_DOWNLOAD_EMBEDDING=true
RUN if [ "$PRE_DOWNLOAD_EMBEDDING" = "true" ]; then \
      python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='jinaai/jina-embeddings-v2-base-zh')"; \
    fi

# 非 root 运行：.venv 保持 root 只读，只 chown 运行期可写目录（模型缓存 / 源码 / 数据）
RUN useradd --create-home appuser \
    && mkdir -p /app/.cache \
    && chown -R appuser:appuser /app/.cache /app/app /app/scripts /app/eval /app/data /app/examples /app/alembic
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

# 生产：gunicorn + uvicorn worker（配置见 gunicorn.conf.py：4 worker + Prometheus 多进程）
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.main:app"]
