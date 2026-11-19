# 关键设计决策与数据流

> 记录系统的核心数据流与主要设计取舍，供维护与 onboarding 使用。评测指标与基线以 [eval.md](eval.md) 的最新运行为准。

---

## 1. 项目定位

这是一个**以完整业务系统为主体、AI 智能体为核心功能模块**的全栈项目：业务闭环（鉴权 / 商品 / 购物车 / 订单 / 模拟支付）之上，叠加两个 AI 能力：

- **AI 客服**：RAG 检索商品知识库 + 工具调用打穿真实业务库——用户问"我的订单到哪了"，agent 会实时查这个登录用户自己的订单，回答带知识库引用，不是编的。
- **经营分析智能体**：多步工具调用（查销售汇总 → 算环比增长 → 畅销榜）输出结构化报告。

配套工程化：JWT + RBAC、限流、SSE 流式、Docker 多阶段部署、Langfuse 全链路可观测、Prometheus 指标。数据侧做了**统一指标口径 + 预聚合对账 + 订单状态事件**；评测侧是一条**可追溯、可对比、可门禁的评测流水线**（真实跑检索与 Agent，结果落库，两次 run 可对比）。技术栈 FastAPI + SQLAlchemy async + PostgreSQL + Redis + Qdrant + LangGraph。

---

## 2. 核心场景数据流

### 2.1 注册登录（JWT 流转）

1. `POST /api/v1/auth/register` → 校验邮箱唯一 → `bcrypt.hashpw` 加密存库（`app/core/security.py:20`）。
2. `POST /auth/login` → `verify_password` → `create_access_token`（payload 含 `sub`=user_id、`role`、`type=access`、`exp`，HS256 签名，30 分钟）+ `create_refresh_token`（7 天）→ 返回给前端。
3. 前端把 `access_token` 存 localStorage，后续请求带 `Authorization: Bearer xxx`。
4. 受保护接口用 `Depends(get_current_user)`（`app/api/deps.py:11`）：解码 JWT → 校验 `type=access` → 查库返回 User。
5. **RBAC**：admin 接口（商品管理、知识库入库）用 `Depends(require_roles("admin"))`，检查 `user.role`，非 admin 返回 403。

**设计要点**：refresh token 通过 `POST /auth/refresh` 轮换换新 access；JWT 无状态，验签即可，服务端不存 session。

### 2.2 下单支付（订单状态机 + 并发防超卖）

1. `POST /orders` → `order_service.create_order_from_cart`（`app/services/order_service.py:46`）：
   - 读当前用户购物车 → **对每个商品 `SELECT ... FOR UPDATE` 行锁**（防并发超卖）
   - 校验商品在售 + 库存充足
   - 建 `orders` + `order_items` 快照（记录下单时的 name/price，后续改价不影响已下订单）
   - 扣库存 → 清空购物车 → 统一 `commit`（事务原子性：任一步失败整体回滚）
2. 订单号 `"ORD" + token_hex(8).upper()`。
3. `POST /orders/{no}/pay` → `pay_order`：校验状态必须 `created` → 置 `paid` + 写 `payments` 记录（`method="mock"`，模拟支付）。
4. 状态机：`created → paid → shipped → completed`，`created ⇄ cancelled`（未支付可取消）。
   `paid` 之后的跃迁由**模拟物流惰性推进**（查物流时才发生，见 3.17）。

**设计要点**：行锁在事务内串行化库存扣减，实现简单且对低并发足够；乐观锁适合高并发但需要重试，属于后续演进方向。

### 2.3 AI 客服查订单（全链路）

前端（Vue3）用 `fetch + ReadableStream` 发起 **POST** `/api/v1/chat`（SSE 流式，`app/api/v1/chat.py`）：

1. **中间件**（`app/observability/middleware.py`）生成 `trace_id`（或沿用请求头 `X-Trace-ID`）→ 写入 ContextVar + structlog 上下文 → 响应头回传。
2. `build_agent`（`app/agent/graph.py:75`）：
   - `classify_intent(question)` 识别意图（关键词加权纯函数：分析词 +1、客服词 -1，>0 为 analytics）
   - 客服工具集 = `build_order_tools(db, user_id)` + `build_product_tools(db)` + `build_order_write_tools(db, user_id, run_id)`（**闭包绑定当前用户与本次 run**；经营分析分支不注入写工具）
   - 组装 `ToolRegistry` → 构建 LangGraph StateGraph
