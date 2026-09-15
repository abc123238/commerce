# 面试准备手册（AI 电商平台）

> 用法：① 先能**不看代码**把「三场景走查」完整讲一遍；② 再对 15 个追问逐个答，答完看「追问纵深」模拟被深挖；③ 最后过「自测清单」自查。每处标注了代码位置，被追问到细节时能定位。

---

## 1. 一分钟自我介绍（开场白）

> 这是一个**生产级电商平台**，业务闭环（鉴权 / 商品 / 购物车 / 订单 / 模拟支付）是主体，AI 智能体是核心功能模块。两个 AI 能力：
> - **AI 客服**：RAG 检索商品知识库 + 工具调用**打穿真实业务库**——用户问"我的订单到哪了"，agent 会实时查这个登录用户自己的订单，回答带知识库引用，不是编的。
> - **经营分析智能体**：多步工具调用（查销售汇总 → 算环比增长 → 畅销榜）输出结构化报告。
>
> 配套工程化：JWT + RBAC、限流、SSE 流式、Docker 多阶段部署、Langfuse 全链路可观测、Prometheus 指标、自研评测套件。技术栈 FastAPI + SQLAlchemy async + PostgreSQL + Redis + Qdrant + LangGraph。

**面试官如果问"这项目解决了什么真实问题"**：在线购物全流程（下单支付的完整闭环）+ 用 AI 解决"客服查订单 / 看经营数据"这两个真实低效场景。

---

## 2. 三个场景数据流走查

### 场景 A：注册登录（JWT 流转）

1. `POST /api/v1/auth/register` → 校验邮箱唯一 → `bcrypt.hashpw` 加密存库（`app/core/security.py:20`）。
2. `POST /auth/login` → `verify_password` → `create_access_token`（payload 含 `sub`=user_id、`role`、`type=access`、`exp`，HS256 签名，30 分钟）+ `create_refresh_token`（7 天）→ 返回给前端。
3. 前端把 `access_token` 存 localStorage，后续请求带 `Authorization: Bearer xxx`。
4. 受保护接口用 `Depends(get_current_user)`（`app/api/deps.py:11`）：解码 JWT → 校验 `type=access` → 查库返回 User。
5. **RBAC**：admin 接口（商品管理、知识库入库）用 `Depends(require_roles("admin"))`，检查 `user.role`，非 admin 返回 403。

追问点：refresh token 怎么用？`POST /auth/refresh` 轮换换新 access。JWT 为什么能无状态？验签即可，服务端不存 session。

### 场景 B：下单支付（订单状态机 + 并发防超卖）

1. `POST /orders` → `order_service.create_order_from_cart`（`app/services/order_service.py:46`）：
   - 读当前用户购物车 → **对每个商品 `SELECT ... FOR UPDATE` 行锁**（防并发超卖）
   - 校验商品在售 + 库存充足
   - 建 `orders` + `order_items` 快照（记录下单时的 name/price，后续改价不影响已下订单）
   - 扣库存 → 清空购物车 → 统一 `commit`（事务原子性：任一步失败整体回滚）
2. 订单号 `"ORD" + token_hex(8).upper()`。
3. `POST /orders/{no}/pay` → `pay_order`：校验状态必须 `created` → 置 `paid` + 写 `payments` 记录（`method="mock"`，模拟支付）。
4. 状态机：`created → paid → completed`，`created ⇄ cancelled`（未支付可取消）。

追问点：为什么用行锁而不是乐观锁？行锁在事务内串行化库存扣减，实现简单且对低并发足够；乐观锁适合高并发但需重试。

### 场景 C：AI 客服问"我的订单到哪了"（全链路，面试必考）

前端（Vue3）用 `fetch + ReadableStream` 发起 **POST** `/api/v1/chat`（SSE 流式，`app/api/v1/chat.py`）：

1. **中间件**（`app/observability/middleware.py`）生成 `trace_id`（或沿用请求头 `X-Trace-ID`）→ 写入 ContextVar + structlog 上下文 → 响应头回传。
2. `build_agent`（`app/agent/graph.py:70`）：
   - `classify_intent(question)` 识别意图（关键词加权纯函数：分析词 +1、客服词 -1，>0 为 analytics）
   - 客服工具集 = `build_order_tools(db, user_id)` + `build_product_tools(db)`（**闭包绑定当前用户**）
   - 组装 `ToolRegistry` → 构建 LangGraph StateGraph
