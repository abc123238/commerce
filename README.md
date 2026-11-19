# AI 电商平台（ai-commerce-platform）

一个以**完整业务系统为主体、AI 智能体为核心功能模块**的全栈项目：电商业务闭环（鉴权 / 商品 / 购物车 / 订单 / 模拟支付）之上，叠加两个 AI 增强能力——**AI 客服**（RAG + 工具调用打穿真实业务库）与**经营分析智能体**（多步工具调用出报表），并配齐 Docker 部署、Langfuse 可观测链路、Prometheus 监控、评测流水线与限流安全。

> 设计目标：业务闭环、AI 能力与工程化并重——可运行、可观测、可评测。

---

## 一句话亮点

- **AI 工具读真实业务库**：客服回答"我的订单到哪了"时，通过工具实时查询登录用户的真实订单，而非编造或只查静态知识库。
- **Agent 能真的下单**："帮我买 2 个无线蓝牙耳机"→ 生成待确认清单 → 用户回"确认"才建单支付。`confirm_order` **不接受任何商品参数**只消费 Redis 快照，加上 `user_id` 闭包注入、`GETDEL` 原子消费、`run_id` 同轮守卫四道结构性防线，"绕过确认"不靠 prompt 兜。
- **模拟物流闭环**：以 `paid_at` 为基点的**确定性**轨迹（纯函数、无随机、可复现），`paid → shipped → completed` 状态**惰性幂等推进**，前端时间线每 30 秒自动前进。
- **全链路可观测**：HTTP `X-Trace-ID` → LangGraph 每步 span → Langfuse trace，一次请求端到端可追溯。
- **工程化而非 demo**：JWT + RBAC、订单状态机、限流、SSE 流式、Docker 多阶段构建、评测结果落库与门禁。

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
START → route(意图) → rewrite(查询改写) → retrieve(RAG 混合检索) → reason(LLM 决策) ⇄ execute_tool(读业务库) → answer(引用校验) → END
```

**AI Agent 数据流**（客服示例）：

```
用户: "我的订单到哪里了？"
  → route       意图识别 = customer_service
  → rewrite     结合历史消解指代，生成自包含检索 query（失败降级原问题）
  → retrieve    Qdrant 混合检索商品知识库（召回 top-k 块）
  → reason      LLM 决策：需要工具 query_order(order_no) 查实时状态
  → execute_tool 读取当前登录用户真实订单（权限：只能查自己）
  → reason      汇总检索资料 + 工具结果，组织带引用回答
  → answer      引用校验 [n] → END
```

**Agent 下单数据流**（两阶段确认，写操作的安全边界）：

```
第 1 轮  用户: "帮我买 2 个无线蓝牙耳机"
  → reason        LLM 决策：先 query_product 拿商品 ID，再 prepare_order 生成清单
  → execute_tool  只读核对商品/库存 → 写 Redis 快照 agent:order:pending:{user_id}
  → answer        复述清单（单价/数量/合计）并请用户确认——此轮绝不建单

第 2 轮  用户: "确认"
  → execute_tool  confirm_order()：无商品参数，GETDEL 原子消费快照 → 建单 + 模拟支付
  → answer        订单号 / 实付金额 / 物流时效提示
