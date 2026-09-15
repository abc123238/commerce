"""混合检索集成测试（需要 Qdrant + embedding 模型缓存）。

使用独立测试集合 rag_test，结束后删除，不影响主知识库。
"""
import pytest

from app.rag import vectorstore

pytestmark = pytest.mark.integration

TEST_COLLECTION = "rag_test"

DOCS = [
    {
        "doc_id": "t:蓝牙耳机",
        "text": "无线蓝牙耳机：主动降噪，蓝牙 5.3，续航 30 小时，支持双设备连接，Type-C 快充。",
        "title": "蓝牙耳机",
    },
    {
        "doc_id": "t:退换政策",
        "text": "退换货政策：签收后 7 天内支持无理由退换，商品需保持完好与吊牌完整。",
        "title": "退换政策",
    },
    {
        "doc_id": "t:机械键盘",
        "text": "机械键盘：87 键热插拔，Gateron 红轴，PBT 键帽，全键无冲，支持三模连接。",
        "title": "机械键盘",
    },
]


@pytest.fixture
async def rag_collection(monkeypatch):
    monkeypatch.setattr(vectorstore.settings, "rag_collection", TEST_COLLECTION)
    await vectorstore.ensure_collection()
    for doc in DOCS:
        await vectorstore.upsert_docs(
            doc_id=doc["doc_id"], text=doc["text"], source="test.md", title=doc["title"]
        )
    yield
    await vectorstore.delete_collection()


async def test_hybrid_recalls_relevant_doc(rag_collection):
    hits = await vectorstore.hybrid_search("蓝牙耳机多少钱 降噪 续航", top_k=3)
    titles = [h.title for h in hits]
    assert "蓝牙耳机" in titles, f"应召回蓝牙耳机，实际 {titles}"
    assert hits[0].doc_id == "t:蓝牙耳机"


async def test_hybrid_recalls_sparse_only_match(rag_collection):
    # 稀疏路（BM25 词汇命中）应能独立召回：查询含强区分词「退换 吊牌」
    hits = await vectorstore.hybrid_search("商品退换货要保留吊牌", top_k=3)
    titles = [h.title for h in hits]
    assert "退换政策" in titles, f"应召回退换政策，实际 {titles}"
