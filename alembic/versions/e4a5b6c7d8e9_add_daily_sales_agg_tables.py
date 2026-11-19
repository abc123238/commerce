"""add daily sales agg tables

Revision ID: e4a5b6c7d8e9
Revises: d3f4a5b6c7d8
Create Date: 2026-11-03 10:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4a5b6c7d8e9'
down_revision: Union[str, Sequence[str], None] = 'd3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "daily_sales_agg",
        sa.Column("stat_date", sa.Date(), primary_key=True),
        sa.Column("revenue", sa.Numeric(precision=14, scale=2), nullable=False, server_default="0"),
        sa.Column("order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "daily_category_sales_agg",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("revenue", sa.Numeric(precision=14, scale=2), nullable=False, server_default="0"),
        sa.Column("order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("stat_date", "category_id", name="uq_daily_category_sales_date_category"),
    )
    op.create_index(
        "ix_daily_category_sales_date",
        "daily_category_sales_agg",
        ["stat_date"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_daily_category_sales_date", table_name="daily_category_sales_agg")
    op.drop_table("daily_category_sales_agg")
    op.drop_table("daily_sales_agg")
