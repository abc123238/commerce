"""引用质量指标：精确率（是否有越界/编造引用）与是否按要求引用。"""
from __future__ import annotations

import re

_PATTERN = re.compile(r"\[(\d+)\]")


def _answer_refs(answer: str) -> set[int]:
    return {int(m) for m in _PATTERN.findall(answer)}


def citation_precision(answer: str, n_retrieved: int) -> float:
    """引用精确率：答案中的 [n] 引用落在检索资料范围内（1..n_retrieved）的比例。

    越界引用视为编造，直接惩罚；无引用视为无编造引用（空集的真值通过）。
    """
    refs = _answer_refs(answer)
    if not refs:
        return 1.0
    valid = {r for r in refs if 1 <= r <= n_retrieved}
    return len(valid) / len(refs)


def citation_presence(answer: str, requires_citation: bool = True) -> float:
    """是否需要引用时是否给出了引用（引用按需）。

    需要引用时必须有 [n] 才通过；不需要引用时不强求——多余引用不惩罚，
    其有效性由 citation_precision 把关。
    """
    if not requires_citation:
        return 1.0
    return 1.0 if _answer_refs(answer) else 0.0
