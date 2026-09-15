"""检索质量指标：Recall@k 与 MRR（纯函数，便于单测）。"""
from __future__ import annotations


def recall_at_k(hit_doc_ids: list[str], golden_ids: list[str], k: int | None = None) -> float:
    """召回率：命中的 golden 文档数 / golden 文档总数。

    hit_doc_ids 为按相关性排序的检索结果；golden_ids 为期望命中的文档。
    """
    top = hit_doc_ids[:k] if k is not None else hit_doc_ids
    golden = set(golden_ids)
    if not golden:
        return 0.0
    return len(set(top) & golden) / len(golden)


def mrr(hit_doc_ids: list[str], golden_ids: list[str]) -> float:
    """平均倒数排名：第一个 golden 命中的位置倒数；无命中为 0。"""
    golden = set(golden_ids)
    for i, doc_id in enumerate(hit_doc_ids, start=1):
        if doc_id in golden:
            return 1.0 / i
    return 0.0
