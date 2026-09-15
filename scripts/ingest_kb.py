"""知识库入库脚本：python -m scripts.ingest_kb。

等价于 python -m app.rag.ingest，封装成 scripts 目录统一入口。
读取 data/knowledge_base 下 Markdown，分块写入 Qdrant（需先启动 qdrant）。
"""
import asyncio

from app.rag.ingest import ingest_kb

if __name__ == "__main__":
    total = asyncio.run(ingest_kb())
    print(f"共入库 {total} 个知识块")
