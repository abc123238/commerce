"""评测 run 生命周期：创建、原子认领、逐用例落库、心跳、回收与对比。

单实例运行由 eval_runs 上的 partial unique index 保证（跨进程/跨实例），
进程重启后用 heartbeat + reaper 释放槽位，不依赖内存状态。
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.db.models.eval import EvalCaseResult, EvalRun
from app.db.session import SessionLocal
from eval import datasets, engine, versions

logger = get_logger("eval")

_ACTIVE_STATUSES = ("pending", "running")
_NUMERIC = (int, float)


class ActiveRunError(RuntimeError):
    """已存在 pending/running 的 run，无法创建新 run。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def reap_stale_runs(older_than_minutes: int | None = None) -> int:
    minutes = older_than_minutes or settings.eval_run_stale_minutes
    cutoff = _now() - timedelta(minutes=minutes)
    async with SessionLocal() as db:
        result = await db.execute(
            update(EvalRun)
            .where(
                EvalRun.status.in_(_ACTIVE_STATUSES),
                func.coalesce(EvalRun.heartbeat_at, EvalRun.created_at) < cutoff,
            )
            .values(
                status="failed",
                finished_at=_now(),
                error_message=f"超时未更新心跳（>{minutes} 分钟），已由 reaper 回收",
            )
        )
        await db.commit()
    count = result.rowcount or 0
    if count:
        logger.warning("eval_runs_reaped", count=count)
    return count


async def _resolve_cases(options: engine.EvalOptions) -> dict[str, list[dict]]:
    cases = datasets.load_datasets(
        options.datasets,
        tags=options.tags,
        difficulty=options.difficulty,
        limit=options.limit,
    )
    if not any(cases.values()):
        raise ValueError("过滤后没有可执行的评测用例")
    return cases


async def create_run(
    options: engine.EvalOptions,
    *,
    triggered_by: str = "cli",
    user_id: int | None = None,
    gate_thresholds: dict | None = None,
) -> EvalRun:
    await reap_stale_runs()
    cases = await _resolve_cases(options)
    meta = versions.run_meta()

    async with SessionLocal() as db:
        run = EvalRun(
            run_id=str(uuid4()),
            status="pending",
            triggered_by=triggered_by,
            triggered_by_user_id=user_id,
            git_commit=meta["git_commit"],
            runner_version=meta["runner_version"],
            agent_model=meta["agent_model"],
            judge_model=meta["judge_model"],
            agent_prompt_hash=meta["agent_prompt_hash"],
            judge_prompt_version=meta["judge_prompt_version"],
            top_k=options.top_k,
            judge_enabled=options.judge,
            config_snapshot={
                "options": asdict(options),
                "gate_thresholds": gate_thresholds,
                "run_meta": meta,
            },
            dataset_snapshot={
                "fingerprint": datasets.fingerprint(cases),
                "cases": cases,
            },
        )
        db.add(run)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ActiveRunError(
                "已有进行中的评测 run，请等待完成或稍后重试"
            ) from exc
        return run


def _case_payload(run_id: str, result: engine.CaseResult) -> dict:
    return {
        "run_id": run_id,
        "dataset": result.dataset,
        "case_id": result.case_id,
        "question": result.question,
        "user_role": result.user_role,
        "status": result.status,
        "expected": result.expected,
        "actual_tools": result.actual_tools,
        "tool_events": result.tool_events,
        "retrieved": result.retrieved,
        "answer": result.answer,
        # rewritten_query 无独立列，并入 metrics JSONB；_score 只统计数值，不受影响。
        "metrics": {
            **(result.metrics or {}),
            **({"rewritten_query": result.rewritten_query} if result.rewritten_query else {}),
        },
        "judge_correctness": result.judge_correctness,
        "judge_faithfulness": result.judge_faithfulness,
        "judge_reason": result.judge_reason,
        "judge_error": result.judge_error,
        "agent_input_tokens": result.agent_input_tokens,
        "agent_output_tokens": result.agent_output_tokens,
        "judge_input_tokens": result.judge_input_tokens,
        "judge_output_tokens": result.judge_output_tokens,
        "cost_usd": result.cost_usd,
        "latency_ms": result.latency_ms,
        "error_type": result.error_type,
        "error_message": result.error_message,
    }


