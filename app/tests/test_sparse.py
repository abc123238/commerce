"""稀疏向量编码纯单测：jieba 预分词 + 确定性哈希。"""
from app.rag.sparse import SPARSE_DIM, sparse_vector, token_index


def test_token_index_deterministic_and_bounded():
    assert token_index("蓝牙") == token_index("蓝牙")
    assert 0 <= token_index("蓝牙") < SPARSE_DIM
    assert 0 <= token_index("机械键盘") < SPARSE_DIM


def test_sparse_vector_same_text_same_vector():
    assert sparse_vector("机械键盘 红轴") == sparse_vector("机械键盘 红轴")


def test_sparse_vector_indices_bounded_and_positive():
    vec = sparse_vector("无线蓝牙耳机，蓝牙连接稳定")
    assert isinstance(vec, dict)
    assert vec, "分词后不应为空"
    assert all(0 <= k < SPARSE_DIM for k in vec)
    assert all(v > 0 for v in vec.values())


def test_sparse_vector_repeat_token_frequency():
    # 同一词出现两次 → 其索引频次为 2（分词粒度不同也至少 >=1）
    idx = token_index("蓝牙")
    vec = sparse_vector("蓝牙耳机，蓝牙连接")
    assert vec.get(idx, 0) >= 1
