"""查询改写：把多轮对话中的指代与省略改写成自包含的检索 query。

检索阶段拿不到 LLM 的上下文理解，只能吃字面 query；这里在检索前补一次
结合历史的改写。任何失败（超时/异常/空输出）都降级为原问题，不能阻断回答。
"""
import asyncio
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.llm import extract_text, get_llm
from app.agent.prompts import REWRITE_PROMPT, build_rewrite_prompt
from app.config import settings
from app.core.logging import get_logger

logger = get_logger("query_rewrite")

_MAX_QUERY_CHARS = 200
_STRIP_PREFIXES = (
    "改写后：",
    "改写后:",
    "改写：",
    "改写:",
    "检索查询：",
    "检索查询:",
    "查询：",
    "查询:",
    "query:",
    "Query:",
)


@dataclass
class RewriteResult:
    query: str
    error: str | None = None


def _clean(text: str, fallback: str) -> str:
    first = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    for prefix in _STRIP_PREFIXES:
        if first.startswith(prefix):
            first = first[len(prefix) :].strip()
    first = first.strip(" \t`\"'“”‘’")
    return first[:_MAX_QUERY_CHARS] if first else fallback


async def rewrite_query(
    question: str, history: list[dict] | None = None
) -> RewriteResult:
    """返回改写后的检索 query；永不抛异常。"""
    question = (question or "").strip()
    if not question:
        return RewriteResult(query=question, error="empty_question")
    if not settings.rag_query_rewrite_enabled:
        return RewriteResult(query=question)

    turns = max(0, settings.rag_rewrite_history_turns)
    recent = (history or [])[-turns * 2 :] if turns else []
    messages = [
        SystemMessage(content=REWRITE_PROMPT),
        HumanMessage(content=build_rewrite_prompt(question, recent)),
    ]
    try:
        response = await asyncio.wait_for(
            get_llm().ainvoke(messages),
            timeout=settings.rag_rewrite_timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("query_rewrite_timeout", question=question[:80])
        return RewriteResult(query=question, error="timeout")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "query_rewrite_failed", question=question[:80], exc=str(exc)[:200]
        )
        return RewriteResult(query=question, error="exception")

    text = extract_text(getattr(response, "content", ""))
    if not text.strip():
        logger.warning("query_rewrite_empty", question=question[:80])
        return RewriteResult(query=question, error="empty_output")
    return RewriteResult(query=_clean(text, question))