async def _claim(run_id: str) -> bool:
    async with SessionLocal() as db:
        result = await db.execute(
            update(EvalRun)
            .where(EvalRun.run_id == run_id, EvalRun.status == "pending")
            .values(status="running", started_at=_now(), heartbeat_at=_now())
            .returning(EvalRun.id)
        )
        claimed = result.first() is not None
        await db.commit()
    return claimed


async def execute_run(run_id: str) -> dict | None:
    """认领并执行一个 run；返回报告字典（已被认领/不存在时返回 None）。"""
    if not await _claim(run_id):
        return None

    async with SessionLocal() as db:
        run = await db.scalar(select(EvalRun).where(EvalRun.run_id == run_id))
        snapshot = (run.config_snapshot or {}) if run else {}
    if run is None:
        return None

    options = engine.EvalOptions(**snapshot.get("options", {}))
    gate_thresholds = snapshot.get("gate_thresholds")

    async def case_sink(result: engine.CaseResult) -> None:
        payload = _case_payload(run_id, result)
        stmt = (
            pg_insert(EvalCaseResult)
            .values(**payload)
            .on_conflict_do_update(
                constraint="uq_eval_case_run_dataset_case",
                set_={
                    key: payload[key]
                    for key in payload
                    if key not in ("run_id", "dataset", "case_id")
                },
            )
        )
        async with SessionLocal() as sink_db:
            await sink_db.execute(stmt)
            await sink_db.execute(
                update(EvalRun)
                .where(EvalRun.run_id == run_id)
                .values(heartbeat_at=_now())
            )
            await sink_db.commit()

    try:
        report = await engine.run_datasets(options, case_sink=case_sink)
        gate = (
            engine.evaluate_gate(report, gate_thresholds)
            if gate_thresholds is not None
            else None
        )
        if gate is not None:
            report["gate"] = gate

        aggregate = {
            key: report.get(key, {})
            for key in ("retrieval", "customer_service", "analytics", "robustness")
        }
        aggregate["summary"] = report.get("summary", {})
        summary = report.get("summary", {})

        async with SessionLocal() as finish_db:
            await finish_db.execute(
                update(EvalRun)
                .where(EvalRun.run_id == run_id)
                .values(
                    status="succeeded",
                    finished_at=_now(),
                    heartbeat_at=_now(),
                    aggregate=aggregate,
                    case_count=summary.get("case_count", 0),
                    scored_count=summary.get("scored_count", 0),
                    error_count=summary.get("error_count", 0),
                    gate_passed=gate.get("passed") if gate else None,
                    gate_details=gate,
                )
            )
            await finish_db.commit()
        logger.info("eval_run_succeeded", run_id=run_id)
        return report
    except Exception as exc:  # noqa: BLE001
        async with SessionLocal() as fail_db:
            await fail_db.execute(
                update(EvalRun)
                .where(EvalRun.run_id == run_id)
                .values(
                    status="failed",
                    finished_at=_now(),
                    error_message=str(exc)[:1000],
                )
            )
            await fail_db.commit()
        logger.error("eval_run_failed", run_id=run_id, exc=str(exc)[:300])
        raise


async def run_eval_now(
    options: engine.EvalOptions,
    *,
    triggered_by: str = "cli",
    user_id: int | None = None,
    gate_thresholds: dict | None = None,
) -> tuple[EvalRun, dict]:
    row = await create_run(
        options,
        triggered_by=triggered_by,
        user_id=user_id,
        gate_thresholds=gate_thresholds,
    )
    report = await execute_run(row.run_id)
    if report is None:
        raise RuntimeError(f"run {row.run_id} 无法认领（可能已被其他进程执行）")
    async with SessionLocal() as db:
        refreshed = await db.scalar(select(EvalRun).where(EvalRun.run_id == row.run_id))
    return refreshed, report


async def get_run(db: AsyncSession, run_id: str) -> EvalRun | None:
    return await db.scalar(select(EvalRun).where(EvalRun.run_id == run_id))


