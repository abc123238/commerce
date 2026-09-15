"""Qdrant 向量库封装：命名向量（dense + sparse）+ 原生 FusionQuery(RRF) 混合检索。"""
import asyncio
import uuid
from dataclasses import dataclass
from functools import lru_cache

from qdrant_client import AsyncQdrantClient, models

from app.config import settings
from app.rag import embeddings, sparse

VECTOR_NAME_DENSE = "dense"
VECTOR_NAME_SPARSE = "sparse"


@dataclass
class Hit:
    doc_id: str
    text: str
    source: str
    title: str
    score: float


@lru_cache(maxsize=1)
def get_client() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=settings.qdrant_url)


async def ensure_collection() -> None:
    """幂等创建集合：dense 768 维 + sparse 命名向量。"""
    client = get_client()
    collections = await client.get_collections()
    names = {c.name for c in collections.collections}
    if settings.rag_collection in names:
        return
    await client.create_collection(
        collection_name=settings.rag_collection,
        vectors_config={
            VECTOR_NAME_DENSE: models.VectorParams(
                size=await asyncio.to_thread(embeddings.embedding_dim),
                distance=models.Distance.COSINE,
            )
        },
        sparse_vectors_config={
            VECTOR_NAME_SPARSE: models.SparseVectorParams(
                index=models.SparseIndexParams(datatype=models.Datatype.FLOAT32)
            )
        },
    )


async def upsert_docs(
    doc_id: str,
    text: str,
    source: str,
    title: str,
) -> None:
    """单条文档入库：dense 向量 + jieba 稀疏向量 + 元数据。"""
    # fastembed 的 embed 是同步 CPU 密集调用，挪到线程池避免阻塞事件循环
    dense = await asyncio.to_thread(embeddings.embed_query, text)
    svec = await asyncio.to_thread(sparse.sparse_vector, text)
    client = get_client()
    await client.upsert(
        collection_name=settings.rag_collection,
        points=[
            models.PointStruct(
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{text}"),
                vector={
                    VECTOR_NAME_DENSE: dense,
                    VECTOR_NAME_SPARSE: models.SparseVector(
                        indices=sorted(svec.keys()), values=[float(v) for v in svec.values()]
                    ),
                },
                payload={"doc_id": doc_id, "text": text, "source": source, "title": title},
            )
        ],
    )


async def hybrid_search(query: str, top_k: int | None = None) -> list[Hit]:
    """dense + sparse 双路 prefetch，RRF 融合后返回命中。"""
    client = get_client()
    k = top_k or settings.rag_top_k
    dense = await asyncio.to_thread(embeddings.embed_query, query)
    svec = await asyncio.to_thread(sparse.sparse_vector, query)
    prefetch_k = k * 3
    resp = await client.query_points(
        collection_name=settings.rag_collection,
        prefetch=[
            models.Prefetch(
                query=dense, using=VECTOR_NAME_DENSE, limit=prefetch_k
            ),
            models.Prefetch(
                query=models.SparseVector(
                    indices=sorted(svec.keys()), values=[float(v) for v in svec.values()]
                ),
                using=VECTOR_NAME_SPARSE,
                limit=prefetch_k,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=k,
        with_payload=True,
    )
    hits = []
    for point in resp.points:
        payload = point.payload or {}
        hits.append(
            Hit(
                doc_id=str(payload.get("doc_id", "")),
                text=str(payload.get("text", "")),
                source=str(payload.get("source", "")),
                title=str(payload.get("title", "")),
                score=float(point.score),
            )
        )
    return hits


async def delete_collection() -> None:
    client = get_client()
    await client.delete_collection(settings.rag_collection)