3. **graph 执行**（`stream_mode="updates"`，每步产出 SSE `step` 事件）：
   - `route`：意图识别
   - `retrieve`：`hybrid_search` 召回 6 个知识块（dense + jieba sparse + RRF），拼进 system/history/知识块/问题 的提示词
   - `reason`：`ChatAnthropic.bind_tools(schemas)`，LLM 决策——该问题决定调用 `query_order` 工具
   - `execute_tool`：`registry.execute` → `asyncio.wait_for` 10s 超时 + 2000 字符截断 → `ToolMessage` 回填（**SQL 里 `where Order.user_id == user_id`，只能查自己**，`app/agent/tools/order_tools.py:21`）
   - `reason`：LLM 汇总"检索资料 + 工具返回的真实订单"组织回答
   - `answer`：抽取回答里 `[n]` → 剥离越界悬空引用 → 拼接参考资料（`app/agent/nodes/core.py:131`）
4. 每步经 Langfuse `CallbackHandler` 记为 span；结束 `yield done(trace_id)`，前端据此在日志/控制台关联全链路。

**核心卖点**：工具返回的是**真实数据库里的实时订单**（价格/状态），知识库引用 `[n]` 只指向检索到的知识块，两者来源清晰不混淆。

---

## 3. 高频追问 15 题

### Q1 为什么用 PostgreSQL 不用 MySQL？
**答案**：最直接原因是 LangGraph 官方 checkpoint 后端用 Postgres，AI 编排选型决定了数据库选型；另外 asyncpg 是全栈 async 的成熟 driver、jsonb 承载 agent 的半结构化数据、留了 pgvector 演进空间。
**纵深**：MySQL 做基础 CRUD 也没问题，差异在 AI 生态衔接；面试重点是"选型跟技术栈咬合"，不是哪个更好。

### Q2 为什么 Qdrant 不用 pgvector？
**答案**：业务库和向量库职责分离，避免向量检索拖累 OLTP；Qdrant 原生支持**命名向量 + FusionQuery(RRF)**，一条查询完成 dense + sparse 混合融合，pgvector 做混合检索要手写两段再融合。
**纵深**：如果上线且量不大，pgvector 能少一个组件——这是可以说的"演进方向"。

### Q3 混合检索具体怎么做？
**答案**（`app/rag/vectorstore.py:78`）：对同一文本算两个向量存一个点——dense 用 fastembed（jina-embeddings-v2-base-zh，768 维余弦距离），sparse 用 **jieba 分词** + 词哈希到 10 万维的稀疏向量（`app/rag/sparse.py`，中文必须预分词，否则 BM25 按空白切分整句成一个 token）。查询时两条 prefetch（各取 top-k×3）喂给 `FusionQuery(RRF)` 融合取 top-6。
**纵深**：RRF 是 rank-based 融合，不依赖分数归一化，dense 和 sparse 分数量纲不同也能直接融合。

### Q4 怎么防止 AI 编造引用？
**答案**（`app/rag/citation.py` + `app/agent/nodes/core.py:142`）：回答里 `[n]` 必须落到**检索到的知识块**上；answer 节点先 `strip_invalid_citations` 把越界的 `[n]` 剥掉，再 `extract_cited_indices` 只保留有效引用并拼参考资料。工具返回的实时数据（订单/库存/价格）**不允许加引用**，防止把实时数据误标成知识库来源。
**纵深**：评测里 citation 相关指标用"无引用视为无编造"的保守语义——宁可少引用不错引用。

### Q5 怎么保证工具只查当前用户的数据？
**答案**（`app/agent/tools/order_tools.py`）：`build_order_tools(db, user_id)` 用**闭包**把 user_id 绑进工具实现，SQL 查询强制 `where Order.user_id == user_id`；工具集在 `build_agent` 时按当前请求的用户构造，请求间天然隔离。
**纵深**：这属于"AI 安全约束"，和 RAG 知识库的静态引用是两个不同数据源，权限边界在 SQL 层而非 LLM 提示词层。

