"""create order status events

Revision ID: c2e3f4a5b6c7
Revises: b1f2c3d4e5a6
Create Date: 2026-11-03 10:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'b1f2c3d4e5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "order_status_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("event", sa.String(length=30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="api"),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("order_id", "to_status", name="uq_order_status_events_order_to"),
    )
    op.create_index(
        "ix_order_status_events_order_time",
        "order_status_events",
        ["order_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_order_status_events_status_time",
        "order_status_events",
        ["to_status", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_order_status_events_status_time", table_name="order_status_events")
    op.drop_index("ix_order_status_events_order_time", table_name="order_status_events")
    op.drop_table("order_status_events")