async def list_runs(
    db: AsyncSession,
    *,
    dataset: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[EvalRun], int]:
    query = select(EvalRun)
    count_query = select(func.count()).select_from(EvalRun)
    if dataset:
        run_ids = select(EvalCaseResult.run_id).where(
            EvalCaseResult.dataset == dataset
        )
        query = query.where(EvalRun.run_id.in_(run_ids))
        count_query = count_query.where(EvalRun.run_id.in_(run_ids))
    if status:
        query = query.where(EvalRun.status == status)
        count_query = count_query.where(EvalRun.status == status)
    query = query.order_by(EvalRun.created_at.desc()).limit(limit).offset(offset)
    rows = list((await db.scalars(query)).all())
    total = (await db.scalar(count_query)) or 0
    return rows, total


async def list_cases(
    db: AsyncSession,
    run_id: str,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[EvalCaseResult], int]:
    query = select(EvalCaseResult).where(EvalCaseResult.run_id == run_id)
    count_query = select(func.count()).select_from(EvalCaseResult).where(
        EvalCaseResult.run_id == run_id
    )
    if status:
        query = query.where(EvalCaseResult.status == status)
        count_query = count_query.where(EvalCaseResult.status == status)
    query = query.order_by(EvalCaseResult.dataset, EvalCaseResult.case_id)
    query = query.limit(limit).offset(offset)
    rows = list((await db.scalars(query)).all())
    total = (await db.scalar(count_query)) or 0
    return rows, total


def _score(metrics: dict | None) -> float | None:
    values = [float(v) for v in (metrics or {}).values() if isinstance(v, _NUMERIC)]
    return round(sum(values) / len(values), 4) if values else None


async def compare_runs(db: AsyncSession, base_run_id: str, current_run_id: str) -> dict:
    base = await get_run(db, base_run_id)
    current = await get_run(db, current_run_id)
    if base is None or current is None:
        raise ValueError("base/current run 不存在")

    base_cases = {
        (case.dataset, case.case_id): case
        for case in (
            await db.scalars(
                select(EvalCaseResult).where(EvalCaseResult.run_id == base_run_id)
            )
        ).all()
    }
    current_cases = {
        (case.dataset, case.case_id): case
        for case in (
            await db.scalars(
                select(EvalCaseResult).where(EvalCaseResult.run_id == current_run_id)
            )
        ).all()
    }

    aggregate_delta: dict[str, dict] = {}
    base_agg = base.aggregate or {}
    current_agg = current.aggregate or {}
    for dataset in ("retrieval", "customer_service", "analytics", "robustness"):
        base_section = base_agg.get(dataset) or {}
        current_section = current_agg.get(dataset) or {}
        metrics: dict[str, dict] = {}
        for metric in sorted(set(base_section) | set(current_section)):
            if metric in ("count", "scored_count", "error_count"):
                continue
            before, after = base_section.get(metric), current_section.get(metric)
            if not isinstance(before, _NUMERIC) and not isinstance(after, _NUMERIC):
                continue
            delta = None
            if isinstance(before, _NUMERIC) and isinstance(after, _NUMERIC):
                delta = round(float(after) - float(before), 4)
            metrics[metric] = {"base": before, "current": after, "delta": delta}
        if metrics:
            aggregate_delta[dataset] = metrics

    changed = {
        "improved": [],
        "regressed": [],
        "broken": [],
        "fixed": [],
        "unchanged": 0,
    }
    for key in sorted(set(base_cases) | set(current_cases)):
        before = base_cases.get(key)
        after = current_cases.get(key)
        dataset, case_id = key
        entry = {"dataset": dataset, "case_id": case_id}
        if after is not None and after.status != "ok":
            if before is not None and before.status == "ok":
                changed["broken"].append(entry)
            else:
                changed["unchanged"] += 1
            continue
        if after is None:
            changed["unchanged"] += 1
            continue
        if before is None:
            changed["improved"].append(entry)
            continue
        if before.status != "ok":
            changed["fixed"].append(entry)
            continue
        base_score, current_score = _score(before.metrics), _score(after.metrics)
        if base_score is None or current_score is None:
            changed["unchanged"] += 1
            continue
        delta = round(current_score - base_score, 4)
        entry.update(base=base_score, current=current_score, delta=delta)
        if delta > 0.01:
            changed["improved"].append(entry)
        elif delta < -0.01:
            changed["regressed"].append(entry)
        else:
            changed["unchanged"] += 1

    return {
        "base_run_id": base_run_id,
        "current_run_id": current_run_id,
        "aggregate_delta": aggregate_delta,
        "case_changes": changed,
    }
