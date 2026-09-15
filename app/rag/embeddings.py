"""Embedding 封装：fastembed 单例 + HF 下载镜像适配。"""
import os
from functools import lru_cache

from app.config import settings

# fastembed 内部经 huggingface_hub 下载模型，先按配置适配国内镜像
if settings.hf_endpoint:
    os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
if settings.hf_disable_xet:
    os.environ["HF_HUB_DISABLE_XET"] = "1"


@lru_cache(maxsize=1)
def get_embedding_model():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=settings.embedding_model)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量编码文档/查询，返回 list[list[float]]。"""
    if not texts:
        return []
    model = get_embedding_model()
    vectors = model.embed(texts, batch_size=16)
    return [list(map(float, v)) for v in vectors]


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]


def embedding_dim() -> int:
    return len(embed_query("维度探测"))