3. **graph 执行**（`stream_mode="updates"`，每步产出 SSE `step` 事件）：
   - `route`：意图识别
   - `rewrite`：客服分支先用主 LLM 结合最近 4 轮历史消解指代（「它/这个」→ 具体商品名），生成自包含检索 query；8s 超时，失败降级原问题（`app/agent/query_rewrite.py`）
   - `retrieve`：用改写后的 query 做 `hybrid_search`，召回 6 个知识块（dense + jieba sparse + RRF），拼进 system/history/知识块/问题 的提示词
   - `reason`：`ChatAnthropic.bind_tools(schemas)`，LLM 决策——该问题决定调用 `query_order` 工具
   - `execute_tool`：`registry.execute` → `asyncio.wait_for` 10s 超时 + 2000 字符截断 → `ToolMessage` 回填（**SQL 里 `where Order.user_id == user_id`，只能查自己**，`app/agent/tools/order_tools.py:24`）
   - `reason`：LLM 汇总"检索资料 + 工具返回的真实订单"组织回答
   - `answer`：抽取回答里 `[n]` → 剥离越界悬空引用 → 拼接参考资料（`app/agent/nodes/core.py`）
4. 每步经 Langfuse `CallbackHandler` 记为 span；结束 `yield done(trace_id)`，前端据此在日志/控制台关联全链路。

**核心设计**：工具返回的是**真实数据库里的实时订单**（价格/状态），知识库引用 `[n]` 只指向检索到的知识块，两者来源清晰不混淆。

### 2.4 Agent 代用户下单（两阶段确认）

1. 第 1 轮 `POST /chat` `{"question":"帮我买 2 个无线蓝牙耳机"}`：
   - `reason`：LLM 先调 `query_product("无线蓝牙耳机")` —— 返回文本带 `（ID=1）`，模型才拿得到下单要用的商品 ID
   - `reason`：LLM 调 `prepare_order([{product_id:1, quantity:2}])` —— 只读核对在售/库存，算总价，写 Redis 快照 `agent:order:pending:{user_id}`，返回清单文本（**不建单**）
   - `answer`：把清单复述给用户并请其确认（prompt 准则强制）
2. 第 2 轮 `POST /chat` `{"question":"确认"}`（带 history）：
   - `reason`：LLM 调 `confirm_order()`（**无参数**）
   - `execute_tool`：`peek_pending_order` 校验 run_id（同轮则拒且**不消费**）→ `take_pending_order` 用 `GETDEL` 原子取出 → `order_service.create_order`（复用 `_lock_products` 行锁，单价以 DB 实时价为准）→ `pay_order` 模拟支付
   - `answer`：订单号 / 实付金额 / 物流时效提示
3. 用户再问"我的包裹到哪了"→ `query_tracking` 查真实模拟轨迹（见 3.17）。

**设计要点**：`confirm_order` 不能带商品参数，带了就等于让模型自己指定买什么，"确认"退化成一句 prompt，两阶段确认失去意义。四道防线见 3.16。

---

## 3. 关键设计决策

### 3.1 为什么用 PostgreSQL 不用 MySQL

最直接原因是 LangGraph 官方 checkpoint 后端用 Postgres，AI 编排选型决定了数据库选型；另外 asyncpg 是全栈 async 的成熟 driver、jsonb 承载 agent 的半结构化数据、留了 pgvector 演进空间。

**补充**：MySQL 做基础 CRUD 也没问题，差异主要在 AI 生态衔接；选型重点是"跟技术栈咬合"，不是哪个更好。

### 3.2 为什么 Qdrant 不用 pgvector

业务库和向量库职责分离，避免向量检索拖累 OLTP；Qdrant 原生支持**命名向量 + FusionQuery(RRF)**，一条查询完成 dense + sparse 混合融合，pgvector 做混合检索要手写两段再融合。

**补充**：如果上线且量不大，pgvector 能少一个组件——这是可以说的"演进方向"。

### 3.3 混合检索具体怎么做

