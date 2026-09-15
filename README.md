# AI 电商平台（ai-commerce-platform）

一个以**完整业务系统为主体、AI 智能体为核心功能模块**的生产级全栈项目：电商业务闭环（鉴权 / 商品 / 购物车 / 订单 / 模拟支付）之上，叠加两个 AI 增强能力——**AI 客服**（RAG + 工具调用打穿真实业务库）与**经营分析智能体**（多步工具调用出报表），并配齐 Docker 部署、Langfuse 可观测链路、Prometheus 监控、RAG 评测与限流安全。

> 面向「AI 应用开发」岗位（实习 + 校招）：一套代码同时证明**工程基本功**（后端全链路）与**用 AI 解决真实问题**的能力。

---

## 一句话亮点

- **AI 工具读真实业务库**：客服回答"我的订单到哪了"时，通过工具实时查询登录用户的真实订单，而非编造或只查静态知识库。
- **全链路可观测**：HTTP `X-Trace-ID` → LangGraph 每步 span → Langfuse trace，一次请求端到端可追溯。
- **生产级而非 demo**：JWT + RBAC、订单状态机、限流、SSE 流式、Docker 多阶段构建、评测回写。

---

## 架构图

```
┌────────────┐   JWT/RBAC    ┌─────────────────────────── FastAPI ───────────────────────────┐
│  浏览器/   │ ─────────────▶ │ TraceMiddleware(生成 X-Trace-ID → ContextVar + structlog)     │
│  curl/SSE  │                │   ├─ /api/v1/auth|products|cart|orders   （业务闭环）        │
│            │                │   ├─ /api/v1/chat      ──▶ LangGraph 客服 Agent（SSE 流式）   │
│            │                │   ├─ /api/v1/analytics ──▶ LangGraph 经营分析 Agent（SSE）    │
│            │                │   └─ /metrics（Prometheus）/health                           │
└────────────┘                └───────┬──────────────┬──────────────┬───────────────┬────────┘
                                      │              │              │               │
                              ┌───────▼────┐  ┌──────▼─────┐  ┌─────▼─────┐  ┌─────▼────────┐
                              │ PostgreSQL │  │   Redis    │  │  Qdrant   │  │ Langfuse(可插│
                              │ 业务/订单库 │  │ 限流/缓存  │  │ 向量库(密+│  │ 拨，失败降级)│
                              └────────────┘  └────────────┘  │ 稀混合检索)│  └──────────────┘
                                                              └───────────┘
Agent 内部（LangGraph StateGraph）：
START → route(意图) → retrieve(RAG 混合检索) → reason(LLM 决策) ⇄ execute_tool(读业务库) → answer(引用校验) → END
```

**AI Agent 数据流**（客服示例）：

```
用户: "我的订单到哪里了？"
  → route       意图识别 = customer_service
  → retrieve    Qdrant 混合检索商品知识库（召回 top-k 块）
  → reason      LLM 决策：需要工具 query_order(order_no) 查实时状态
  → execute_tool 读取当前登录用户真实订单（权限：只能查自己）
  → reason      汇总检索资料 + 工具结果，组织带引用回答
  → answer      引用校验 [n] → END
```

---

## 技术栈

| 域 | 选型 |
|---|---|
| API | FastAPI + uvicorn / gunicorn（`UvicornWorker`） |
| ORM / 迁移 | SQLAlchemy 2（async）+ Alembic |
| 数据库 | PostgreSQL 16（asyncpg） |
| 缓存 / 限流 | Redis + slowapi（按用户/接口，429 + Retry-After） |
| AI 编排 | LangGraph（StateGraph）+ langchain-core |
| LLM | langchain-anthropic `ChatAnthropic`（自定义 base_url / auth_token） |
| RAG | Qdrant 命名向量（dense + jieba BM25 稀疏）原生 FusionQuery(RRF) 混合检索 |
| Embedding | fastembed（jina-embeddings-v2-base-zh，本地推理） |
| 中文分词 | jieba（中文 BM25 必做） |
| 可观测 | Langfuse（可插拔）+ structlog JSON 日志 + Prometheus / Grafana |
| 评测 | 自研评测套件（检索 / 引用 / 任务 / LLM-as-judge） |
| MCP | FastMCP demo（商品查询能力标准工具化） |
| 前端 | Vue 3 + Vite + Element Plus（SSE 流式聊天，vite build 产物由 FastAPI 静态托管） |
| 部署 | Docker Compose（profile 拆分：核心 / observability / monitoring） |

---

## 目录结构

```
ai-commerce-platform/
├── app/
│   ├── main.py / config.py
│   ├── api/v1/            # auth / products / cart / orders / chat / analytics
│   ├── core/              # security(JWT+RBAC) / logging / trace / rate_limit
│   ├── db/models/         # user / product / cart / order / payment
│   ├── agent/             # graph / nodes / tools / prompts / intent / memory
│   ├── rag/               # embeddings / vectorstore / hybrid / ingest / citation
│   ├── observability/     # langfuse / middleware / metrics / events
│   └── schemas/
├── eval/                  # runner / judge / report + 三数据集 + 指标
├── scripts/               # init_db / seed / ingest_kb / smoke / verify_flow
├── data/knowledge_base/   # 客服 RAG 语料
├── docs/                  # architecture / observability / eval / interview_prep
├── examples/              # mcp_demo_server.py（MCP 演示）
├── monitoring/            # prometheus.yml + grafana provisioning
├── frontend/              # Vue 3 前端（vite build 产物 → app/static）
├── Dockerfile / docker-compose.yml / .env.example
```

