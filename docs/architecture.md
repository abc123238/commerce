# 架构设计

## 1. 总体设计原则

- **业务主体 + AI 增强**：电商业务闭环是主体，AI 智能体是核心功能模块（客服 / 经营分析），而非独立 demo。业务层保证工程基本功，AI 层证明"用 AI 解决真实问题"。
- **可观测优先**：一切请求、agent 运行、工具调用都有 trace_id 贯穿，任何一步可回溯。
- **单 Agent 多步骤**：客服与经营分析共享同一张 LangGraph 图，按意图分流工具集与检索路径，避免多 Agent 的编排复杂度。
- **Langfuse 可插拔**：可观测基础设施不可用时，主链路降级为日志 + 指标，不阻塞业务。

## 2. 分层架构

```
HTTP 层（api/v1）          → 鉴权 / 参数校验 / 限流 / SSE
业务层（core + db）         → JWT/RBAC / 日志 / 追踪 / 缓存 / SQLAlchemy 模型
Agent 层（agent/）          → LangGraph 图 + 节点 + 工具集 + 意图识别
RAG 层（rag/）              → embedding / 向量存储 / 混合检索 / 引用
可观测层（observability/）  → Langfuse / Prometheus 指标 / structlog
```

## 3. 数据库设计（PostgreSQL）

| 实体 | 关键字段 | 说明 |
|---|---|---|
| users | id, email, password_hash, role | role ∈ {admin, customer}，RBAC 依据 |
| categories | id, name | 商品分类 |
| products | id, category_id, name, description, price, stock, status | status ∈ {on, off} |
| carts / cart_items | user_id / cart_id, product_id, quantity | 购物车 |
| orders | id, order_no, user_id, status, total, paid_at, shipped_at, completed_at, tracking_no, carrier | 状态机见下；物流四字段由模拟物流写入 |
| order_items | order_id, product_id, name, price, quantity | 下单快照，价格不受后续改价影响 |
| payments | id, order_id, method, status | 模拟支付 |
| order_status_events | order_id, from_status, to_status, occurred_at, source, actor_user_id, meta | 订单状态事件（业务时间）；`UNIQUE(order_id,to_status)` 保证幂等 |
| daily_sales_agg | stat_date, revenue, order_count, item_count | 按日预聚合 |
| daily_category_sales_agg | stat_date, category_id, revenue, order_count, item_count | 按日 × 品类预聚合；`UNIQUE(stat_date,category_id)` |
| eval_runs | run_id, status, timestamps, git_commit, prompt_hash, config/dataset_snapshot, aggregate, gate_* | 评测 run；partial unique index 限制同时只有一个 active run |
| eval_case_results | run_id, dataset, case_id, status, tool_events, retrieved, metrics, judge_* | 逐用例明细；`UNIQUE(run_id,dataset,case_id)` |

**订单状态机**：

```
                      支付(模拟)             惰性推进(查物流时)        惰性推进
created ─────────────────────▶ paid ──────────────────────▶ shipped ──────────▶ completed
   │
   └──▶ cancelled（未支付可取消；created ⇄ cancelled）
```

`paid` 之后的跃迁由 `logistics_service.advance_order_status` 在**查询物流时**惰性完成：
`FOR UPDATE` 行锁 + 状态前置判断保证幂等，一次请求最多连跳两态；`shipped_at` /
`completed_at` 写的是 `paid_at + 固定偏移`而非 `now`，因此轨迹与落库时间永远一致。

每次状态变更同时写 `order_status_events`（created / paid / cancelled / shipped / completed）：
`occurred_at` 用业务时间而非写入时间，停留时长可复现；`(order_id,to_status)` 唯一约束 +
`INSERT ... ON CONFLICT DO NOTHING` 保证重复推进/并发不抛错、不重复计数。

**AI 安全约束**：订单/购物车查询工具在 SQL 层强制 `user_id = 当前登录用户`，杜绝越权。

## 4. Agent 图（LangGraph StateGraph）

```
START → route ──(analytics)──▶ reason
   │                           │
   └──(customer_service)──▶ rewrite ──▶ retrieve ──▶ reason
                                                       │
                      reason 决策工具调用 ──▶ execute_tool ──▶ reason
                      reason 直接作答  ───▶ answer ──▶ END
```

- **route**：意图识别（关键词启发式计分，纯函数可单测）。经营分析加分、客服减分，>0 归为 analytics。
- **rewrite**：结合最近 4 轮对话历史，把「它 / 这个 / 那款」等指代与省略改写成自包含的检索 query；超时、异常或空输出降级为原问题，不阻断回答。经营分析分支不经过此节点。
- **retrieve**：用改写后的 query 做 Qdrant 混合检索（dense + sparse RRF），把召回知识块拼入用户提示词。
- **reason**：`ChatAnthropic` + 工具 schema `bind_tools`，LLM 决策调用工具还是直接回答。
- **execute_tool**：按 schema 执行注册工具，超时 + 截断 + 打点，结果以 `ToolMessage` 回填。
- **answer**：抽取 `[n]` 引用 → 剥离越界悬空引用 → 拼参考来源，保证"带引用作答"。

`max_steps` 上限 6 步防死循环；`ToolRegistry` 以闭包绑定当前会话与用户，单请求内隔离。

## 5. RAG 设计

