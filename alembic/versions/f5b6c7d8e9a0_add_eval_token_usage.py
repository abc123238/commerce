"""add eval token usage and cost

Revision ID: f5b6c7d8e9a0
Revises: e4a5b6c7d8e9
Create Date: 2026-11-06 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5b6c7d8e9a0"
down_revision: Union[str, Sequence[str], None] = "e4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    for column in (
        "agent_input_tokens",
        "agent_output_tokens",
        "judge_input_tokens",
        "judge_output_tokens",
    ):
        op.add_column(
            "eval_case_results",
            sa.Column(column, sa.Integer(), nullable=False, server_default="0"),
        )
    op.add_column(
        "eval_case_results",
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("eval_case_results", "cost_usd")
    op.drop_column("eval_case_results", "judge_output_tokens")
    op.drop_column("eval_case_results", "judge_input_tokens")
    op.drop_column("eval_case_results", "agent_output_tokens")
    op.drop_column("eval_case_results", "agent_input_tokens")
