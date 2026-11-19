"""评测接口：后台触发 run、查询 run/case、对比两次 run（admin-only）。

完整评测耗时数分钟，POST 只负责创建 run 并交给 BackgroundTasks；
后台任务状态全部落库，进程重启由 heartbeat + reaper 兜底。
生产环境更稳妥的做法是由独立 worker/CLI 执行，接口只做查询。
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.api.deps import require_roles
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.eval import (
    EvalCaseListOut,
    EvalCompareOut,
    EvalRunCreate,
    EvalRunCreated,
    EvalRunListOut,
    EvalRunOut,
)
from app.services import eval_service
from eval import config, datasets, engine

router = APIRouter(prefix="/eval", tags=["eval"])


def _to_options(body: EvalRunCreate) -> engine.EvalOptions:
    names = (
        list(datasets.DATASETS)
        if body.dataset == "all"
        else [body.dataset]
    )
    return engine.EvalOptions(
        datasets=names,
        tags=body.tags,
        difficulty=body.difficulty,
        limit=body.limit,
        judge=body.judge,
        top_k=config.RETRIEVAL_TOP_K,
    )


@router.post("/runs", response_model=EvalRunCreated, status_code=202)
async def create_run(
    body: EvalRunCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_roles("admin")),
) -> EvalRunCreated:
    try:
        run = await eval_service.create_run(
            _to_options(body),
            triggered_by="api",
            user_id=user.id,
            gate_thresholds=engine.load_thresholds() if body.gate else None,
        )
    except eval_service.ActiveRunError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    background_tasks.add_task(eval_service.execute_run, run.run_id)
    return EvalRunCreated(run_id=run.run_id, status=run.status)


@router.get("/runs", response_model=EvalRunListOut)
async def list_runs(
    dataset: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db=Depends(get_db),
    _user: User = Depends(require_roles("admin")),
) -> EvalRunListOut:
    rows, total = await eval_service.list_runs(
        db, dataset=dataset, status=status, limit=limit, offset=offset
    )
    return EvalRunListOut(items=rows, total=total)


@router.get("/runs/{run_id}", response_model=EvalRunOut)
async def get_run(
    run_id: str,
    db=Depends(get_db),
    _user: User = Depends(require_roles("admin")),
) -> EvalRunOut:
    run = await eval_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(404, "run 不存在")
    return run


@router.get("/runs/{run_id}/cases", response_model=EvalCaseListOut)
async def list_cases(
    run_id: str,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db=Depends(get_db),
    _user: User = Depends(require_roles("admin")),
) -> EvalCaseListOut:
    if await eval_service.get_run(db, run_id) is None:
        raise HTTPException(404, "run 不存在")
    rows, total = await eval_service.list_cases(
        db, run_id, status=status, limit=limit, offset=offset
    )
    return EvalCaseListOut(items=rows, total=total)


@router.get("/runs/{run_id}/compare/{base_run_id}", response_model=EvalCompareOut)
async def compare_runs(
    run_id: str,
    base_run_id: str,
    db=Depends(get_db),
    _user: User = Depends(require_roles("admin")),
) -> EvalCompareOut:
    try:
        result = await eval_service.compare_runs(db, base_run_id, run_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return EvalCompareOut(**result)
