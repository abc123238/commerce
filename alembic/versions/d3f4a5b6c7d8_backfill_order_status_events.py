"""backfill order status events

Revision ID: d3f4a5b6c7d8
Revises: c2e3f4a5b6c7
Create Date: 2026-11-03 10:10:00.000000

把存量订单的各阶段时间戳转成事件。单独成一个 revision：
DDL 与 DML 分离，便于在数据量较大时独立控制执行/回滚。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd3f4a5b6c7d8'
down_revision: Union[str, Sequence[str], None] = 'c2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    statements = (
        # 创建事件：created_at 一定有值
        """
        INSERT INTO order_status_events
            (order_id, from_status, to_status, event, occurred_at, source)
        SELECT id, NULL, 'created', 'created', created_at, 'backfill'
        FROM orders
        ON CONFLICT (order_id, to_status) DO NOTHING
        """,
        """
        INSERT INTO order_status_events
            (order_id, from_status, to_status, event, occurred_at, source)
        SELECT id, 'created', 'paid', 'paid', paid_at, 'backfill'
        FROM orders WHERE paid_at IS NOT NULL
        ON CONFLICT (order_id, to_status) DO NOTHING
        """,
        """
        INSERT INTO order_status_events
            (order_id, from_status, to_status, event, occurred_at, source)
        SELECT id, 'created', 'cancelled', 'cancelled', cancelled_at, 'backfill'
        FROM orders WHERE cancelled_at IS NOT NULL
        ON CONFLICT (order_id, to_status) DO NOTHING
        """,
        """
        INSERT INTO order_status_events
            (order_id, from_status, to_status, event, occurred_at, source)
        SELECT id, 'paid', 'shipped', 'shipped', shipped_at, 'backfill'
        FROM orders WHERE shipped_at IS NOT NULL
        ON CONFLICT (order_id, to_status) DO NOTHING
        """,
        """
        INSERT INTO order_status_events
            (order_id, from_status, to_status, event, occurred_at, source)
        SELECT id, 'shipped', 'completed', 'completed', completed_at, 'backfill'
        FROM orders WHERE completed_at IS NOT NULL
        ON CONFLICT (order_id, to_status) DO NOTHING
        """,
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM order_status_events WHERE source = 'backfill'")
