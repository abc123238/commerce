"""评测主入口：跑三套数据集 -> 聚合指标 -> 写 report.json / report.md。

用法：uv run python -m eval.runner
前置：已启动依赖服务（Postgres/Qdrant/Redis）并完成 seed + 知识库入库
（uv run python -m scripts.seed && uv run python -m scripts.ingest_kb）。
"""
import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.agent.graph import run_agent
from app.core.logging import get_logger
from app.db.models.user import User
from app.db.session import SessionLocal
from app.rag.vectorstore import hybrid_search
from eval import config
from eval.metrics.answer import judge_metrics
from eval.metrics.citation import citation_presence, citation_precision
from eval.metrics.retrieval import mrr, recall_at_k
from eval.metrics.task import key_fields_covered, tool_sequence_match
from eval.report import build_markdown

logger = get_logger("eval")

_AGENT_METRIC_KEYS = ("tool_sequence_match", "key_fields_covered")
_CITATION_KEYS = ("citation_precision", "citation_presence")


def _zero_metrics(include_citation: bool) -> dict:
    """失败兜底：全 0 分。引用指标只在 RAG 类数据集上统计。"""
    keys = list(_AGENT_METRIC_KEYS) + (list(_CITATION_KEYS) if include_citation else [])
    return {k: 0.0 for k in keys}


def _load_dataset(name: str) -> list[dict]:
    path = config.DATASETS_DIR / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def _run_retrieval_case(case: dict) -> dict:
    hits = await hybrid_search(case["question"], top_k=config.RETRIEVAL_TOP_K)
    ids = [h.doc_id for h in hits]
    return {
        "recall@k": recall_at_k(ids, case.get("golden_doc_ids", [])),
        "mrr": mrr(ids, case.get("golden_doc_ids", [])),
    }


def _extract_tools(steps: list[dict]) -> list[str]:
    """从 step 审计中还原实际执行的工具名（detail 形如「执行 2 个工具：a, b」）。"""
    names: list[str] = []
    for s in steps:
        detail = s.get("detail", "")
        if s.get("node") == "execute_tool" and detail.startswith("执行") and "：" in detail:
            names.extend(n.strip() for n in detail.split("：", 1)[1].split(",") if n.strip())
    return names


async def _run_agent_case(
    db, user_id: int, case: dict, include_citation: bool, user_role: str | None = None
) -> dict:
    result = await run_agent(db, user_id, case["question"], user_role=user_role)
    answer = result.get("answer", "")
    metrics = {
        "tool_sequence_match": tool_sequence_match(
            _extract_tools(result.get("steps", [])), case.get("expected_tools", [])
        ),
        "key_fields_covered": key_fields_covered(answer, case.get("expected_fields", [])),
    }
    if include_citation:
        metrics["citation_precision"] = citation_precision(answer, len(result.get("retrieved", [])))
        metrics["citation_presence"] = citation_presence(answer, case.get("requires_citation", False))
    if config.ENABLE_LLM_JUDGE:
        judge = await judge_metrics(case["question"], answer, case.get("golden_answer"))
        metrics.update({"correctness": judge["correctness"], "faithfulness": judge["faithfulness"]})
    return metrics


def _aggregate(metrics: list[dict]) -> dict:
    if not metrics:
        return {"count": 0}
    agg = {"count": len(metrics)}
    for key in metrics[0]:
        agg[key] = round(sum(m[key] for m in metrics) / len(metrics), 3)
    return agg


async def _run_retrieval() -> dict:
    cases = _load_dataset("retrieval")
    return _aggregate([await _run_retrieval_case(c) for c in cases])


async def _run_agent_dataset(db, user_id: int, name: str) -> dict:
    cases = _load_dataset(name)[: config.MAX_AGENT_CASES]
    include_citation = name == "customer_service"
    # 经营分析数据集需要管理员角色，否则 build_agent 会回退到客服工具集
    user_role = "admin" if name == "analytics" else "customer"
    metrics = []
    for case in cases:
        try:
            metrics.append(
                await _run_agent_case(db, user_id, case, include_citation, user_role=user_role)
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("eval_case_fail", dataset=name, question=case["question"][:50], exc=str(exc)[:150])
            metrics.append(_zero_metrics(include_citation))
    return _aggregate(metrics)


async def _write_back_langfuse(report: dict) -> None:
    """把聚合评测分回写 Langfuse（按 trace 打标）。可插拔：未部署时跳过。"""
    if not config.WRITE_BACK_LANGFUSE or not report:
        return
    try:
        from langfuse import Langfuse

        client = Langfuse()
        for dataset, section in (("retrieval", report["retrieval"]), ("customer_service", report["customer_service"]), ("analytics", report["analytics"])):
            if not section.get("count"):
                continue
            for metric, value in section.items():
                if metric == "count":
                    continue
                client.score(
                    name=f"eval:{dataset}:{metric}",
                    value=value,
                    comment=f"{dataset} 数据集聚合指标",
                )
        client.flush()
        logger.info("langfuse_writeback_ok")
    except Exception as exc:  # noqa: BLE001
        logger.warning("langfuse_writeback_skip", exc=str(exc)[:120])


async def run() -> dict:
    """执行全部评测并落盘 report.json / report.md，返回报告字典。"""
    retrieval = await _run_retrieval()

    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == "demo@example.com"))
        if user is None:
            logger.error("demo_user_missing", hint="请先运行 uv run python -m scripts.seed")
            return {}
        customer_service = await _run_agent_dataset(db, user.id, "customer_service")
        analytics = await _run_agent_dataset(db, user.id, "analytics")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "retrieval": retrieval,
        "customer_service": customer_service,
        "analytics": analytics,
    }
    config.REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    config.REPORT_MD.write_text(build_markdown(report), encoding="utf-8")
    logger.info("eval_done", report=config.REPORT_MD.name)
    return report


if __name__ == "__main__":
    asyncio.run(run())
