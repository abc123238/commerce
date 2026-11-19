"""add eval tables

Revision ID: b1f2c3d4e5a6
Revises: a2d8830bbaf5
Create Date: 2026-11-03 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b1f2c3d4e5a6'
down_revision: Union[str, Sequence[str], None] = 'a2d8830bbaf5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_by", sa.String(length=20), nullable=False, server_default="cli"),
        sa.Column("triggered_by_user_id", sa.Integer(), nullable=True),
        sa.Column("git_commit", sa.String(length=40), nullable=True),
        sa.Column("runner_version", sa.String(length=20), nullable=False, server_default="1"),
        sa.Column("agent_model", sa.String(length=100), nullable=True),
        sa.Column("judge_model", sa.String(length=100), nullable=True),
        sa.Column("agent_prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("judge_prompt_version", sa.String(length=20), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=True),
        sa.Column("judge_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dataset_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("aggregate", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scored_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gate_passed", sa.Boolean(), nullable=True),
        sa.Column("gate_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_eval_runs_run_id", "eval_runs", ["run_id"], unique=True)
    op.create_index("ix_eval_runs_status", "eval_runs", ["status"], unique=False)
    # 同一时刻仅允许一个 pending/running run；partial unique index 是并发闸门
    op.create_index(
        "uq_eval_runs_single_active",
        "eval_runs",
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','running')"),
    )

    op.create_table(
        "eval_case_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("dataset", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("user_role", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ok"),
        sa.Column("expected", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("actual_tools", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tool_events", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("retrieved", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("judge_correctness", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("judge_faithfulness", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("judge_reason", sa.Text(), nullable=True),
        sa.Column("judge_error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_type", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["eval_runs.run_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "dataset", "case_id", name="uq_eval_case_run_dataset_case"),
    )
    op.create_index("ix_eval_case_run_dataset", "eval_case_results", ["run_id", "dataset"], unique=False)
    op.create_index("ix_eval_case_run_status", "eval_case_results", ["run_id", "status"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_eval_case_run_status", table_name="eval_case_results")
    op.drop_index("ix_eval_case_run_dataset", table_name="eval_case_results")
    op.drop_table("eval_case_results")
    op.drop_index("uq_eval_runs_single_active", table_name="eval_runs")
    op.drop_index("ix_eval_runs_status", table_name="eval_runs")
    op.drop_index("ix_eval_runs_run_id", table_name="eval_runs")
    op.drop_table("eval_runs")