### Q6 为什么单 Agent 而不是多 Agent？
**答案**（`app/agent/graph.py`）：客服和经营分析**共享同一张图**，只在 `route` 节点按意图分流——analytics 跳过检索直接走工具推理，客服走 RAG；工具集也按意图组装。单图让可观测链路简单（一条 trace 贯穿），避免多 Agent 的编排成本。
**纵深**：多 Agent 在"角色明显不同、需要并行或隔离上下文"时才有收益，当前需求是负收益。

### Q7 trace_id 怎么贯穿全链路？
**答案**（`app/observability/middleware.py` + `app/core/trace.py`）：HTTP 中间件生成/沿用 `trace_id` → 写进 **ContextVar**（异步上下文内共享）→ bind 到 structlog 上下文（所有日志带 trace_id）→ 响应头回传 `X-Trace-ID`。agent 层把 `trace_id` 作为 run_id 传入，Langfuse 回调以它为 trace 关联，Graph 每步自动成 span。前端 SSE 的 `done` 事件把 trace_id 返回给用户，任何一步可在日志/控制台按它检索。
**纵深**：ContextVar 解决的是"async 上下文传递"，比传参干净；structlog 的 contextvars 绑定让每一行 JSON 日志都带追踪 ID。

### Q8 Langfuse 挂了/没配怎么办？
**答案**（`app/observability/langfuse.py`）：`make_langfuse_callbacks` 先检查 `langfuse_enabled` 和 key，不满足直接返回 `None`，主链路照常跑——可观测降级为 **structlog 日志 + Prometheus 指标**，不阻塞业务。
**纵深**：这是"可观测设施可插拔"的设计原则，面试可以讲成"基础设施不可用不能让业务承担连带故障"。

### Q9 Prometheus 多进程指标怎么合并？
**答案**（`gunicorn.conf.py` + `app/observability/metrics.py`）：gunicorn 4 worker 各自进程内计数，直接抓取会丢数据。方案：启动时设 `prometheus_multiproc_dir`，`prometheus_client` 把指标写成**每 worker 一个 .db 文件**；`/metrics` 端点用 `MultiProcessCollector` 聚合所有 worker 的文件；`child_exit` 里 `mark_process_dead(pid)` 标记退出 worker 的文件防陈旧数据。
**纵深**：单进程开发（uvicorn/pytest）不设该 env，走默认 Registry，行为一致。这个"多进程 + 计数器"的坑是生产部署真实踩过的。

### Q10 SSE 是怎么实现的？为什么不用 WebSocket？
**答案**：服务端 `sse_starlette.EventSourceResponse`，`graph.astream(stream_mode="updates")` 把每步 `{type: step/answer/done/error}` 序列化成 SSE 事件流回；响应带反缓冲头（`Cache-Control: no-cache`、`X-Accel-Buffering: no`）防止代理/nginx 吞流。**前端**是难点：接口是 POST，浏览器 `EventSource` 只支持 GET，所以前端用 `fetch + ReadableStream` 逐行解析（`frontend/src/api/sse.js`）。
**纵深**：SSE 适合"服务端→客户端单向流"（正是 agent 进度/回答），WebSocket 适合双向实时交互；这里用 SSE 更轻、天然兼容 HTTP 缓存和代理。

### Q11 下单的并发安全怎么保证？
**答案**（`app/services/order_service.py:56`）：对购物车每个商品 `SELECT ... FOR UPDATE` **行锁**串行化库存扣减，同一商品并发下单时后到者等锁，然后看到更新后的库存，不足则报"库存不足"；整笔下单（建订单 + 快照 + 扣库存 + 清购物车）在一个事务里 `commit`，任一步失败整体回滚。
**纵深**：快照（order_items 记录下单时价格）保证后续改价不影响历史订单；这是电商领域"下单一致性"的经典考点。

### Q12 限流怎么做的？
**答案**（`app/core/rate_limit.py`）：slowapi + Redis 存储，`key_func` 优先按 JWT 的 `sub`（登录用户）限流，未登录回退 IP；AI 接口 `/chat` `/analytics` 20 次/分/用户，其余公开接口 100 次/分，超限返回 429 + `Retry-After`。
**纵深**：按用户而非单纯 IP，避免代理后面多人共享 IP 被误伤；AI 接口限得更严是因为 LLM 调用成本高。

