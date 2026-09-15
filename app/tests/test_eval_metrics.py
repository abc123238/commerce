"""评测指标纯单测（不依赖外部服务）。"""
from eval.metrics.citation import citation_presence, citation_precision
from eval.metrics.retrieval import mrr, recall_at_k
from eval.metrics.task import key_fields_covered, tool_sequence_match


def test_recall_at_k():
    assert recall_at_k(["a", "b", "c"], ["a", "c"]) == 1.0
    assert recall_at_k(["a", "b", "c"], ["a", "c"], k=1) == 0.5
    assert recall_at_k(["x"], ["a"]) == 0.0
    assert recall_at_k([], []) == 0.0


def test_mrr():
    assert mrr(["a", "b"], ["b"]) == 0.5
    assert mrr(["a", "b"], ["c"]) == 0.0
    assert mrr(["a", "b"], ["a"]) == 1.0


def test_citation_precision():
    assert citation_precision("见 [1] 和 [2] 的描述", n_retrieved=2) == 1.0
    assert citation_precision("见 [3] 的内容", n_retrieved=2) == 0.0  # 越界引用
    assert citation_precision("没有引用", n_retrieved=2) == 1.0  # 无引用即无编造引用
    assert citation_precision("见 [1] 和 [9]", n_retrieved=3) == 0.5


def test_citation_presence():
    assert citation_presence("见 [1]", requires_citation=True) == 1.0
    assert citation_presence("无引用", requires_citation=True) == 0.0
    assert citation_presence("无引用", requires_citation=False) == 1.0
    assert citation_presence("见 [2]", requires_citation=False) == 1.0  # 不需要引用时多余引用不惩罚


def test_tool_sequence_match():
    assert tool_sequence_match(["a", "b"], ["a", "b"]) == 1.0
    assert tool_sequence_match(["a", "b", "c"], ["a", "c"]) == 0.8
    assert tool_sequence_match(["a"], ["a", "b"]) == 0.5
    assert tool_sequence_match([], ["a"]) == 0.0
    assert tool_sequence_match([], []) == 1.0


def test_key_fields_covered():
    assert key_fields_covered("销售额 100 元", ["销售额", "100"]) == 1.0
    assert key_fields_covered("销售额 100 元", ["销售额", "增长率"]) == 0.5
    assert key_fields_covered("", ["销售额"]) == 0.0
    assert key_fields_covered("任意", []) == 1.0