---

## 从零到 Demo

### 方式一：Docker 全栈（推荐，一条命令起核心栈）

```bash
# 0. 准备环境变量
cp .env.example .env        # 填 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / JWT_SECRET_KEY

# 0.5 构建前端（一次性；产物输出到 app/static，构建镜像时带入）
cd frontend && npm install && npm run build && cd ..

# 1. 构建镜像并启动核心栈（api + postgres + qdrant + redis）
docker compose up -d --build

# 2. 初始化（一次性）：建表 → 种子数据 → 知识库入库
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed
docker compose exec api python -m scripts.ingest_kb

# 3. 冒烟验证
docker compose exec api python -m scripts.smoke

# 4.（可选）追加可观测 / 监控栈
docker compose --profile observability up -d
docker compose --profile monitoring up -d
```

- Web 前端：http://127.0.0.1:8000（Vue 3 界面，由 FastAPI 静态托管）
- API 文档：http://127.0.0.1:8000/docs
- Langfuse（可观测）：http://127.0.0.1:3000
- Prometheus：http://127.0.0.1:9090 · Grafana：http://127.0.0.1:3001（admin/admin）

> 所有基础设施端口仅绑定 `127.0.0.1`，唯一对外入口 `api:8000`。内存有限时只起核心栈，AI 链路自动降级为「structlog 日志 + Prometheus 指标」，不阻塞主流程。

### 方式二：本地开发

```bash
uv sync                 # 安装依赖（含 dev 组）
docker compose up -d postgres qdrant redis   # 只起基础设施
uv run alembic upgrade head
uv run python -m scripts.seed
uv run python -m scripts.ingest_kb
uv run uvicorn app.main:app --reload --port 8000

# 另开终端：前端开发服务器（/api 自动代理到 8000）
cd frontend && npm install && npm run dev    # http://127.0.0.1:5173
```

### Docker 部署排障速查

| 现象 | 原因 / 解决 |
|---|---|
| 重建镜像后代码还是旧的（如 `/metrics` 404） | Docker BuildKit 对 `COPY . .` 有已知缓存 bug，用 `docker compose build --no-cache api` 强制全量重建 |
| 首次 `ingest_kb` 时 embedding 下载报 Xet CAS 401 | `.env` 需设 `HF_HUB_DISABLE_XET=1`（huggingface_hub≥1.x 的变量名；旧名 `HF_DISABLE_XET` 已失效）。镜像默认跳过构建期下载，首次入库时落到持久卷 `hf_cache` |
| Qdrant 显示 unhealthy | qdrant 镜像无 wget/curl，compose 已用 `bash /dev/tcp` 端口探测替代，无需手动操作 |
| 业务闭环验证 | `docker compose exec api python -m scripts.smoke`；或宿主机跑 `python scripts/verify_flow.py`（注册→登录→加购→下单→支付→查单，逐步骤带 X-Trace-ID 打印） |

---

## 业务闭环 API 一览（前缀 `/api/v1`）

| 模块 | 端点 | 说明 |
|---|---|---|
| 鉴权 | `POST /auth/register` · `/auth/login` · `/auth/refresh` · `GET /auth/me` | JWT access+refresh，bcrypt |
| 商品 | `GET /products` · `/products/{id}` · `/categories` | 公开浏览 |
| 购物车 | `GET /cart` · `POST/PATCH/DELETE /cart/items/...` | 需登录 |
| 订单 | `POST /orders` · `GET /orders` · `GET /orders/{no}` · `POST /orders/{no}/pay` · `/cancel` | 状态机 created→paid→completed，模拟支付 |
| AI 客服 | `POST /chat` | SSE 流式，RAG + 订单/商品/优惠工具 |
| 经营分析 | `POST /analytics` | SSE 流式，多步销售分析工具 |
| Ops | `GET /health` · `GET /metrics` | 健康检查 / Prometheus 指标 |

**AI 接口调用示例**（curl SSE）：

```bash
# 1. 登录拿 token
TOKEN=$(curl -s -X POST 127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"customer@example.com","password":"...password"}' | jq -r .access_token)

# 2. 问 AI 客服"我的订单到哪里了"（流式观察 agent 每步）
curl -N 127.0.0.1:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"question":"我的订单到哪里了？"}'

# 3. 经营分析："上季度销售额和上月比涨了多少，哪个品类最好？"
curl -N 127.0.0.1:8000/api/v1/analytics \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"question":"最近销售额和上月比涨了多少？哪个品类卖得最好？"}'
```

