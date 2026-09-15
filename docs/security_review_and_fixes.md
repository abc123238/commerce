# 安全审查与修复记录

- 审查日期：2026-08-11
- 审查范围：后端（FastAPI + LangGraph Agent/RAG）、前端（Vue 3）
- 修复方式：见各节「修复」与「验证」；文档末尾附遗留待办

本次审查共发现 **4 个 HIGH、8 个 MEDIUM、若干 LOW** 问题，已全部修复并补充回归测试。
整体结论：认证核心（bcrypt、参数化 SQL、JWT 显式算法列表、服务端 Decimal 计价）本就可靠，
主要风险集中在**越权访问（角色缺失）**、**并发竞态（缺行锁）** 与 **前端 XSS + token 存储**。

---

## HIGH

### H1 跨租户数据泄露：任意登录用户可查看全局销售数据

**根因**：`/api/v1/analytics` 与 `/api/v1/chat` 仅做登录校验（`get_current_user`），未做角色检查。
`build_agent` 对任何命中「经营分析」意图的用户都绑定 `build_analytics_tools(db)`，
而这些工具聚合的是**全部用户**的已支付订单（无 `user_id` 过滤）。

**修复**：
- `app/api/v1/analytics.py`：路由依赖改为 `Depends(require_roles("admin"))`，普通顾客直接 403。
- `app/agent/graph.py`：`build_agent` 新增 `user_role` 参数；当 `intent == "analytics"` 且 `user_role != "admin"` 时，
  将意图回退为 `customer_service`，只绑定按 `user_id` 隔离的订单/商品工具。
- `app/api/v1/chat.py`：调用 `build_agent` 时透传 `user_role=user.role`。
- `eval/runner.py`：评测经营分析数据集时显式传入 `user_role="admin"`，保证评测用例不受影响。
- `frontend/src/App.vue`：非管理员不再显示「经营分析」导航入口。

**验证**：`app/tests/test_api_auth.py::test_analytics_requires_admin` —— 普通顾客 POST `/api/v1/analytics` 返回 403。

---

### H2 并发取消订单导致库存双回补

**根因**：`cancel_order` 读取订单行时**未加 `FOR UPDATE`**，且状态检查发生在加锁之前。
两次并发取消都能通过 `status == CREATED` 检查，随后各自对商品执行 `stock += quantity`，库存回补两次。

**修复**：`app/services/order_service.py::cancel_order`
- 用 `select(...).where(order_no=..., user_id=...).with_for_update()` 先锁订单行；
- 状态检查移入锁内：后到事务在锁释放后重新读取状态为 `CANCELLED`，直接 400；
- 商品行锁按 `product_id` 升序获取，避免跨订单并发回补时的锁序死锁。

**验证**：`app/tests/test_api_orders.py::test_concurrent_cancel_restores_stock_once` ——
并发两次取消：一个 200、一个 400，库存只回补一次（4→3→4）。

---

### H3 同一购物车并发下单产生重复订单 / 库存双扣

**根因**：`create_order_from_cart` 在加商品锁**之前**读取购物车条目，加锁后未重读。
两个并发请求都读到同一份旧快照，各自创建订单并扣减库存。

**修复**：`app/services/order_service.py::create_order_from_cart`
- 先用 `select(Cart).options(selectinload(Cart.items)).where(user_id=...).with_for_update()`
  锁购物车行，串行化「同一购物车的下单」；
- 后到事务在锁释放后重读，看到已被清空的购物车 → 400「购物车为空」；
- 商品行锁同样改为按 `product_id` 升序获取。

**验证**：`app/tests/test_api_orders.py::test_concurrent_checkout_single_order` ——
并发两次下单：一个 201、一个 400，库存只扣一次（3→1），订单表仅 1 条。

> 备注：`pay_order` 存在同类竞态（重复插入 Payment 触发唯一约束 → 500），一并改为锁定订单行后校验状态。

---

### H4 前端存储型 XSS（v-html 弱清洗）

**根因**：`frontend/src/components/SseChat.vue` 将 LLM/知识库返回内容经 `marked` 渲染后直接 `v-html`。
手写 `sanitize()` 仅剥离双引号 `on\w+="..."` 与 script/style/iframe，单引号事件属性、SVG 事件、
`javascript:` 协议等均可存活；RAG 文本来自数据库（商品描述、订单数据），可被污染 → 存储型 XSS。

**修复**：
- `frontend/package.json`：新增 `dompurify` 依赖；
- `frontend/src/components/SseChat.vue`：`renderMd` 改为 `DOMPurify.sanitize(marked.parse(...))`，
  删除手写 `sanitize()`。DOMPurify 默认白名单会剥除全部 `on*` 事件属性、`javascript:` 协议与危险标签。

**验证**：`npm run build` 通过；DOMPurify 白名单不产出可执行事件属性。

---

## MEDIUM

### M1 JWT 存储于 localStorage（可被 XSS 窃取）

**根因**：前端把 `access_token` 写入 `localStorage`，任何 XSS（如 H4）都能直接读取。

**修复**（前端不再持有 token，会话改由 HttpOnly cookie 承载）：
- `app/core/security.py`：新增常量 `ACCESS_TOKEN_COOKIE`；
- `app/api/v1/auth.py`：`login/register/refresh` 在响应中种下 HttpOnly cookie
  （`httponly=True, samesite="lax", secure=settings.cookie_secure`）；新增 `POST /auth/logout` 清除 cookie；
- `app/api/deps.py`：`get_current_user` 优先从 cookie 取 token，`Authorization` header 兜底（兼容脚本/测试）；
- `frontend/src/api/client.js`：移除 localStorage token 读写；fetch 统一 `credentials: 'same-origin'`；
  新增 `readUser/saveUser/clearUser/probeAuth/logout` 帮助函数；
