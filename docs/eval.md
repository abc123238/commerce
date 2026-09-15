# 评测体系（eval）

## 1. 目标

用**真实 agent 运行 + 真实检索**衡量系统质量，而不是"模型好即系统好"。每个用例都跑一遍完整的 LangGraph 图 / 混合检索，再按数据集类型计算指标，最终产出 `eval/report.md`。

## 2. 数据集

位于 `eval/datasets/*.jsonl`，三类：

| 数据集 | 当前规模 | 一条用例长什么样 |
|---|---|---|
| `retrieval.jsonl` | 10 | `{question, golden_doc_ids}` —— 该问题应召回哪些知识块 |
| `customer_service.jsonl` | 5 | `{question, expected_tools, key_fields, golden_refs?, requires_citation}` —— 客服应调哪些工具、覆盖哪些字段、是否必须引用 |
| `analytics.jsonl` | 4 | `{question, expected_tools, key_fields, requires_citation}` —— 经营分析应调的工具序列与关键字段 |

字段说明：

- `expected_tools`：golden 工具序列（有序），用于**工具序列匹配**。
- `key_fields`：答案必须覆盖的关键信息点，用于**关键字段覆盖**（人工标注 + 字符串/数字核对）。
- `golden_refs`：该问题应引用的知识块集合。
- `requires_citation`：该问题是否强制要求带引用（如退换政策类问题 = true；查订单这类工具型问题 = false，实时数据不加引用）。

## 3. 指标

### 检索（retrieval 集）

| 指标 | 定义 |
|---|---|
| Recall@k | 召回的 golden 文档数 / golden 文档总数（k = RAG_TOP_K = 6） |
| MRR | golden 文档在结果列表中最靠前的名次的倒数均值 |

### Agent（customer_service / analytics 集）

| 指标 | 定义 |
|---|---|
| tool_sequence_match | 实际调用工具序列与 `expected_tools` 是否一致（顺序敏感） |
| key_fields_covered | 答案覆盖的关键字段比例 |
| citation_precision | 答案中 `[n]` 引用指向合法检索块的比例。**保守语义**：无引用视为无编造引用 → 得 1.0 |
| citation_presence | 需要引用的问题是否带引用。**宽松语义**：`requires_citation=false` 时无论是否引用都不得 1.0，只惩罚"该引用而未引用" |
| correctness | LLM-as-judge 对答案正确性打分（0-1） |
| faithfulness | LLM-as-judge 对"答案是否忠实于检索与工具结果"打分（0-1） |

### 引用语义（关键设计）

- `[n]` **只能指向检索到的知识块**（`retrieved` 列表）；工具返回的实时数据（价格 / 库存 / 订单状态 / 优惠计算）**不加引用**——这是 answer 节点提示词的硬规则。
- `strip_invalid_citations` 在 answer 节点剥离越界的悬空 `[n]`（防止模型幻觉出知识块编号）。
- 评测的引用指标与上述规则对齐：工具型问题 `requires_citation=false`，不惩罚"未引用"。

## 4. LLM-as-judge

`eval/judge.py` 用独立的 judge LLM（DeepSeek 推理模型，`thinking={"type": "disabled"}` 强制纯文本输出）打分，一次失败自动重试一次，解析失败降级为 0 分并告警。

实现要点：

- `get_judge_llm()` 独立于业务 LLM，`max_tokens=4096` + 禁用 thinking 块，避免"思考占满预算导致空输出"。
- `extract_text(resp.content)` —— 必须传 AIMessage 的 `content`，而不是整个消息对象。
- 输出 JSON：`{"correctness": 0.x, "faithfulness": 0.x}`，带 code fence 兜底剥离。

## 5. 运行与产物

```bash
uv run python -m eval.runner
```

- 产物：`eval/report.md`（人类可读）+ `eval/report.json`（结构化，可接入 CI）。
- 可选回写：`eval/config.py` 置 `WRITE_BACK_LANGFUSE=true` 且 Langfuse 已启用时，评测 trace 带 `eval` 标签回写，方便在控制台对比 badcase。
- 冒烟控制：`MAX_AGENT_CASES=5` 限制每个 agent 数据集最大运行条数（LLM 成本考虑）。

**扩容评估规模**（简历更硬的做法）：

1. `retrieval.jsonl`：无需 LLM，直接补到 30-50 条，成本极低。
2. `customer_service / analytics`：增加真实业务问题（覆盖不同工具组合、边界问题），并把 `MAX_AGENT_CASES` 调大。
3. 定期全量重跑，把 `report.md` 归档进 git，形成"指标随版本演进"的记录。

## 6. 当前基线（2026-08-01）

| 数据集 | 指标 | 数值 |
|---|---|---|
| retrieval | recall@k | 95% |
| retrieval | MRR | 90% |
| customer_service | tool_sequence_match | 100% |
| customer_service | key_fields_covered | 100% |
| customer_service | citation_precision / presence | 100% / 100% |
| customer_service | correctness / faithfulness | 98% / 100% |
| analytics | tool_sequence_match | 86.7% |
| analytics | key_fields_covered | 100% |
| analytics | correctness / faithfulness | 82.5% / 87.5% |

分析集正确性低于客服的原因：多步聚合 + 数值计算对 LLM 精确度要求更高，是后续优化（更强的 few-shot / 计算节点后置校验）的目标区域。
