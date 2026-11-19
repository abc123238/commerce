"""查询改写纯单测：不依赖 Qdrant / LLM（monkeypatch 掉模型与检索）。"""
import asyncio
from types import SimpleNamespace

from app.agent import query_rewrite
from app.agent.nodes import core
from app.agent.query_rewrite import RewriteResult, rewrite_query
from app.agent.graph import _route_after_route


class _FakeLLM:
    def __init__(self, content=None, exc: Exception | None = None, delay: float = 0.0):
        self.content = content
        self.exc = exc
        self.delay = delay
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return SimpleNamespace(content=self.content)


def _patch_llm(monkeypatch, llm) -> None:
    monkeypatch.setattr(query_rewrite, "get_llm", lambda: llm)


async def test_rewrite_success_and_clean(monkeypatch):
    monkeypatch.setattr(query_rewrite.settings, "rag_query_rewrite_enabled", True)
    llm = _FakeLLM('改写后："无线蓝牙耳机降噪效果"\n（以上为改写结果）')
    _patch_llm(monkeypatch, llm)

    result = await rewrite_query("它降噪效果怎么样", [{"role": "user", "content": "耳机"}])

    assert result.query == "无线蓝牙耳机降噪效果"
    assert result.error is None
    assert llm.calls == 1


async def test_rewrite_keeps_question_when_already_self_contained(monkeypatch):
    monkeypatch.setattr(query_rewrite.settings, "rag_query_rewrite_enabled", True)
    _patch_llm(monkeypatch, _FakeLLM("七天无理由退换怎么申请"))

    result = await rewrite_query("七天无理由退换怎么申请", [])

    assert result.query == "七天无理由退换怎么申请"
    assert result.error is None


async def test_rewrite_timeout_falls_back(monkeypatch):
    monkeypatch.setattr(query_rewrite.settings, "rag_query_rewrite_enabled", True)
    monkeypatch.setattr(query_rewrite.settings, "rag_rewrite_timeout_seconds", 0.01)
    _patch_llm(monkeypatch, _FakeLLM("改写结果", delay=1.0))

    result = await rewrite_query("它多少钱", [])

    assert result.query == "它多少钱"
    assert result.error == "timeout"


async def test_rewrite_exception_falls_back(monkeypatch):
    monkeypatch.setattr(query_rewrite.settings, "rag_query_rewrite_enabled", True)
    _patch_llm(monkeypatch, _FakeLLM(exc=RuntimeError("upstream down")))

    result = await rewrite_query("它多少钱", [])

    assert result.query == "它多少钱"
    assert result.error == "exception"


async def test_rewrite_empty_output_falls_back(monkeypatch):
    monkeypatch.setattr(query_rewrite.settings, "rag_query_rewrite_enabled", True)
    _patch_llm(monkeypatch, _FakeLLM("  \n "))

    result = await rewrite_query("它多少钱", [])

    assert result.query == "它多少钱"
    assert result.error == "empty_output"


async def test_rewrite_disabled_skips_llm(monkeypatch):
    monkeypatch.setattr(query_rewrite.settings, "rag_query_rewrite_enabled", False)
    llm = _FakeLLM("不应被调用")
    _patch_llm(monkeypatch, llm)

    result = await rewrite_query("它多少钱", [])

    assert result.query == "它多少钱"
    assert result.error is None
    assert llm.calls == 0


async def test_rewrite_node_writes_state_and_step(monkeypatch):
    async def fake_rewrite(question, history):
        return RewriteResult(query="无线蓝牙耳机降噪效果")

    monkeypatch.setattr(core, "rewrite_query", fake_rewrite)
    node = core.make_rewrite_node()

    update = await node({"question": "它降噪效果怎么样", "history": []})

    assert update["rewritten_query"] == "无线蓝牙耳机降噪效果"
    assert update["steps"][0]["node"] == "rewrite"
    assert "改写" in update["steps"][0]["detail"]


async def test_rewrite_node_reports_fallback(monkeypatch):
    async def fake_rewrite(question, history):
        return RewriteResult(query=question, error="timeout")

    monkeypatch.setattr(core, "rewrite_query", fake_rewrite)
    node = core.make_rewrite_node()

    update = await node({"question": "它多少钱", "history": []})

    assert update["rewritten_query"] == "它多少钱"
    assert "失败" in update["steps"][0]["detail"]


async def test_retrieve_prefers_rewritten_query(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_search(query, top_k=None):
        captured["query"] = query
        return [SimpleNamespace(text="t", source="s", title="title")]

    monkeypatch.setattr(core, "hybrid_search", fake_search)
    node = core.make_retrieve_node()

    await node(
        {
            "question": "它降噪效果怎么样",
            "rewritten_query": "无线蓝牙耳机降噪效果",
            "history": [],
        }
    )

    assert captured["query"] == "无线蓝牙耳机降噪效果"


def test_route_sends_customer_service_to_rewrite():
    assert _route_after_route({"intent": "customer_service"}) == "rewrite"
    assert _route_after_route({"intent": "analytics"}) == "reason"
