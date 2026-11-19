"""评测执行引擎：与数据库解耦的执行层，逐用例回调 case_sink。

CLI 与后端 API 共用同一套执行/聚合/门禁逻辑，避免两处口径漂移。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from app.core.logging import get_logger
from eval import config, datasets, versions
from eval.metrics.answer import judge_metrics
from eval.metrics.citation import citation_presence, citation_precision
from eval.metrics.retrieval import mrr, recall_at_k
from eval.metrics.safety import behavior_match, safety_pass
from eval.metrics.task import key_fields_covered, tool_sequence_match
from eval.usage import UsageCollector, estimate_cost

logger = get_logger("eval")

DEMO_USER_EMAIL = "demo@example.com"
AGENT_DATASETS = ("customer_service", "analytics", "robustness")

CaseSink = Callable[["CaseResult"], Awaitable[None]]


@dataclass
class EvalOptions:
    datasets: list[str] = field(default_factory=lambda: list(datasets.DATASETS))
    tags: list[str] = field(default_factory=list)
    difficulty: str | None = None
    limit: int | None = None
    judge: bool = config.ENABLE_LLM_JUDGE
    top_k: int = config.RETRIEVAL_TOP_K


@dataclass
class CaseResult:
    dataset: str
    case_id: str
    question: str
    user_role: str
    status: str  # ok | error
    expected: dict
    actual_tools: list[str] = field(default_factory=list)
    tool_events: list[dict] = field(default_factory=list)
    retrieved: list[dict] = field(default_factory=list)
    answer: str = ""
    rewritten_query: str | None = None
    metrics: dict | None = None
    judge_correctness: float | None = None
    judge_faithfulness: float | None = None
    judge_reason: str | None = None
    judge_error: str | None = None
    agent_input_tokens: int = 0
    agent_output_tokens: int = 0
    judge_input_tokens: int = 0
    judge_output_tokens: int = 0
    cost_usd: float | None = None
    latency_ms: int = 0
    error_type: str | None = None
    error_message: str | None = None


def _expected(case: dict) -> dict:
    keys = (
        "golden_doc_ids",
        "expected_tools",
        "expected_fields",
        "golden_answer",
        "requires_citation",
        "expected_behavior",
        "forbidden_fields",
    )
    return {key: case[key] for key in keys if key in case}


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


async def _run_retrieval_case(case: dict, options: EvalOptions) -> CaseResult:
    from app.agent.query_rewrite import rewrite_query
    from app.rag.vectorstore import hybrid_search

    started = time.perf_counter()
    try:
        question = case["question"]
        history = case.get("history") or []
        # 只有多轮用例需要 LLM 改写；单轮检索冒烟保持无 LLM 依赖。
        query = question
        rewritten = None
        if history:
            result = await rewrite_query(question, history)
            query = result.query
            rewritten = result.query
        hits = await hybrid_search(query, top_k=options.top_k)
        ids = [hit.doc_id for hit in hits]
        golden = case.get("golden_doc_ids", [])
        metrics = {"recall@k": recall_at_k(ids, golden), "mrr": mrr(ids, golden)}
        if history:
            before_hits = (
                hits
                if query == question
                else await hybrid_search(question, top_k=options.top_k)
            )
            before_ids = [hit.doc_id for hit in before_hits]
            metrics["recall@k_before"] = recall_at_k(before_ids, golden)
            metrics["mrr_before"] = mrr(before_ids, golden)
        return CaseResult(
            dataset="retrieval",
            case_id=case["id"],
            question=question,
            user_role=case.get("user_role", "customer"),
            status="ok",
            expected=_expected(case),
            retrieved=[
                {
                    "doc_id": hit.doc_id,
                    "source": hit.source,
                    "title": hit.title,
                    "score": round(float(hit.score), 4),
                }
                for hit in hits
            ],
            rewritten_query=rewritten,
            metrics=metrics,
            cost_usd=0.0,
            latency_ms=_elapsed_ms(started),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "eval_case_fail",
            dataset="retrieval",
            case=case.get("id"),
            exc=str(exc)[:200],
        )
        return CaseResult(
            dataset="retrieval",
            case_id=case["id"],
            question=case["question"],
            user_role=case.get("user_role", "customer"),
            status="error",
            expected=_expected(case),
            latency_ms=_elapsed_ms(started),
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],
        )


async def _run_agent_case(
    db, user_id: int, dataset: str, case: dict, options: EvalOptions
) -> CaseResult:
    from app.agent.graph import run_agent

    role = case.get("user_role") or ("admin" if dataset == "analytics" else "customer")
    started = time.perf_counter()
    usage = UsageCollector()
    try:
        result = await run_agent(
            db, user_id, case["question"], user_role=role, callbacks=[usage]
        )
        answer = result.get("answer", "")
        tool_events = result.get("tool_events", [])
        actual_tools = [
            event["name"]
            for event in tool_events
            if event.get("status") == "executed"
        ]
        metrics = {
            "tool_sequence_match": tool_sequence_match(
                actual_tools, case.get("expected_tools", [])
            ),
            "key_fields_covered": key_fields_covered(
                answer, case.get("expected_fields", [])
            ),
        }
        if dataset in ("customer_service", "robustness"):
            retrieved = result.get("retrieved", [])
            metrics["citation_precision"] = citation_precision(answer, len(retrieved))
            metrics["citation_presence"] = citation_presence(
                answer, case.get("requires_citation", False)
            )
        expected_behavior = case.get("expected_behavior")
        if expected_behavior:
            metrics["behavior_match"] = behavior_match(answer, expected_behavior)
        forbidden = case.get("forbidden_fields")
        if forbidden:
            metrics["safety_pass"] = safety_pass(answer, forbidden)

        judged: dict = {}
        if options.judge:
            judged = await judge_metrics(
                case["question"],
                answer,
                case.get("golden_answer"),
                context=result.get("retrieved"),
            )
            for key in ("correctness", "faithfulness"):
                if judged.get(key) is not None:
                    metrics[key] = judged[key]

        judge_input = int(judged.get("input_tokens") or 0)
        judge_output = int(judged.get("output_tokens") or 0)
        return CaseResult(
            dataset=dataset,
            case_id=case["id"],
            question=case["question"],
            user_role=role,
            status="ok",
            expected=_expected(case),
            actual_tools=actual_tools,
            tool_events=tool_events,
            retrieved=result.get("retrieved", []),
            answer=answer,
            metrics=metrics,
            judge_correctness=judged.get("correctness"),
            judge_faithfulness=judged.get("faithfulness"),
            judge_reason=judged.get("reason"),
            judge_error=judged.get("error"),
            agent_input_tokens=usage.input_tokens,
            agent_output_tokens=usage.output_tokens,
            judge_input_tokens=judge_input,
            judge_output_tokens=judge_output,
            cost_usd=estimate_cost(
                usage.input_tokens + judge_input,
                usage.output_tokens + judge_output,
            ),
            latency_ms=_elapsed_ms(started),
        )
    except Exception as exc:  # noqa: BLE001
        # 单例失败可能让会话停在失败事务里，回滚以免污染后续用例。
        await db.rollback()
        logger.error(
            "eval_case_fail", dataset=dataset, case=case.get("id"), exc=str(exc)[:200]
        )
        return CaseResult(
            dataset=dataset,
            case_id=case["id"],
            question=case["question"],
            user_role=role,
            status="error",
            expected=_expected(case),
            latency_ms=_elapsed_ms(started),
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],
        )


async def _demo_user_id(db) -> int:
    from sqlalchemy import select

    from app.db.models.user import User

    user = await db.scalar(select(User).where(User.email == DEMO_USER_EMAIL))
    if user is None:
        raise RuntimeError(
            f"缺少演示用户 {DEMO_USER_EMAIL}，请先运行 python -m scripts.seed"
        )
    return user.id


async def run_datasets(options: EvalOptions, case_sink: CaseSink | None = None) -> dict:
    """按选项执行评测，返回报告字典（含逐用例结果）。"""
    cases_by_dataset = datasets.load_datasets(
        options.datasets,
        tags=options.tags,
        difficulty=options.difficulty,
        limit=options.limit,
    )
    if not any(cases_by_dataset.values()):
        raise ValueError("过滤后没有可执行的评测用例")

    results: list[CaseResult] = []

    async def _emit(result: CaseResult) -> None:
        results.append(result)
        if case_sink is not None:
            await case_sink(result)

    for case in cases_by_dataset.get("retrieval", []):
        await _emit(await _run_retrieval_case(case, options))

    agent_datasets = [
        name for name in AGENT_DATASETS if cases_by_dataset.get(name)
    ]
    if agent_datasets:
        from app.db.session import SessionLocal

        async with SessionLocal() as db:
            user_id = await _demo_user_id(db)
            for name in agent_datasets:
                for case in cases_by_dataset[name]:
                    await _emit(await _run_agent_case(db, user_id, name, case, options))

    return build_report(results, options)


def _percentile(values: list[int], q: float) -> int:
    """最近秩百分位；样本量小时用于 p50/p95 的粗略估计。"""
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def aggregate_cases(results: list[CaseResult]) -> dict:
    """按数据集聚合：只对成功用例求均值，错误单独计数。

    延迟只统计成功用例（失败可能瞬时返回，混入会拉低分位数）；
    token / 成本按成功用例求和，便于估算一次全量评测的开销。
    """
    sections: dict[str, dict] = {}
    for result in results:
        section = sections.setdefault(
            result.dataset,
            {
                "count": 0,
                "scored_count": 0,
                "error_count": 0,
                "_values": {},
                "_latencies": [],
                "_input_tokens": 0,
                "_output_tokens": 0,
                "_cost": None,
            },
        )
        section["count"] += 1
        if result.status != "ok":
            section["error_count"] += 1
            continue
        section["scored_count"] += 1
        section["_latencies"].append(result.latency_ms)
        section["_input_tokens"] += result.agent_input_tokens + result.judge_input_tokens
        section["_output_tokens"] += result.agent_output_tokens + result.judge_output_tokens
        if result.cost_usd is not None:
            section["_cost"] = (section["_cost"] or 0.0) + result.cost_usd
        for key, value in (result.metrics or {}).items():
            if value is None:
                continue
            section["_values"].setdefault(key, []).append(float(value))

    for section in sections.values():
        for key, values in section.pop("_values").items():
            section[key] = round(sum(values) / len(values), 3) if values else None

        latencies = section.pop("_latencies")
        if latencies:
            section["latency_ms_p50"] = _percentile(latencies, 0.5)
            section["latency_ms_p95"] = _percentile(latencies, 0.95)
            section["latency_ms_avg"] = round(sum(latencies) / len(latencies), 1)
            section["latency_ms_max"] = max(latencies)

        input_tokens = section.pop("_input_tokens")
        output_tokens = section.pop("_output_tokens")
        section["input_tokens"] = input_tokens
        section["output_tokens"] = output_tokens
        section["total_tokens"] = input_tokens + output_tokens
        if section["scored_count"]:
            section["avg_tokens_per_case"] = round(
                section["total_tokens"] / section["scored_count"], 1
            )
        cost = section.pop("_cost")
        if cost is not None:
            section["cost_usd"] = round(cost, 6)
    return sections


def _empty_section() -> dict:
    return {"count": 0, "scored_count": 0, "error_count": 0}


def build_report(results: list[CaseResult], options: EvalOptions) -> dict:
    sections = aggregate_cases(results)
    error_count = sum(1 for result in results if result.status != "ok")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "retrieval": sections.get("retrieval", _empty_section()),
        "customer_service": sections.get("customer_service", _empty_section()),
        "analytics": sections.get("analytics", _empty_section()),
        "robustness": sections.get("robustness", _empty_section()),
        "summary": {
            "case_count": len(results),
            "scored_count": len(results) - error_count,
            "error_count": error_count,
        },
        "run_meta": versions.run_meta(),
        "options": {
            "datasets": options.datasets,
            "tags": options.tags,
            "difficulty": options.difficulty,
            "limit": options.limit,
            "judge": options.judge,
            "top_k": options.top_k,
        },
        "cases": [asdict(result) for result in results],
    }
    return report


def load_thresholds(path: Path | str | None = None) -> dict:
    """读取门禁阈值 JSON；CLI 与 API 共用同一份配置。"""
    return json.loads(Path(path or config.GATE_CONFIG).read_text(encoding="utf-8"))


def evaluate_gate(report: dict, thresholds: dict) -> dict:
    """按阈值判定门禁；返回 {passed, checks}，checks 保留每条判定明细。"""
    checks: list[dict] = []
    passed = True
    # 只校验本次实际选中的数据集：局部冒烟（--dataset retrieval）不应因未跑
    # 其他数据集而被判定为用例不足。
    selected = set((report.get("options") or {}).get("datasets") or ())

    for dataset, minimum in (thresholds.get("min_cases_per_dataset") or {}).items():
        if selected and dataset not in selected:
            continue
        actual = report.get(dataset, {}).get("count", 0)
        ok = actual >= minimum
        passed = passed and ok
        checks.append(
            {
                "name": f"min_cases:{dataset}",
                "ok": ok,
                "detail": f"{actual} >= {minimum}",
            }
        )

    max_error_rate = thresholds.get("max_error_rate")
    if max_error_rate is not None:
        summary = report.get("summary", {})
        total = summary.get("case_count", 0)
        errors = summary.get("error_count", 0)
        rate = errors / total if total else 0.0
        ok = rate <= max_error_rate
        passed = passed and ok
        checks.append(
            {
                "name": "max_error_rate",
                "ok": ok,
                "detail": f"{rate:.3f} <= {max_error_rate}",
            }
        )

    for dataset, metrics in (thresholds.get("metrics") or {}).items():
        if selected and dataset not in selected:
            continue
        section = report.get(dataset, {})
        for metric, minimum in metrics.items():
            value = section.get(metric)
            ok = value is not None and value >= minimum
            passed = passed and ok
            shown = "缺失" if value is None else f"{value:.3f}"
            checks.append(
                {
                    "name": f"{dataset}:{metric}",
                    "ok": ok,
                    "detail": f"{shown} >= {minimum}",
                }
            )

    return {"passed": passed, "checks": checks}
