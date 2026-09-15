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
| orders | id, order_no, user_id, status, total | 状态机见下 |
| order_items | order_id, product_id, name, price, quantity | 下单快照，价格不受后续改价影响 |
| payments | id, order_id, method, status | 模拟支付 |

**订单状态机**：

```
             支付(模拟)        取消
created ────────────────▶ paid ──────────▶ completed
   │                      ▲
   └──────────────────────┘   created ⇄ cancelled（未支付可取消）
```

**AI 安全约束**：订单/购物车查询工具在 SQL 层强制 `user_id = 当前登录用户`，杜绝越权。

## 4. Agent 图（LangGraph StateGraph）

```
START → route ──(analytics)──▶ reason
   │                           │
   └──(customer_service)──▶ retrieve ──▶ reason
                                           │
                      reason 决策工具调用 ──▶ execute_tool ──▶ reason
                      reason 直接作答  ───▶ answer ──▶ END
```

- **route**：意图识别（关键词启发式计分，纯函数可单测）。经营分析加分、客服减分，>0 归为 analytics。
- **retrieve**：Qdrant 混合检索（dense + sparse RRF），把召回知识块拼入用户提示词。
- **reason**：`ChatAnthropic` + 工具 schema `bind_tools`，LLM 决策调用工具还是直接回答。
- **execute_tool**：按 schema 执行注册工具，超时 + 截断 + 打点，结果以 `ToolMessage` 回填。
- **answer**：抽取 `[n]` 引用 → 剥离越界悬空引用 → 拼参考来源，保证"带引用作答"。

`max_steps` 上限 6 步防死循环；`ToolRegistry` 以闭包绑定当前会话与用户，单请求内隔离。

## 5. RAG 设计

- **语料**：`data/knowledge_base/` 下商品知识文档（说明 / 规格 / 退换政策），入库时按 Markdown 标题切块，`doc_id = {文件名}:{标题}`。
- **稠密向量**：fastembed 本地推理（jina-embeddings-v2-base-zh），构建时预下载，避免运行时联网。
- **稀疏向量**：jieba 预分词生成 BM25 权重（中文不走空白分词，规避经典失效坑）。
- **融合检索**：Qdrant 命名向量 + `FusionQuery(rrf)` 原生 RRF 融合，top-k 默认 6。
- **引用**：`[n]` 严格指向"检索到的知识块"；工具返回的实时数据（价格/库存/订单状态）不加引用，防止把实时数据误标成知识库来源。

## 6. 工具集（读真实业务库）

| 工具 | 模块 | 说明 |
|---|---|---|
| query_order | order_tools | 查当前用户真实订单状态（user_id 强制） |
| query_product | product_tools | 查商品价格 / 库存 / 上下架 |
| calc_discount | product_tools | 按优惠规则算最终价 |
| current_date | analytics_tools | 报告取当前日期（避免 LLM 乱猜时间） |
| query_sales_summary | analytics_tools | 按日 / 品类聚合销售额 |
| calc_growth | analytics_tools | 环比 / 同比计算 |
| top_products | analytics_tools | 畅销榜 |

工具实现统一走 `ToolRegistry.execute(name, args)`：JSON Schema 校验 → 白名单 → 超时 → 截断。

## 7. 鉴权与安全

- JWT（HS256）：access 30min + refresh 7day；`/auth/refresh` 轮换。
- RBAC：`Depends(get_current_admin)` / `get_current_user` 依赖注入控制 admin 操作（商品上下架、知识库入库）。
- 限流：slowapi + Redis，`/chat` `/analytics` 20 次/分/用户，其余 100 次/分，429 带 `Retry-After`。
- 密码 bcrypt；`.env` 不入库；AI 接口强制登录。

## 8. 部署

- **Dockerfile**：多阶段（uv builder → python:3.12-slim），非 root，healthcheck，`gunicorn -k uvicorn.workers.UvicornWorker -w 2`。
- **Compose profile**：
  - 默认：api / postgres / qdrant / redis（业务核心）
  - `observability`：langfuse-web / worker / clickhouse / minio（内存充足时启用）
  - `monitoring`：prometheus / grafana
- 基础设施端口仅绑 `127.0.0.1`，唯一对外 `api:8000`。