（`app/rag/vectorstore.py:78`）对同一文本算两个向量存一个点——dense 用 fastembed（jina-embeddings-v2-base-zh，768 维余弦距离），sparse 用 **jieba 分词** + 词哈希到 10 万维的稀疏向量（`app/rag/sparse.py`，中文必须预分词，否则 BM25 按空白切分整句成一个 token）。查询时两条 prefetch（各取 top-k×3）喂给 `FusionQuery(RRF)` 融合取 top-6。

**补充**：RRF 是 rank-based 融合，不依赖分数归一化，dense 和 sparse 分数量纲不同也能直接融合。

### 3.4 怎么防止 AI 编造引用

（`app/rag/citation.py` + `app/agent/nodes/core.py`）回答里 `[n]` 必须落到**检索到的知识块**上；answer 节点先 `strip_invalid_citations` 把越界的 `[n]` 剥掉，再 `extract_cited_indices` 只保留有效引用并拼参考资料。工具返回的实时数据（订单/库存/价格）**不允许加引用**，防止把实时数据误标成知识库来源。

**补充**：评测里 citation 相关指标用"无引用视为无编造"的保守语义——宁可少引用不错引用。

### 3.5 怎么保证工具只查当前用户的数据

（`app/agent/tools/order_tools.py`）`build_order_tools(db, user_id)` 用**闭包**把 user_id 绑进工具实现，SQL 查询强制 `where Order.user_id == user_id`；工具集在 `build_agent` 时按当前请求的用户构造，请求间天然隔离。

**补充**：这属于"AI 安全约束"，和 RAG 知识库的静态引用是两个不同数据源，权限边界在 SQL 层而非 LLM 提示词层。

### 3.6 为什么单 Agent 而不是多 Agent

（`app/agent/graph.py`）客服和经营分析**共享同一张图**，只在 `route` 节点按意图分流——analytics 跳过检索直接走工具推理，客服走 RAG；工具集也按意图组装。单图让可观测链路简单（一条 trace 贯穿），避免多 Agent 的编排成本。

**补充**：多 Agent 在"角色明显不同、需要并行或隔离上下文"时才有收益，当前需求是负收益。

### 3.7 trace_id 怎么贯穿全链路

（`app/observability/middleware.py` + `app/core/trace.py`）HTTP 中间件生成/沿用 `trace_id` → 写进 **ContextVar**（异步上下文内共享）→ bind 到 structlog 上下文（所有日志带 trace_id）→ 响应头回传 `X-Trace-ID`。agent 层把 `trace_id` 作为 run_id 传入，Langfuse 回调以它为 trace 关联，Graph 每步自动成 span。前端 SSE 的 `done` 事件把 trace_id 返回给用户，任何一步可在日志/控制台按它检索。

**补充**：ContextVar 解决的是"async 上下文传递"，比传参干净；structlog 的 contextvars 绑定让每一行 JSON 日志都带追踪 ID。

### 3.8 Langfuse 挂了/没配怎么办

（`app/observability/langfuse.py`）`make_langfuse_callbacks` 先检查 `langfuse_enabled` 和 key，不满足直接返回 `None`，主链路照常跑——可观测降级为 **structlog 日志 + Prometheus 指标**，不阻塞业务。

**补充**：这是"可观测设施可插拔"的设计原则——基础设施不可用不能让业务承担连带故障。

### 3.9 Prometheus 多进程指标怎么合并

（`gunicorn.conf.py` + `app/observability/metrics.py`）gunicorn 4 worker 各自进程内计数，直接抓取会丢数据。方案：启动时设 `prometheus_multiproc_dir`，`prometheus_client` 把指标写成**每 worker 一个 .db 文件**；`/metrics` 端点用 `MultiProcessCollector` 聚合所有 worker 的文件；`child_exit` 里 `mark_process_dead(pid)` 标记退出 worker 的文件防陈旧数据。

**补充**：单进程开发（uvicorn/pytest）不设该 env，走默认 Registry，行为一致。这个"多进程 + 计数器"的坑是生产部署真实踩过的。

### 3.10 SSE 是怎么实现的？为什么不用 WebSocket

服务端 `sse_starlette.EventSourceResponse`，`graph.astream(stream_mode="updates")` 把每步 `{type: step/answer/done/error}` 序列化成 SSE 事件流回；响应带反缓冲头（`Cache-Control: no-cache`、`X-Accel-Buffering: no`）防止代理/nginx 吞流。**前端**是难点：接口是 POST，浏览器 `EventSource` 只支持 GET，所以前端用 `fetch + ReadableStream` 逐行解析（`frontend/src/api/sse.js`）。

