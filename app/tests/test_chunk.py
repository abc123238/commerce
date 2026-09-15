"""知识库分块逻辑纯单测。"""
from app.rag.ingest import MAX_CHUNK, _split_long, chunk_document


def test_chunk_by_headings():
    md = "## 耳机\n内容A。\n### 音质\n内容B。\n"
    chunks = chunk_document(md, "products.md", "products")
    assert len(chunks) == 2
    assert chunks[0]["title"] == "耳机"
    assert chunks[1]["title"] == "音质"
    assert all(c["source"] == "products.md" for c in chunks)
    assert all("products" in c["text"] for c in chunks)


def test_chunk_ignores_heading_only_sections():
    md = "## 只有标题\n\n## 有效标题\n正文内容。\n"
    chunks = chunk_document(md, "f.md", "f")
    assert len(chunks) == 1
    assert chunks[0]["title"] == "有效标题"


def test_split_long_keeps_chunks_within_limit():
    long_body = "很好。很好。很好。" * 200  # 远超 500 字符上限
    parts = _split_long(long_body)
    assert len(parts) > 1
    assert all(len(p) <= MAX_CHUNK for p in parts)
    assert "很好。很好。很好。" * 200 == "".join(parts)


def test_split_short_passthrough():
    assert _split_long("短文本。") == ["短文本。"]