```

四道防线让"跳过确认"不可能：① `confirm_order` 入参 schema 里没有商品字段；② `user_id` 只经工具闭包注入，快照 key 含 user_id，跨用户读不到；③ `GETDEL` 原子消费（并发 confirm 只有一个拿到快照）；④ 快照记录生成本轮的 `run_id`，同一轮内先 prepare 再 confirm 会被拒——`core.py` 会在一次 `execute_tool` 里顺序执行同一轮的全部 tool_calls，前三条挡不住这个时序。

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
| 评测 | 自研评测流水线（run/case 持久化、检索 / 引用 / 任务 / 鲁棒性 / LLM-as-judge、延迟与 token 成本、门禁、run 对比） |
| MCP | FastMCP demo（商品查询能力标准工具化） |
| 前端 | Vue 3 + Vite + Element Plus（SSE 流式聊天，vite build 产物由 FastAPI 静态托管） |
| 部署 | Docker Compose（profile 拆分：核心 / observability / monitoring） |

---

## 目录结构

```
ai-commerce-platform/
├── app/
│   ├── main.py / config.py
│   ├── api/v1/            # auth / products / cart / orders / chat / analytics / eval
│   ├── core/              # security(JWT+RBAC) / logging / trace / rate_limit / redis
│   ├── db/models/         # user / product / cart / order / eval / order_event / analytics_agg
│   ├── agent/             # graph / nodes / tools / prompts / intent / llm / state
│   │                      #   tools/: order_read · order_write(下单) · product · analytics · registry
│   ├── services/          # order / logistics(模拟物流) / pending_order(快照)
│   │                      #   analytics(统一口径) / analytics_agg(预聚合) / order_event / eval(run 生命周期)
│   ├── rag/               # embeddings / vectorstore / sparse / ingest / citation
│   ├── observability/     # langfuse / middleware / metrics
│   └── schemas/
├── eval/                  # runner(CLI) / engine / datasets / judge / usage / report / thresholds.json + 四数据集
├── scripts/               # seed / ingest_kb / smoke / verify_flow / verify_order_flow
├── data/knowledge_base/   # 客服 RAG 语料
├── docs/                  # architecture / observability / eval / design_decisions
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
| 订单 | `POST /orders` · `GET /orders` · `GET /orders/{no}` · `POST /orders/{no}/pay` · `/cancel` | 状态机 created→paid→shipped→completed，模拟支付 |
| 物流 | `GET /orders/{no}/tracking` | 模拟轨迹（轨迹不落库，查一次顺带幂等推进状态） |
| AI 客服 | `POST /chat` | SSE 流式，RAG + 订单/物流/商品/优惠工具 + 下单（两阶段确认） |
| 经营分析 | `POST /analytics` | SSE 流式，多步销售分析工具 |
| 数据平台 | `GET /analytics/sales` · `/top-products` · `/funnel` · `/aggregates` · `POST /analytics/aggregates/refresh` | admin-only：统一指标口径、漏斗、预聚合刷新（无前端页面） |
| 评测 | `POST /eval/runs` · `GET /eval/runs[/{id}[/cases]]` · `GET /eval/runs/{id}/compare/{base}` | admin-only：BackgroundTasks 触发（默认按 `eval/thresholds.json` 记录门禁）、状态轮询、逐用例明细与两次运行对比 |
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
| 我的订单 | 模拟支付 / 取消，状态标签，**查看物流**抽屉时间线（30 秒自动刷新，可看到轨迹随时间推进） |
| AI 客服 | SSE 流式：agent 步骤轨迹 + 打字机回答 + 可折叠引用来源 |
| 经营分析 | SSE 流式：多步工具调用轨迹 + 结构化报告（markdown 表格） |

**关键实现**：后端 `/chat` `/analytics` 是 **POST + SSE**，浏览器 `EventSource` 只支持 GET，所以前端用 `fetch + ReadableStream` 逐行解析事件流（`src/api/sse.js`），按 `step / answer / done / error` 分发，回答用打字机逐字渲染模拟流式观感。`vite build` 产物输出到 `app/static`，由 FastAPI 静态托管（未构建前端时后端纯 API 模式不受影响）。解析逻辑经 `node scripts/sse_frontend_test.mjs` 对真实后端做端到端验证。

---

## 可观测性

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

## 评测（eval）：可追溯、可对比、可门禁

四数据集 × 六类指标，CLI 与后端 API 共用同一执行内核（`eval/engine.py`）：

| 数据集 | 数量 | 指标 |
|---|---|---|
| customer_service | 18 | 工具序列匹配 / 关键字段覆盖 / 引用精确率 / 引用按需 / 正确性 / 忠实度 |
| analytics | 16 | 工具序列匹配 / 关键字段覆盖 / 正确性 / 忠实度 |
| retrieval | 18 | Recall@k / MRR |
| robustness | 12 | 行为符合预期（拒答/追问）/ 无敏感内容泄露 / 正确性 / 忠实度 |

