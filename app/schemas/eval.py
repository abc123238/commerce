"""评测接口的请求/响应模型（admin 内部接口）。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DatasetName = Literal[
    "all", "retrieval", "customer_service", "analytics", "robustness"
]
Difficulty = Literal["easy", "medium", "hard"]


class EvalRunCreate(BaseModel):
    dataset: DatasetName = "all"
    tags: list[str] = Field(default_factory=list)
    difficulty: Difficulty | None = None
    limit: int | None = Field(default=None, ge=1)
    judge: bool = True
    gate: bool = True


class EvalRunCreated(BaseModel):
    run_id: str
    status: str


class EvalRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    triggered_by: str
    git_commit: str | None = None
    runner_version: str
    agent_model: str | None = None
    judge_model: str | None = None
    judge_prompt_version: str | None = None
    top_k: int | None = None
    judge_enabled: bool
    case_count: int
    scored_count: int
    error_count: int
    gate_passed: bool | None = None
    gate_details: dict | None = None
    aggregate: dict | None = None
    error_message: str | None = None


class EvalRunListOut(BaseModel):
    items: list[EvalRunOut]
    total: int


class EvalCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset: str
    case_id: str
    question: str
    user_role: str | None = None
    status: str
    expected: dict | None = None
    actual_tools: list | None = None
    tool_events: list | None = None
    retrieved: list | None = None
    answer: str | None = None
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
    latency_ms: int
    error_type: str | None = None
    error_message: str | None = None


class EvalCaseListOut(BaseModel):
    items: list[EvalCaseOut]
    total: int


class EvalCompareOut(BaseModel):
    base_run_id: str
    current_run_id: str
    aggregate_delta: dict
    case_changes: dict
