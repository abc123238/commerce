"""任务级指标：工具序列匹配与答案要点覆盖（纯函数）。"""
from __future__ import annotations


def tool_sequence_match(actual: list[str], expected: list[str]) -> float:
    """实际执行工具序列与期望序列的匹配度，0~1。

    完全一致 = 1.0；期望是实际的有序子序列 = 0.8；否则按集合交并比。
    """
    if actual == expected:
        return 1.0
    it = iter(actual)
    if expected and all(t in it for t in expected):
        return 0.8
    inter = len(set(actual) & set(expected))
    union = len(set(actual) | set(expected))
    return inter / union if union else 0.0


def key_fields_covered(answer: str, fields: list[str]) -> float:
    """答案是否覆盖期望要点（字段/关键词），按命中比例计分。"""
    if not fields:
        return 1.0
    hit = sum(1 for f in fields if f in answer)
    return hit / len(fields)
