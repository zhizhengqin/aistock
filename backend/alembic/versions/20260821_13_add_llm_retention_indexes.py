"""Add narrow indexes for stale outbox recovery and retention reservations."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_13"
down_revision: Union[str, None] = "20260813_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_task_outbox_locked_at",
        "task_outbox",
        ["locked_at", "id"],
        postgresql_where=sa.text("status = 'locked' AND locked_at IS NOT NULL"),
    )
    op.create_index(
        "ix_llm_token_reservations_task_reserved",
        "llm_token_reservations",
        ["task_id"],
        postgresql_where=sa.text("status = 'reserved'"),
    )


def downgrade() -> None:
    op.drop_index("ix_llm_token_reservations_task_reserved", table_name="llm_token_reservations")
    op.drop_index("ix_task_outbox_locked_at", table_name="task_outbox")