- `frontend/src/router/index.js`：守卫改为「本地有 user 则放行，否则静默探测 `/auth/me`」；
- `frontend/src/views/Login.vue`、`frontend/src/App.vue`：适配 cookie 会话与 `probeAuth`；
- `frontend/src/api/sse.js`：SSE 请求不再手拼 Authorization，改用 cookie。

**验证**：`app/tests/test_api_auth.py::test_cookie_auth_flow` —— 登录后仅凭 cookie 访问 `/auth/me` 成功。

> 生产部署必须设置 `COOKIE_SECURE=true`（HTTPS），否则 cookie 明文传输。

### M2 JWT 默认密钥 "change-me"

**修复**：`app/config.py` 移除 `jwt_secret_key` 的默认值（改为必填），未配置时启动即报错；
`.env.example` 补充生成强密钥的指引。

### M3 `.env` 泄露真实密钥 / DEBUG 带入生产

**修复**：
- `app/config.py` 新增 `_guard_prod_debug` 校验器：`env` 非 `dev/test/local` 时禁止 `DEBUG=true`；
- `.env` 中的 `ANTHROPIC_AUTH_TOKEN`（DeepSeek key）已在会话中暴露，**需用户自行轮换**，见「遗留待办」。

### M4 登录/注册无限流

**修复**：`app/api/v1/auth.py` 的 `register/login/refresh` 挂载 `@limiter.limit(f"{settings.rate_limit_public}/minute")`，
未登录请求以 IP 为维度限流，抑制爆破与批量注册。

### M5 SSE 无心跳、无断开清理

**修复**：
- `app/api/v1/chat.py`、`app/api/v1/analytics.py`：`EventSourceResponse` 加 `ping=15` 心跳注释帧；
  `event_generator` 增加 `finally`，客户端断开时 astream 关闭并取消 LLM 调用；
- `frontend/src/api/sse.js`：增加 90s 空闲超时（abort + onError）；流正常关闭但未收到 `done/error` 时
  复位 `running`（onError「连接已中断」）；
- `frontend/src/components/SseChat.vue`：`onUnmounted` 终止未完成请求并清理打字机 `setInterval`。

### M6 Langfuse 未指定 host，trace 默认发往云端

**修复**：`app/observability/langfuse.py` 的 `CallbackHandler` 显式传入
`host=settings.langfuse_host`（并补齐 `secret_key`）。

### M7 同步 embedding 阻塞事件循环

**修复**：`app/rag/vectorstore.py` 中 `embed_query` / `sparse_vector` / `embedding_dim`
等同步 CPU 密集调用改为 `asyncio.to_thread(...)`，不再阻塞 async 请求。

### M8 LLM 请求无超时

**修复**：`app/agent/llm.py` 的 `_llm_kwargs` 增加 `timeout=settings.llm_timeout_seconds`（默认 60s）；
`app/config.py` 新增 `llm_timeout_seconds` 字段。

---

## LOW（已一并修复）

| 问题 | 修复 |
|---|---|
| 畸形 JWT（缺 sub/非数字）导致 500 | `app/api/deps.py` 对 `int(payload["sub"])` 加 try，返回 401 |
| 注册重复邮箱 409 造成用户枚举 | `app/api/v1/auth.py` 改为 400 + 通用文案；测试同步更新 |
| `/health` 泄露 env | `app/main.py` 仅返回 `{"status":"ok"}` |
| Agent 工具循环可重复空转 | `app/agent/state.py` 增 `executed_tools` 字段；`nodes/core.py` 对相同「工具+参数」去重，提示模型勿重复调用 |
| citation 正则误匹配 markdown 链接 | `app/rag/citation.py` 正则改为 `\[(\d+)\](?!\()` |
| 答案文本为普通字符串时走通用兜底 | `app/agent/nodes/core.py::answer_node` 改用 `extract_text`（兼容 str 与块列表） |
| 购物车快速加减竞态 | `frontend/src/views/Cart.vue` 对每行加 `pending` 集合，请求进行中禁用输入 |
| 取消对话框拒绝产生 unhandled rejection | `Cart.vue` / `Orders.vue` 对 `ElMessageBox.confirm` 包 try/catch |
| 搜索不重置页码 | `frontend/src/views/Products.vue` 新增 `search()` 重置 `page=1` |
| 路由守卫只查 token 存在不校验有效性 | `frontend/src/router/index.js` 改为 `/auth/me` 静默探测 |
| App.vue 对 localStorage JSON 未防御性解析 | 由 `readUser()` 统一捕获解析异常 |

> `/metrics` 端点保持无鉴权公开（供 Prometheus 抓取），不暴露业务数据，未改动。

---

## 遗留待办（需人工处理）

1. **轮换 `.env` 中的 `ANTHROPIC_AUTH_TOKEN`**（DeepSeek API key `sk-fdd17e…` 已在审查会话中暴露，
   无论是否入库都应视为已泄露，请到 DeepSeek 控制台重新生成并更新 `.env`）。
2. **生产部署检查**：设置强 `JWT_SECRET_KEY`、`COOKIE_SECURE=true`、`DEBUG=false`、`ENV=production`。
3. **可选增强**：refresh token 轮换（当前失窃后 7 天内仍可用）；会话空闲自动刷新 access token。

## 测试与验证

```bash
# 纯单测（无需外部服务）
.venv/Scripts/python.exe -m pytest -m "not integration" -q

# 全部测试（需 Postgres / Redis / Qdrant 已启动）
.venv/Scripts/python.exe -m pytest -q          # 43 passed

# 前端构建（DOMPurify 依赖已装入）
cd frontend && npm run build
```
