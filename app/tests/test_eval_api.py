"""评测接口集成测试（需要 Postgres）：admin 鉴权、后台触发、查询与对比。"""
import uuid

import pytest
from sqlalchemy import select

from app.db.models.eval import EvalCaseResult, EvalRun
from app.db.models.user import User
from app.services import eval_service

pytestmark = pytest.mark.integration

_AUTH = "/api/v1/auth"
_EVAL = "/api/v1/eval"


async def _admin_headers(client, db_session) -> dict:
    email = f"eval_{uuid.uuid4().hex[:8]}@example.com"
    response = await client.post(
        f"{_AUTH}/register", json={"email": email, "password": "Test@123456"}
    )
    assert response.status_code == 201
    user = await db_session.scalar(select(User).where(User.email == email))
    user.role = "admin"
    await db_session.commit()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_eval_endpoints_require_admin(client, clean_db):
    email = f"eval_{uuid.uuid4().hex[:8]}@example.com"
    response = await client.post(
        f"{_AUTH}/register", json={"email": email, "password": "Test@123456"}
    )
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    resp = await client.post(f"{_EVAL}/runs", json={}, headers=headers)
    assert resp.status_code == 403
    assert (await client.get(f"{_EVAL}/runs", headers=headers)).status_code == 403


async def test_create_run_returns_202_and_rejects_concurrent(
    client, db_session, clean_db, session_factory, monkeypatch
):
    headers = await _admin_headers(client, db_session)
    monkeypatch.setattr(eval_service, "SessionLocal", session_factory)

    async def _noop(run_id: str) -> None:
        return None

    monkeypatch.setattr(eval_service, "execute_run", _noop)

    payload = {"dataset": "retrieval", "limit": 2, "judge": False}
    first = await client.post(f"{_EVAL}/runs", json=payload, headers=headers)
    assert first.status_code == 202
    run_id = first.json()["run_id"]
    assert first.json()["status"] == "pending"

    second = await client.post(f"{_EVAL}/runs", json=payload, headers=headers)
    assert second.status_code == 409

    detail = await client.get(f"{_EVAL}/runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["run_id"] == run_id

    cases = await client.get(f"{_EVAL}/runs/{run_id}/cases", headers=headers)
    assert cases.status_code == 200
    assert cases.json() == {"items": [], "total": 0}

    assert (await client.get(f"{_EVAL}/runs/missing", headers=headers)).status_code == 404


async def test_list_cases_and_compare(client, db_session, clean_db):
    headers = await _admin_headers(client, db_session)
    base_id, current_id = str(uuid.uuid4()), str(uuid.uuid4())
    db_session.add_all(
        [
            EvalRun(
                run_id=base_id,
                status="succeeded",
                aggregate={"retrieval": {"count": 1, "recall@k": 0.4}},
            ),
            EvalRun(
                run_id=current_id,
                status="succeeded",
                aggregate={"retrieval": {"count": 1, "recall@k": 0.9}},
            ),
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            EvalCaseResult(
                run_id=base_id,
                dataset="retrieval",
                case_id="retrieval-01",
                question="q",
                status="ok",
                metrics={"recall@k": 0.4},
            ),
            EvalCaseResult(
                run_id=current_id,
                dataset="retrieval",
                case_id="retrieval-01",
                question="q",
                status="ok",
                metrics={"recall@k": 0.9},
                judge_reason="覆盖了关键要点",
            ),
        ]
    )
    await db_session.commit()

    cases = await client.get(
        f"{_EVAL}/runs/{current_id}/cases", headers=headers
    )
    assert cases.json()["total"] == 1
    assert cases.json()["items"][0]["judge_reason"] == "覆盖了关键要点"

    listed = await client.get(
        f"{_EVAL}/runs", params={"dataset": "retrieval"}, headers=headers
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 2

    compare = await client.get(
        f"{_EVAL}/runs/{current_id}/compare/{base_id}", headers=headers
    )
    assert compare.status_code == 200
    body = compare.json()
    assert body["aggregate_delta"]["retrieval"]["recall@k"]["delta"] == 0.5
    assert body["case_changes"]["improved"][0]["case_id"] == "retrieval-01"
