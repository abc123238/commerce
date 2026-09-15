# 可观测性设计

## 1. 目标

**一次请求从 HTTP 到 Agent 每一步都可追溯**。三类信号各司其职：

| 信号 | 载体 | 回答的问题 |
|---|---|---|
| 追踪（Tracing） | Langfuse + `X-Trace-ID` | 这次 agent 运行经历了哪些节点、调了哪些工具、耗时多少 |
| 日志（Logging） | structlog JSON | 每条日志属于哪个 trace、哪个用户、哪一步 |
| 指标（Metrics） | Prometheus / Grafana | 系统整体状态：QPS、延迟分位、限流拒绝、工具调用分布 |

## 2. 链路追踪（Tracing）

### trace_id 的生成与贯穿

```
HTTP 请求进入
  └─ TraceMiddleware.dispatch
       ├─ 取请求头 X-Trace-ID，无则 new_trace_id()（uuid4.hex）
       ├─ set_trace_id() 写入 ContextVar
       ├─ structlog.contextvars.bind_contextvars(trace_id=...)
       ├─ call_next() 处理业务
       └─ 响应头回传 X-Trace-ID（curl 可直接看到全链路标识）
```

- **ContextVar**：同一请求的异步上下文内共享，日志与中间件据此串联。
- **agent run**：chat / analytics 接口把 `get_trace_id()` 作为 `run_id` 传入 `build_agent`，并在 Langfuse 回调里作为 trace_id。

### Langfuse 集成（可插拔）

```python
def make_langfuse_callbacks(run_id, user_id=None) -> list | None:
    if not settings.langfuse_enabled: return None        # 开关
    if not keys configured: return None                  # 缺密钥自动降级
    handler = CallbackHandler(public_key=..., trace_context={"trace_id": run_id})
    return [handler]
```

- 通过 `trace_context={"trace_id": run_id}` 让 Langfuse trace 关联到我们的 HTTP trace_id。
- **降级策略**：`langfuse_enabled=false`、缺密钥或网络不可达时返回 `None`，graph 照常运行，仅少一条外部 trace——主链路零阻塞。
- 控制台每轮对话一条 trace，展开即为：route → retrieve → reason → execute_tool ×N → answer 的嵌套 span。

## 3. 日志（structlog JSON）

- 中间件输出 `request` 事件：`method / path / status / duration_ms / trace_id`。
- agent 节点输出 `tool_call` 事件：`run_id / tool / args / result_preview`。
- chat / analytics 接口输出 `chat_start / analytics_start`：`run_id / user_id / question`。
- 统一 JSON 格式，可直接对接 ELK / Loki；本地开发按需美化。

## 4. 指标（Prometheus）

| 指标 | 类型 | 维度 | 埋点位置 |
|---|---|---|---|
| `http_requests_total` | Counter | method / path / status | TraceMiddleware |
| `http_request_duration_seconds` | Histogram | method / path | TraceMiddleware |
| `agent_runs_total` | Counter | intent | `run_agent` |
| `retrievals_total` | Counter | – | `retrieve_node` |
| `tool_calls_total` | Counter | tool | `execute_tool_node` |
| `rate_limited_total` | Counter | – | `/metrics` 异常处理器（慢限流拒绝） |

- `/metrics` 端点由 `app.main` 挂载，`render_metrics()` 返回 `generate_latest()`。
- Prometheus 按 `monitoring/prometheus.yml` 抓取 `api:8000`；Grafana 自动 provisioning 一个总览仪表盘（QPS / P50/P90/P99 / agent 运行 / 检索 / 工具分布 / 限流）。

## 5. SSE 流式（agent 过程可见）

chat / analytics 用 `sse-starlette` 流式返回，事件：

```
message: {"type":"step",  "node":"retrieve",       "detail":"混合检索召回 6 个知识块"}
message: {"type":"step",  "node":"reason",         "detail":"调用工具 1 个"}
message: {"type":"step",  "node":"execute_tool",   "detail":"执行 1 个工具：query_order"}
message: {"type":"answer","content":"您的订单正在配送中……","citations":[...]}
message: {"type":"done",  "trace_id":"5571966e2d6e4b57a3cbd392c15e4bca"}
```

生产化三件套：`Cache-Control: no-cache`、`X-Accel-Buffering: no`（反 nginx 缓冲）、`Connection: keep-alive`。

## 6. 排查示例

一次"回答慢/不准"的排查路径：

1. curl 拿 `X-Trace-ID`；
2. 日志里 grep 该 trace_id → 看每步耗时；
3. Langfuse 打开该 trace → 看 LLM 输入输出、工具参数与返回；
4. Prometheus 查 `tool_calls_total` 分布 → 判断是不是某工具频繁被调；
5. 若召回不相关 → 查 `retrievals_total` 频率与知识库语料。
