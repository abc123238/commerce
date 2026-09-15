"""稀疏向量编码：jieba 分词 + 哈希映射到固定维度。

Qdrant 原生混合检索需要 sparse 向量（{indices, values}）。
中文 BM25 若不做预分词会按空白切分导致整句一个 token，故这里强制 jieba 分词。
词->索引采用确定性哈希，保证查询与文档对齐。
"""
import hashlib

import jieba

SPARSE_DIM = 100_000


def _tokenize(text: str) -> list[str]:
    # jieba.cut 返回生成器；过滤空白与单字符标点
    return [t for t in jieba.cut(text) if t.strip() and len(t) > 1]


def token_index(token: str) -> int:
    return int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % SPARSE_DIM


def sparse_vector(text: str) -> dict[str, int]:
    """返回 {index: term_frequency}（Qdrant sparse vector 原始格式）。"""
    freq: dict[int, int] = {}
    for token in _tokenize(text):
        freq[token_index(token)] = freq.get(token_index(token), 0) + 1
    return freq