- **语料**：`data/knowledge_base/` 下商品知识文档（说明 / 规格 / 退换政策），入库时按 Markdown 标题切块，`doc_id = {文件名}:{标题}`。
- **稠密向量**：fastembed 本地推理（jina-embeddings-v2-base-zh），构建时预下载，避免运行时联网。
- **稀疏向量**：jieba 预分词生成 BM25 权重（中文不走空白分词，规避经典失效坑）。
- **融合检索**：Qdrant 命名向量 + `FusionQuery(rrf)` 原生 RRF 融合，top-k 默认 6。
- **查询改写**：检索前用主 LLM 结合历史消解指代与省略（`app/agent/query_rewrite.py`），独立 8s 超时；失败自动回退原问题。单轮已自包含的问题要求模型原样返回。
- **引用**：`[n]` 严格指向"检索到的知识块"；工具返回的实时数据（价格/库存/订单状态）不加引用，防止把实时数据误标成知识库来源。

## 6. 工具集（读真实业务库）

读工具（无写副作用）：

| 工具 | 模块 | 说明 |
|---|---|---|
| query_order | order_tools | 查当前用户真实订单状态（user_id 强制） |
| query_tracking | order_tools | 查模拟物流轨迹（顺带幂等推进状态，他人订单 404） |
| query_product | product_tools | 查商品价格 / 库存 / 上下架（返回带商品 ID，下单要用） |
| calc_discount | product_tools | 按优惠规则算最终价 |
| current_date | analytics_tools | 报告取当前日期（避免 LLM 乱猜时间） |
| query_sales_summary | analytics_tools | 按日 / 品类聚合销售额 |
| calc_growth | analytics_tools | 环比 / 同比计算 |
| top_products | analytics_tools | 畅销榜 |

写工具（**收窄在 `order_write_tools.py` 一个文件**便于审计，只注入客服意图）：

| 工具 | 说明 |
|---|---|
| prepare_order | 只读核对商品/库存 → 清单写入 Redis 快照，不建单 |
| confirm_order | **无参**，只消费快照 → 建单 + 模拟支付 |

两阶段确认的四道防线：① `confirm_order` 入参 schema 无商品字段；② `user_id` 只经
闭包注入且快照 key 含 user_id；③ 快照 `GETDEL` **原子消费**（并发 confirm 只有一个拿到）；
④ 快照记录生成轮的 `run_id`，同一轮先 prepare 再 confirm 会被拒——`execute_tool` 节点会在
一次调用里顺序执行同一轮的全部 tool_calls，前三条挡不住这个时序。
Redis 不可用时写路径 **fail-closed**（下单不可用），不做"跳过快照"的降级。

工具实现统一走 `ToolRegistry.execute(name, args)`：JSON Schema 校验 → 白名单 → 超时 → 截断。

## 7. 数据指标层与预聚合

**唯一口径**：`app/services/analytics_service.py` 定义 `PAID_FILTER = Order.paid_at IS NOT NULL`
与营收表达式（`unit_price * quantity`），Agent 工具、结构化 API、预聚合刷新都调用它。
营收按 `paid_at` 判断而非 `status == paid`，否则订单被模拟物流推进到 shipped/completed 后会静默漏统计。

**预聚合**：`analytics_agg_service.refresh_daily_agg()` 在事务内先取
`pg_advisory_xact_lock`，再删除目标区间并插入重算结果——删除 + 插入保证幂等并清理脏行。
对账测试用固定数据验证「预聚合表 == 实时指标 == 手工期望」，而不是只比较两个都调用同一函数的输出。

**漏斗**：以区间内创建订单为 cohort，统计到达各状态的数量、转化率与相邻事件停留时长；
取消订单单独计数。`occurred_at` 使用业务时间，保证重跑可复现。

**API**：`/analytics/sales|top-products|funnel|aggregates` 与 `POST /analytics/aggregates/refresh`，全部 admin-only，无前端页面。

## 8. 评测流水线

```
CLI / POST /eval/runs
   → eval_service.create_run     数据集过滤 + 快照 + partial unique index 抢槽位
   → execute_run                原子 claim pending→running
   → eval/engine.run_datasets   检索 / Agent 执行，逐用例 case_sink 落库 + 心跳
   → 聚合 + 门禁                status=ok 才参与均值，error 单独计数
   → succeeded / failed         写 aggregate、gate、finished_at
```

- `eval/engine.py` 与 DB 解耦，CLI 和 API 共用；`eval/datasets.py` 负责 JSONL 加载/过滤/指纹/校验。
- Agent 的 `execute_tool` 节点额外产出结构化 `tool_events`，指标直接用真实工具名，不再解析中文审计文案。
- 运行失败（`status=error`、judge 调用/解析失败）指标为 null，与"真 0 分"分离。
- `heartbeat_at` + `reap_stale_runs()` 兜底进程重启；完整评测推荐 CLI，BackgroundTasks 只适合触发与轮询。

## 9. 鉴权与安全

- JWT（HS256）：access 30min + refresh 7day；`/auth/refresh` 轮换。
- RBAC：`Depends(get_current_admin)` / `get_current_user` 依赖注入控制 admin 操作（商品上下架、知识库入库）。
- 限流：slowapi + Redis，`/chat` `/analytics` 20 次/分/用户，其余 100 次/分，429 带 `Retry-After`。
- 密码 bcrypt；`.env` 不入库；AI 接口强制登录。

## 10. 部署

- **Dockerfile**：多阶段（uv builder → python:3.12-slim），非 root，healthcheck，`gunicorn -k uvicorn.workers.UvicornWorker -w 2`。
- **Compose profile**：
  - 默认：api / postgres / qdrant / redis（业务核心）
  - `observability`：langfuse-web / worker / clickhouse / minio（内存充足时启用）
  - `monitoring`：prometheus / grafana
- 基础设施端口仅绑 `127.0.0.1`，唯一对外 `api:8000`。