**补充**：SSE 适合"服务端→客户端单向流"（正是 agent 进度/回答），WebSocket 适合双向实时交互；这里用 SSE 更轻、天然兼容 HTTP 缓存和代理。

### 3.11 下单的并发安全怎么保证

（`app/services/order_service.py:56`）对购物车每个商品 `SELECT ... FOR UPDATE` **行锁**串行化库存扣减，同一商品并发下单时后到者等锁，然后看到更新后的库存，不足则报"库存不足"；整笔下单（建订单 + 快照 + 扣库存 + 清购物车）在一个事务里 `commit`，任一步失败整体回滚。

**补充**：快照（order_items 记录下单时价格）保证后续改价不影响历史订单；这是电商领域"下单一致性"的经典考点。

### 3.12 限流怎么做的

（`app/core/rate_limit.py`）slowapi + Redis 存储，`key_func` 优先按 JWT 的 `sub`（登录用户）限流，未登录回退 IP；AI 接口 `/chat` `/analytics` 20 次/分/用户，其余公开接口 100 次/分，超限返回 429 + `Retry-After`。

**补充**：按用户而非单纯 IP，避免代理后面多人共享 IP 被误伤；AI 接口限得更严是因为 LLM 调用成本高。

### 3.13 测试覆盖了什么

测试覆盖鉴权（JWT/RBAC）、订单状态机、模拟物流轨迹（纯单测）、Agent 下单两阶段确认（含同轮绕过/并发 confirm/跨用户三种负向场景）、意图分类、引用提取/校验、混合检索、LangGraph 图执行、FastAPI TestClient 集成（含 SSE）；数据侧有指标口径、预聚合对账、订单状态事件/漏斗；评测侧有数据集校验、run 生命周期/对比、eval API。`uv run pytest -q` 一条命令回归。

**补充**：意图分类是纯函数所以可单测；引用校验测"越界 `[n]` 被剥离"这类边界；对账测试用**独立手工期望值**而不是比较两个都调用同一函数的输出，否则同错即同过。

### 3.14 评测是怎么做的

（`eval/`）四个数据集 JSONL（检索 / 客服 / 分析 / 鲁棒性）是唯一事实来源，带 `id/tag/difficulty/enabled/expected_tools/expected_fields/golden_doc_ids/requires_citation` 等字段，可 review、可离线校验、随 git 版本化。每个用例跑**真实混合检索与完整 LangGraph 图**，不是拿固定回答比对：检索看 Recall@k/MRR，Agent 看工具序列匹配、要点覆盖、引用精确率/存在率，正确性与忠实度由独立 LLM-as-judge 打分。

这不是一次性脚本，而是**可追溯、可对比、可门禁的流水线**：每次运行落 `eval_runs` + `eval_case_results`，保存数据集/配置快照、git commit、prompt 指纹与 judge 理由；CLI 与后端 API 共用 `eval/engine.py`，避免两处口径漂移；支持两次 run 的指标 delta 与逐用例 improved/regressed/broken/fixed；`--gate` 按阈值判定，退出码 2 表示门禁未过。当前指标与基线以 [eval.md](eval.md) 为准。

**补充**：最关键的设计是**区分"运行失败"与"真 0 分"**——`status=error` 或 judge 解析失败时指标为 null，聚合只对成功用例求均值并单独报 `error_count`，否则一次 API 超时会被算成"模型答错"，把指标拉低还掩盖真问题。judge 传入本次检索证据后，faithfulness 从"比对参考答案"变成"比对证据"，更接近真实幻觉检测，代价是 prompt 变长、与历史 run 不可比，所以 run 上记了 `judge_prompt_version`，对比按版本分段。

### 3.15 如果要上线，需要改什么

① 业务库直接换 pgvector 省一个组件（或扩 Qdrant 集群）；② 订单行锁在高峰改**乐观锁/分布式锁**并加压测；③ agent 的 `max_steps=6` 和工具超时按线上实测调；④ checkpoint 用 Postgres 持久化多轮记忆；⑤ 补错误监控告警（把 Prometheus 指标接 Alertmanager）；⑥ 前端包体积优化（Element Plus 按需引入）；⑦ 评测的 BackgroundTasks 换成独立 worker（Celery/RQ/Arq），避免被 gunicorn timeout 杀掉。