### Q13 测试覆盖了什么？
**答案**：pytest 39 个用例，覆盖鉴权（JWT/RBAC）、订单状态机、意图分类、引用提取/校验、混合检索、LangGraph 图执行、FastAPI TestClient 集成（含 SSE）。`uv run pytest -q` 一条命令回归。
**纵深**：意图分类是纯函数所以可单测；引用校验测"越界 `[n]` 被剥离"这类边界。

### Q14 评测是怎么做的？
**答案**（`eval/`）：三个数据集——customer_service（工具序列 + 引用）、analytics（工具序列 + 结果）、retrieval（golden_doc_ids）。指标：检索 Recall@k/MRR、引用存在率/精确率、工具序列匹配、回答正确性/忠实度（LLM-as-judge）。`uv run python -m eval.runner` 出 `report.md` 并回写 Langfuse 打标。当前：检索 recall@k 95%/MRR 90%，客服正确性 98%/忠实度 100%，分析正确性 82.5%/忠实度 87.5%。
**纵深**：这是"把 AI 系统当工程来做"的体现——不只看能不能跑，看指标能不能度量、能不能回归。

### Q15 如果要上线，你会改什么？
**答案**：① 业务库直接换 pgvector 省一个组件（或扩 Qdrant 集群）；② 订单行锁在高峰改**乐观锁/分布式锁**并加压测；③ agent 的 `max_steps=6` 和工具超时按线上实测调；④ checkpoint 用 Postgres 持久化多轮记忆；⑤ 补错误监控告警（把 Prometheus 指标接 Alertmanager）；⑥ 前端包体积优化（Element Plus 按需引入，现在全量 1.2MB）。
**纵深**：面试官要的不是"完美方案"，而是"知道哪里是短板 + 有改进思路"，说 3~4 个具体的就够。

---

## 4. 自测清单

能**不看代码**讲出以下内容即视为掌握：

- [ ] 画出架构分层（HTTP / 业务 / Agent / RAG / 可观测）并说明每一层职责
- [ ] 画出 LangGraph 图（route → retrieve/reason → execute_tool 循环 → answer）和两条条件边的判定逻辑
- [ ] 讲清"AI 客服问订单"完整数据流（从 SSE 进到带引用回答出），包括工具如何读真实 DB 且只读自己的
- [ ] 讲清混合检索：dense/sparse 各是什么、RRF 怎么融合、中文为什么要 jieba 预分词
- [ ] 讲清引用防编造机制：`[n]` 怎么校验、越界怎么剥离、工具实时数据为什么不能加引用
- [ ] 讲清 trace_id 全链路（中间件 → ContextVar → structlog → Langfuse → 响应头）和 Langfuse 可插拔降级
- [ ] 讲清 Prometheus 多进程合并原理（multiproc_dir / MultiProcessCollector / child_exit）
- [ ] 讲清订单状态机和并发防超卖（行锁 + 事务原子性 + 快照）
- [ ] 讲清 SSE 为什么前端要用 fetch 流式而不是 EventSource
- [ ] 说出 39 个测试覆盖的类别、评测三数据集和当前指标数字
- [ ] 用 30 秒讲完"项目是什么 + 三个亮点"

**卡壳了**：先回对应 Q/A 或代码位置，能复述后再练一遍，直到不卡。

---

## 5. 被问"这是 AI 写的吧"怎么答

不要否认，也不要只甩锅给 AI。用"AI 辅助编码 + 我掌握架构"的说法：

> 对，编码过程用了 AI 辅助提效——现在 AI 写代码很普遍。但这个项目的**架构设计、技术选型、每个关键决策**（为什么 Qdrant、为什么单 Agent、引用校验怎么防编造、工具权限怎么隔离）都是我梳理清楚的。你随便问哪一层，我都能讲清楚它的设计和取舍。

然后**用 Q1~Q15 证明**。面试官要的不是"逐行手写"，而是"你能不能 hold 住这套系统的解释权"——这正是这份手册帮你练的东西。
