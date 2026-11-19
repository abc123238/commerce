"""评测运行与逐用例结果模型。

run 级保存配置/数据快照与聚合指标，case 级保存单条用例的输入、
实际工具轨迹、检索证据、judge 理由与错误信息，便于历史对比与失败归因。
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        # 数据库级单实例闸门：同一时刻只允许一个 pending/running 的 run。
        # 相比 Redis 锁无需 TTL 续期，跨进程/跨实例天然成立。
        Index(
            "uq_eval_runs_single_active",
            text("(true)"),
            unique=True,
            postgresql_where=text("status IN ('pending','running')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # 对外暴露的 UUID；case 表按它关联，避免泄露自增 id 语义
    run_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    triggered_by: Mapped[str] = mapped_column(String(20), default="cli")
    triggered_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    git_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    runner_version: Mapped[str] = mapped_column(String(20), default="1")
    agent_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    judge_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    agent_prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    judge_prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    judge_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    config_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    dataset_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    aggregate: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    case_count: Mapped[int] = mapped_column(Integer, default=0)
    scored_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)

    gate_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    gate_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    cases: Mapped[list["EvalCaseResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class EvalCaseResult(Base):
    __tablename__ = "eval_case_results"
    __table_args__ = (
        UniqueConstraint("run_id", "dataset", "case_id", name="uq_eval_case_run_dataset_case"),
        Index("ix_eval_case_run_dataset", "run_id", "dataset"),
        Index("ix_eval_case_run_status", "run_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("eval_runs.run_id", ondelete="CASCADE")
    )
    dataset: Mapped[str] = mapped_column(String(40))
    case_id: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    user_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # ok / error：区分「真 0 分」与「运行失败」
    status: Mapped[str] = mapped_column(String(20), default="ok")

    expected: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    actual_tools: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    tool_events: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    retrieved: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    judge_correctness: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    judge_faithfulness: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    judge_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    agent_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    agent_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    judge_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    judge_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)

    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped[EvalRun] = relationship(back_populates="cases")