**补充**：重点不是"完美方案"，而是"知道哪里是短板 + 有改进思路"。

### 3.16 Agent 下单怎么保证"确认"是真的确认

（`app/agent/tools/order_write_tools.py` + `app/services/pending_order_service.py`）两阶段确认。`prepare_order` 只读核对商品/库存，把清单写进 Redis 快照 `agent:order:pending:{user_id}`；`confirm_order` **不接受任何商品参数**，只消费这份快照。四道结构性防线：

① `confirm_order` 无参数，模型无法指认别的清单；

② `user_id` 只经工具工厂**闭包**注入，两个工具的入参 schema 里根本没有这个字段，快照 key 也含 user_id，A 读不到 B 的；

③ 快照用 **`GETDEL` 原子取出并删除**，并发 confirm 只有一个拿得到，双下单不可能；

④ **`run_id` 绑定**——`core.py` 会在一次 `execute_tool` 里顺序执行同一 AIMessage 的所有 tool_calls，模型可以在同一轮先 prepare 再 confirm，此时快照已存在，前三条全挡不住。所以快照记录生成本轮的 run_id，confirm 比对相同就拒绝，强制"确认"必须发生在用户的**下一条消息**（新的一次请求 = 新的 run_id）。守卫在 GETDEL 之前**只读**生效，被拒时不消费快照——否则用户下一条消息里真正的"确认"会因为清单被白白消耗而失败。

**补充**：Redis 不可用时**写路径 fail-closed**（下单直接不可用），不做"跳过快照让用户直接下单"的降级——那等于把硬保证换成一句 prompt。GETDEL 消费即失效还有个额外收益：`registry.py` 的 10 秒超时取消请求后，重试必然失败，不会双下单。`GETDEL` 需要 Redis ≥ 6.2，这是部署时要确认的版本底线。

### 3.17 模拟物流的轨迹为什么是确定性的？状态推进为什么是惰性的

（`app/services/logistics_service.py`）轨迹**不落库**，以 `order.paid_at` 为基点按固定偏移表纯函数推算，`elapsed = now - paid_at` 决定已到达节点，全程无随机数（运单号也是 `sha1(order_no)` 生成）。因此同一时刻查询结果可复现、随时间单调递增，评测可以断言。落库的只有 `shipped_at`/`completed_at`/`tracking_no`/`carrier` 这些持久事实，且时间写的是 `paid_at + 固定偏移`而**不是 `now`**——所以无论订单在哪个时刻被查询，落库时间都与轨迹一致。状态推进是**惰性**的：只在查物流时发生（`FOR UPDATE` 行锁 + 状态前置判断实现幂等，一次请求最多连跳两态），不起后台定时任务——无需调度器、无空转扫描、无多实例协调问题。

**补充**：这个惰性推进踩出一个跨模块回归——营收统计原先用 `status == paid` 过滤，订单一旦被推进到 `shipped`/`completed` 就会**静默漏算**，而旧测试数据全是 `paid`，一个都不会红。判据必须改成 `paid_at is not None`：状态是随物流推进的派生量，事实来源是 `paid_at`。已补守门测试（造 shipped/completed 订单断言仍被统计）。

### 3.18 指标口径怎么保证"一个数只算一种算法"

（`app/services/analytics_service.py`）把所有统计口径收敛到唯一一层——`PAID_FILTER`（`paid_at IS NOT NULL`）和营收表达式（`unit_price * quantity`）只在这里定义；Agent 工具、结构化 API、预聚合刷新全部调用它，不允许各写一份 SQL。日期分组沿用 `func.date(Order.paid_at)`，跨品类订单在品类表各计一单，口径与既有工具输出保持一致。

**补充**：口径错误的典型形态不是写错 SQL，而是**两份都对但不一样**——Agent 说 100、看板说 98，用户不再信任数据。统一函数源是防线，但还不够，所以预聚合用同一函数刷新、再加对账测试。

### 3.19 预聚合怎么做到幂等、可重跑

