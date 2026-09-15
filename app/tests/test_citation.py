"""引用提取/格式化纯单测。"""
from app.rag.citation import build_references, extract_cited_indices


def test_extract_dedup_and_sort():
    assert extract_cited_indices("见 [3] 与 [1] 再 [3] 又 [2]") == [1, 2, 3]


def test_extract_ignore_oob_and_zero():
    assert extract_cited_indices("[0] [5] [2]", max_index=3) == [2]


def test_extract_no_citations():
    assert extract_cited_indices("没有任何引用标记") == []


def test_extract_no_max_index():
    assert extract_cited_indices("[1] [9]") == [1, 9]


def test_build_references_format():
    citations = [
        {"index": 1, "title": "无线蓝牙耳机", "source": "products.md"},
        {"index": 2, "title": "退换政策", "source": "returns.md"},
    ]
    refs = build_references(citations)
    assert refs.startswith("参考资料：")
    assert "[1] 无线蓝牙耳机（products.md）" in refs
    assert "[2] 退换政策（returns.md）" in refs


def test_build_references_empty():
    assert build_references([]) == ""
