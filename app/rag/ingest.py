"""知识库入库：读取 data/knowledge_base 下 Markdown，分块后写入 Qdrant。"""
import re
from pathlib import Path

from app.rag import vectorstore

KB_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "knowledge_base"
MAX_CHUNK = 500


def _load_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def chunk_document(text: str, source: str, title: str) -> list[dict]:
    """按 ##/### 标题切分，长段落再按句拆分，保证 chunk <= MAX_CHUNK。"""
    sections = re.split(r"(?m)^(?=#{2,3} )", text)
    chunks: list[dict] = []
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        lines = sec.splitlines()
        section_title = lines[0].lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
        if not body:
            continue
        for part in _split_long(body):
            chunks.append(
                {
                    "title": section_title,
                    "text": f"{title}｜{section_title}\n{part}",
                    "source": source,
                }
            )
    return chunks


def _split_long(body: str, limit: int = MAX_CHUNK) -> list[str]:
    if len(body) <= limit:
        return [body]
    # 按中文句号/分号切句，贪心合并到不超过 limit
    sentences = re.split(r"(?<=[。；!?])", body)
    parts, current = [], ""
    for sent in sentences:
        if not sent.strip():
            continue
        if len(current) + len(sent) > limit and current:
            parts.append(current.strip())
            current = sent
        else:
            current += sent
    if current.strip():
        parts.append(current.strip())
    return parts


async def ingest_kb() -> int:
    """入库全部知识库文档，返回 chunk 总数。"""
    await vectorstore.ensure_collection()
    total = 0
    for path in sorted(KB_DIR.glob("*.md")):
        text = _load_markdown(path)
        title = path.stem
        chunks = chunk_document(text, source=str(path), title=title)
        for chunk in chunks:
            await vectorstore.upsert_docs(
                doc_id=f"{title}:{chunk['title']}",
                text=chunk["text"],
                source=chunk["source"],
                title=chunk["title"],
            )
            total += 1
        print(f"入库 {path.name}: {len(chunks)} 块")
    return total


if __name__ == "__main__":
    import asyncio

    print(f"共入库 {asyncio.run(ingest_kb())} 个知识块")