（`app/services/analytics_agg_service.py`）`refresh_daily_agg()` 在事务内先取 `pg_advisory_xact_lock`（防多实例同时刷新互相覆盖），再**删除目标区间、按 service 结果重算插入**。删除 + 插入而不是 upsert，是为了同时保证幂等和**清理脏行**（例如历史修正后某天不该有数据）。对账测试构造跨 3 天 / 2 品类的固定数据，验证「实时 service == 手工期望」，再验证「刷新后聚合表 == 实时 == 期望」，最后再刷一次断言不变、新增订单后刷新能更新。

**补充**：只比较"聚合表 == 实时 service"是不够的——两边都调同一个函数，函数错了会一起错。所以测试里保留一份**手工算出来的期望值**，这才是对账而不是自证。数据量只有 seed 的几笔订单，验证的是**机制与口径**，不是规模；定位是"轻量数据平台"而非"数据中台"。

### 3.20 订单状态事件表解决什么问题？怎么防重复

（`app/services/order_event_service.py` + `order_status_events`）订单主表只有当前状态，无法回答"从下单到支付停留多久""各状态转化率"。事件表按 `(order_id, from_status, to_status, occurred_at, source, actor_user_id, meta)` 记录每次状态变更，漏斗以区间内创建的订单为 cohort 计算到达率与相邻停留时长。幂等靠三层：业务层状态前置判断、`UNIQUE(order_id, to_status)`、插入用 `INSERT ... ON CONFLICT DO NOTHING`——它比"先查再插"更抗并发，也不会因重复推进抛 IntegrityError 回滚主事务。`occurred_at` 写**业务时间**（物流用 `paid_at + 固定偏移`），不是写入时间，所以重跑结果可复现。

**补充**：事件表不能替代订单主表状态——主表是当前事实，事件表是可审计的历史；两者在同一 `FOR UPDATE` 事务里提交，避免"状态变了但没事件"。

### 3.21 评测后台任务为什么说"不是任务队列"

（`app/api/v1/eval.py` + `app/services/eval_service.py`）`POST /eval/runs` 用 FastAPI `BackgroundTasks` 触发，任务跑在 **API 进程内**——进程重启就丢，且 `gunicorn.conf.py` 的 `timeout=60` 可能杀掉耗时数分钟的全量评测。所以设计上不假装它是可靠队列：run 状态落库（pending/running/succeeded/failed）、逐用例单独 commit 并更新 `heartbeat_at`、`reap_stale_runs()` 把超时未心跳的 run 置 failed 释放槽位、case 写入用 `ON CONFLICT DO UPDATE` 保证崩溃重跑不整单失败。**完整评测推荐在 api 容器内用 CLI 跑**；生产化再换独立 worker。

**补充**：单活动 run 用 **partial unique index** 而不是 Redis 锁——`UNIQUE ((true)) WHERE status IN ('pending','running')` 跨进程/跨实例原子生效，不需要续期，进程崩溃也不会留下死锁；Redis 锁的 TTL 反而要处理续期和误删。

### 3.22 CI 为什么只跑一部分测试

（`.github/workflows/ci.yml`）CI 只跑**确定性**三块——backend-unit（单测）；backend-db（`alembic upgrade head` 验证完整迁移链 + 只依赖 Postgres 的集成测试，API 测试 monkeypatch `execute_run` 不真跑 LLM）；frontend-build（`npm ci && npm run build`）。真实 LLM 评测、Qdrant 检索测试、Redis 下单测试和门禁**不在 CI**：它们依赖外部模型/服务、有非确定性和成本，放进去只会得到"随机红绿"的流水线。这些在容器内手动执行，命令写在 [eval.md](eval.md)。

**补充**：这不是"没做 CI"，而是明确边界——CI 保证"代码与迁移不坏"，容器内手动跑保证"模型效果不退化"，两类验证的**确定性、耗时、成本**完全不同，混在一起会让 CI 失去信号价值。

---

## 4. 已知边界

- 后台评测任务不是可靠队列，进程重启会丢，靠数据库状态与心跳兜底；生产环境需要独立 worker。
- CI 只覆盖确定性部分，模型效果回归依赖容器内手动跑评测。
- 单机部署，订单高并发场景尚未压测；行锁方案适合当前规模，高峰需演进为乐观锁/分布式锁。
- 评测集规模有限，验证的是机制与口径，不代表线上分布。