"""评测报告生成：将 report 字典渲染为可读 markdown。"""
from __future__ import annotations

_METRIC_LABELS = {
    "recall@k": "Recall@k",
    "mrr": "MRR",
    "recall@k_before": "改写前 Recall@k（仅多轮）",
    "mrr_before": "改写前 MRR（仅多轮）",
    "tool_sequence_match": "工具序列匹配",
    "key_fields_covered": "要点覆盖",
    "citation_precision": "引用精确率",
    "citation_presence": "引用按需",
    "correctness": "正确性",
    "faithfulness": "忠实度",
    "behavior_match": "行为符合预期",
    "safety_pass": "无敏感内容泄露",
    "latency_ms_p50": "延迟 p50",
    "latency_ms_p95": "延迟 p95",
    "latency_ms_avg": "延迟均值",
    "latency_ms_max": "延迟最大值",
    "input_tokens": "输入 token",
    "output_tokens": "输出 token",
    "total_tokens": "总 token",
    "avg_tokens_per_case": "单用例平均 token",
    "cost_usd": "成本估算（USD）",
}

_LATENCY_KEYS = {"latency_ms_p50", "latency_ms_p95", "latency_ms_avg", "latency_ms_max"}
_TOKEN_KEYS = {"input_tokens", "output_tokens", "total_tokens", "avg_tokens_per_case"}

_SECTIONS = [
    ("retrieval", "检索质量"),
    ("customer_service", "AI 客服"),
    ("analytics", "经营分析"),
    ("robustness", "鲁棒性与安全"),
]

_EXPLAIN = [
    "- **Recall@k / MRR**：混合检索对 golden 文档的召回率与排名质量。",
    "- **工具序列匹配**：agent 实际调用工具序列与期望序列的相似度（1.0 完全一致）。",
    "- **要点覆盖**：回答是否包含期望的关键字段/数字。",
    "- **引用精确率**：回答中 [n] 引用是否落在检索资料范围内（识别编造引用；无引用视为无编造）。",
    "- **引用按需**：需要引用时是否按要求给出引用（不需要时不惩罚多余引用，其有效性由引用精确率把关）。",
    "- **正确性 / 忠实度**：LLM-as-judge 评估（0~1）。",
    "- **行为符合预期**：鲁棒性用例回答是否符合 refuse / clarify 预期（关键词启发式，仅作回归信号）。",
    "- **无敏感内容泄露**：回答中未出现 forbidden_fields 标注的敏感串（1.0 为未泄露）。",
    "- **延迟 / token / 成本**：只统计成功用例；延迟为单用例端到端耗时，token 含 agent 与 judge 两次调用，成本按配置单价估算。",
]


def _fmt(metric: str, v) -> str:
    if metric in _LATENCY_KEYS:
        return f"{v:.0f} ms"
    if metric in _TOKEN_KEYS:
        return f"{v:,.0f}"
    if metric == "cost_usd":
        return f"${v:.4f}"
    return f"{v * 100:.1f}%"


def build_markdown(report: dict) -> str:
    lines = ["# AI 评测报告", ""]
    counts = "、".join(
        f"{title}{report.get(key, {}).get('count', 0)} 条"
        for key, title in [
            ("retrieval", "检索"),
            ("customer_service", "客服"),
            ("analytics", "经营分析"),
            ("robustness", "鲁棒性"),
        ]
    )
    lines.append(f"- 生成时间：{report.get('generated_at', '')}")
    summary = report.get("summary")
    if summary:
        lines.append(
            f"- 用例数：{counts}；成功 {summary.get('scored_count', 0)} 条，"
            f"错误 {summary.get('error_count', 0)} 条"
        )
    else:
        lines.append(f"- 用例数：{counts}")
    gate = report.get("gate")
    if gate:
        lines.append(f"- 门禁：{'通过' if gate.get('passed') else '未通过'}")
    lines.append("")

    for key, title in _SECTIONS:
        section = report.get(key, {})
        lines.append(f"## {title}")
        lines.append("")
        if not section.get("count"):
            lines.append("（无用例，请先 seed / ingest_kb）")
            lines.append("")
            continue
        lines.append("| 指标 | 得分 |")
        lines.append("| --- | --- |")
        for metric, value in section.items():
            if metric not in _METRIC_LABELS or value is None:
                continue
            lines.append(f"| {_METRIC_LABELS[metric]} | {_fmt(metric, value)} |")
        lines.append("")

    lines.append("## 指标说明")
    lines.append("")
    lines.extend(_EXPLAIN)
    return "\n".join(lines)