响应头 `X-Trace-ID` 与 body 中的 `trace_id` 即为该次 agent 运行的全链路 ID，可在 Langfuse / 日志中检索。

---

## 前端（Vue 3 + Element Plus）

`frontend/` 下的 SPA，覆盖业务闭环与两个 AI 功能：

| 页面 | 功能 |
|---|---|
| 登录 / 注册 | JWT 存 localStorage，路由守卫 |
| 商品列表 | 搜索 + 加购 |
| 购物车 | 改数量 / 删除 / 下单 |
| 我的订单 | 模拟支付 / 取消，状态标签 |
| AI 客服 | SSE 流式：agent 步骤轨迹 + 打字机回答 + 可折叠引用来源 |
| 经营分析 | SSE 流式：多步工具调用轨迹 + 结构化报告（markdown 表格） |

**关键实现**：后端 `/chat` `/analytics` 是 **POST + SSE**，浏览器 `EventSource` 只支持 GET，所以前端用 `fetch + ReadableStream` 逐行解析事件流（`src/api/sse.js`），按 `step / answer / done / error` 分发，回答用打字机逐字渲染模拟流式观感。`vite build` 产物输出到 `app/static`，由 FastAPI 静态托管（未构建前端时后端纯 API 模式不受影响）。解析逻辑经 `node scripts/sse_frontend_test.mjs` 对真实后端做端到端验证。

---

## 可观测性（简历重点）

```
HTTP 请求 ──▶ 中间件生成 trace_id（X-Trace-ID 回传）
   │              ├─ structlog JSON 日志：trace_id / user_id / node / tokens / latency
   │              ├─ Prometheus 指标：http / agent_runs / retrievals / tool_calls / rate_limited
   ▼
LangGraph run ──▶ Langfuse CallbackHandler（每步 span，自动嵌套）
   │              └─ config.metadata 写入 trace_id / session_id / user_id / tags
   ▼
每一步审计 step{node, detail} ──▶ SSE 流回前端，用户可见 agent 思考过程
```

详见 [docs/observability.md](docs/observability.md)。

---

## 评测（eval）

三数据集 × 六类指标，`uv run python -m eval.runner` 输出 `eval/report.md` 并回写 Langfuse 打标：

| 数据集 | 数量 | 指标 |
|---|---|---|
| customer_service | 5（agent 冒烟上限可调） | 工具序列匹配 / 关键字段覆盖 / 引用精确率 / 引用存在率 / 正确性 / 忠实度 |
| analytics | 4（agent 冒烟上限可调） | 工具序列匹配 / 关键字段覆盖 / 正确性 / 忠实度 |
| retrieval | 10 | Recall@k / MRR |

> 评测均为**真实 agent 运行 + LLM-as-judge**，成本较高，`eval/config.py` 的 `MAX_AGENT_CASES` 默认每个 agent 数据集跑 5 条控制冒烟时长；检索集无需 LLM，可直接扩展到几十条。加大评估规模见 [docs/eval.md](docs/eval.md)。

当前报告：检索 recall@k 95% / MRR 90%；客服正确性 98% / 忠实度 100%；分析正确性 82.5% / 忠实度 87.5%。详见 [docs/eval.md](docs/eval.md)。

---

## 安全与工程化

- **鉴权**：JWT（access 30min + refresh 7day）+ bcrypt + RBAC（`admin` / `customer`）依赖注入。
- **限流**：slowapi + Redis，公开接口 100 次/分、AI 接口 20 次/分，429 + Retry-After。
- **AI 安全**：工具白名单 + JSON Schema 参数校验 + `asyncio.wait_for` 超时 + 结果截断；客服工具**只能查当前登录用户数据**；`max_steps` 上限防死循环。
- **日志**：structlog JSON（含 trace_id），`.env` 不入库（`.gitignore`）。
- **部署**：Docker 多阶段构建（uv → slim，非 root，healthcheck），Compose profile 按需起服务。

---

## 面试话术速记

1. **为什么单 Agent 而不是多 Agent？** 客服与经营分析共享同一张图，仅按意图分流工具集与是否检索——复杂度可控、可观测链路简单，多 Agent 带来的编排成本对当前需求是负收益。
2. **可观测怎么做？** HTTP 中间件生成 trace_id（ContextVar + `X-Trace-ID`），Langfuse 回调自动把 graph 每步拉成嵌套 span，structlog 打 trace_id 维度日志，Prometheus 聚合指标；Langfuse 可插拔，挂了不影响主链路。
3. **AI 怎么保证真实？** 工具直接读业务库 + answer 节点强制引用校验（越界 `[n]` 会被剥离），评测的 citation_precision 用"无引用视为无编造"的保守语义兜底。
4. **中文检索为什么效果好？** 稠密（fastembed 中文向量）+ jieba 预分词 BM25 稀疏，Qdrant FusionQuery(RRF) 融合，规避了 BM25 对中文空白分词失效的经典坑。

---

## 本地测试

```bash
uv run pytest -q        # 39 个用例：鉴权 / 订单状态机 / 意图 / 引用 / hybrid / graph / API 集成
```
