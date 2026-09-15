"""评测报告生成：将 report 字典渲染为可读 markdown。"""
from __future__ import annotations

_METRIC_LABELS = {
    "recall@k": "Recall@k",
    "mrr": "MRR",
    "tool_sequence_match": "工具序列匹配",
    "key_fields_covered": "要点覆盖",
    "citation_precision": "引用精确率",
    "citation_presence": "引用按需",
    "correctness": "正确性",
    "faithfulness": "忠实度",
}

_SECTIONS = [
    ("retrieval", "检索质量"),
    ("customer_service", "AI 客服"),
    ("analytics", "经营分析"),
]

_EXPLAIN = [
    "- **Recall@k / MRR**：混合检索对 golden 文档的召回率与排名质量。",
    "- **工具序列匹配**：agent 实际调用工具序列与期望序列的相似度（1.0 完全一致）。",
    "- **要点覆盖**：回答是否包含期望的关键字段/数字。",
    "- **引用精确率**：回答中 [n] 引用是否落在检索资料范围内（识别编造引用；无引用视为无编造）。",
    "- **引用按需**：需要引用时是否按要求给出引用（不需要时不惩罚多余引用，其有效性由引用精确率把关）。",
    "- **正确性 / 忠实度**：LLM-as-judge 评估（0~1）。",
]


def _fmt(v) -> str:
    return f"{v * 100:.1f}%"


def build_markdown(report: dict) -> str:
    lines = ["# AI 评测报告", ""]
    counts = "、".join(
        f"{title}{report.get(key, {}).get('count', 0)} 条"
        for key, title in [("retrieval", "检索"), ("customer_service", "客服"), ("analytics", "经营分析")]
    )
    lines.append(f"- 生成时间：{report.get('generated_at', '')}")
    lines.append(f"- 用例数：{counts}")
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
            if metric == "count":
                continue
            lines.append(f"| {_METRIC_LABELS.get(metric, metric)} | {_fmt(value)} |")
        lines.append("")

    lines.append("## 指标说明")
    lines.append("")
    lines.extend(_EXPLAIN)
    return "\n".join(lines)