每个数据集另统计**延迟 p50/p95/avg/max、输入/输出 token 与成本估算**（只算成功用例；成本按 `EVAL_INPUT_PRICE_PER_MTOK` / `EVAL_OUTPUT_PRICE_PER_MTOK` 估算）。

```bash
# 全量 + 门禁（默认入库；容器内运行）
python -m eval.runner --gate
# 局部冒烟：只跑检索、不调 LLM、不写库
python -m eval.runner --dataset retrieval --no-judge --no-persist
# 按标签/难度/条数过滤
python -m eval.runner --dataset customer_service --tag order_write --limit 3
```

- **run 级持久化**：配置/数据集快照、git commit、prompt 指纹、模型名、逐用例工具轨迹/检索证据/judge 理由与错误全部入库。
- **运行失败 ≠ 真 0 分**：`status=error` 与指标为 `null` 分开统计；聚合只对成功用例求均值，并单独报 `error_count`。
- **单实例并发闸门**：`eval_runs` 上的 partial unique index（`WHERE status IN ('pending','running')`）跨进程原子生效，取代 Redis TTL 锁。
- **BackgroundTasks 的边界**：进程内任务、gunicorn `timeout=60` 可能中断长评测；DB 状态 + heartbeat + reaper 兜底，完整评测推荐容器内 CLI。
- **CI 只跑确定性部分**：迁移链、纯单测、Postgres 集成测试、前端构建；真实 LLM 评测/Qdrant/Redis 用例与门禁不在 CI（原因与本地命令见 [docs/eval.md](docs/eval.md)）。

> 数据集扩充后 `eval/report.md` 的历史数字已不可直接对比（judge prompt 也升级为 `v2-context`），请以最新一次运行的报告为准。

---

## 安全与工程化

- **鉴权**：JWT（access 30min + refresh 7day）+ bcrypt + RBAC（`admin` / `customer`）依赖注入。
- **限流**：slowapi + Redis，公开接口 100 次/分、AI 接口 20 次/分，429 + Retry-After。
- **AI 安全**：工具白名单 + JSON Schema 参数校验 + `asyncio.wait_for` 超时 + 结果截断；客服工具**只能查当前登录用户数据**；`max_steps` 上限防死循环。
- **AI 写操作边界**：写工具收窄在 `app/agent/tools/order_write_tools.py` 一个文件便于审计；只有客服意图注入写工具（经营分析是只读场景）；Redis 不可用时**写路径 fail-closed**（下单直接不可用），不做"跳过快照"的降级。
- **日志**：structlog JSON（含 trace_id），`.env` 不入库（`.gitignore`）。
- **部署**：Docker 多阶段构建（uv → slim，非 root，healthcheck），Compose profile 按需起服务。

---

## 关键设计取舍

1. **为什么单 Agent 而不是多 Agent？** 客服与经营分析共享同一张图，仅按意图分流工具集与是否检索——复杂度可控、可观测链路简单，多 Agent 带来的编排成本对当前需求是负收益。
2. **可观测怎么做？** HTTP 中间件生成 trace_id（ContextVar + `X-Trace-ID`），Langfuse 回调自动把 graph 每步拉成嵌套 span，structlog 打 trace_id 维度日志，Prometheus 聚合指标；Langfuse 可插拔，挂了不影响主链路。
3. **AI 怎么保证真实？** 工具直接读业务库 + answer 节点强制引用校验（越界 `[n]` 会被剥离），评测的 citation_precision 用"无引用视为无编造"的保守语义兜底。
4. **中文检索为什么效果好？** 稠密（fastembed 中文向量）+ jieba 预分词 BM25 稀疏，Qdrant FusionQuery(RRF) 融合，规避了 BM25 对中文空白分词失效的经典坑。

---

## 本地测试

```bash
uv run pytest -m "not integration" -q   # 纯单测：意图 / 引用 / 指标 / 数据集校验（conftest 仍需 Postgres 建测试库）
uv run pytest -q                        # 全量：另含订单状态机 / 物流 / Agent 下单 / 指标口径对账 / 评测 run 生命周期
```

> 集成测试需要已启动 Postgres；Qdrant/Redis 相关用例（hybrid、Agent 下单）另需对应服务。
