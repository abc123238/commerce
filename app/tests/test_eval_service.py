"""评测 run 生命周期集成测试（需要 Postgres）：单实例闸门、落库、回收、对比。"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models.eval import EvalCaseResult, EvalRun
from app.services import eval_service
from eval import engine

pytestmark = pytest.mark.integration


def _options(**overrides) -> engine.EvalOptions:
    values = {
        "datasets": ["retrieval"],
        "tags": [],
        "difficulty": None,
        "limit": 2,
        "judge": False,
        "top_k": 6,
    }
    values.update(overrides)
    return engine.EvalOptions(**values)


@pytest.fixture
def patch_sessions(monkeypatch, session_factory):
    monkeypatch.setattr(eval_service, "SessionLocal", session_factory)
    return session_factory


async def test_create_run_snapshot_and_single_active(clean_db, patch_sessions):
    run = await eval_service.create_run(_options(), triggered_by="api", user_id=None)
    assert run.status == "pending"
    assert run.dataset_snapshot["fingerprint"]
    assert len(run.dataset_snapshot["cases"]["retrieval"]) == 2
    assert run.config_snapshot["options"]["datasets"] == ["retrieval"]

    with pytest.raises(eval_service.ActiveRunError):
        await eval_service.create_run(_options())


async def test_execute_run_persists_cases_and_aggregate(
    clean_db, patch_sessions, monkeypatch
):
    async def fake_run(options, case_sink=None):
        results = [
            engine.CaseResult(
                dataset="retrieval",
                case_id="retrieval-01",
                question="q1",
                user_role="customer",
                status="ok",
                expected={"golden_doc_ids": ["faq:x"]},
                metrics={"recall@k": 1.0, "mrr": 0.5},
            ),
            engine.CaseResult(
                dataset="retrieval",
                case_id="retrieval-02",
                question="q2",
                user_role="customer",
                status="error",
                expected={},
                error_type="RuntimeError",
                error_message="boom",
            ),
        ]
        for result in results:
            if case_sink is not None:
                await case_sink(result)
        return engine.build_report(results, options)

    monkeypatch.setattr(eval_service.engine, "run_datasets", fake_run)

    run = await eval_service.create_run(_options())
    report = await eval_service.execute_run(run.run_id)

    assert report["summary"] == {
        "case_count": 2,
        "scored_count": 1,
        "error_count": 1,
    }
    rows = (
        await clean_db.scalars(
            select(EvalCaseResult).where(EvalCaseResult.run_id == run.run_id)
        )
    ).all()
    assert {row.case_id: row.status for row in rows} == {
        "retrieval-01": "ok",
        "retrieval-02": "error",
    }

    refreshed = await clean_db.scalar(
        select(EvalRun).where(EvalRun.run_id == run.run_id)
    )
    assert refreshed.status == "succeeded"
    assert refreshed.case_count == 2
    assert refreshed.scored_count == 1
    assert refreshed.error_count == 1
    assert refreshed.aggregate["retrieval"]["recall@k"] == 1.0


async def test_reap_stale_runs(clean_db, patch_sessions):
    old = datetime.now(timezone.utc) - timedelta(minutes=60)
    run = EvalRun(
        run_id=str(uuid4()),
        status="running",
        started_at=old,
        heartbeat_at=old,
    )
    clean_db.add(run)
    await clean_db.commit()

    count = await eval_service.reap_stale_runs(older_than_minutes=30)
    assert count == 1

    await clean_db.refresh(run)
    assert run.status == "failed"
    assert "心跳" in run.error_message


async def test_compare_runs_reports_metric_and_case_delta(clean_db):
    base_id, current_id = str(uuid4()), str(uuid4())
    clean_db.add_all(
        [
            EvalRun(
                run_id=base_id,
                status="succeeded",
                aggregate={"retrieval": {"count": 1, "recall@k": 0.5}},
            ),
            EvalRun(
                run_id=current_id,
                status="succeeded",
                aggregate={"retrieval": {"count": 1, "recall@k": 0.8}},
            ),
        ]
    )
    await clean_db.flush()
    clean_db.add_all(
        [
            EvalCaseResult(
                run_id=base_id,
                dataset="retrieval",
                case_id="retrieval-01",
                question="q",
                status="ok",
                metrics={"recall@k": 0.5},
            ),
            EvalCaseResult(
                run_id=current_id,
                dataset="retrieval",
                case_id="retrieval-01",
                question="q",
                status="ok",
                metrics={"recall@k": 0.8},
            ),
        ]
    )
    await clean_db.commit()

    result = await eval_service.compare_runs(clean_db, base_id, current_id)
    assert result["aggregate_delta"]["retrieval"]["recall@k"]["delta"] == 0.3
    assert result["case_changes"]["improved"][0]["case_id"] == "retrieval-01"
