"""add winner fields and google_book_id to events table

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-05-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "p6q7r8s9t0u1"
down_revision: str | None = "o5p6q7r8s9t0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("events", sa.Column("has_winner", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("events", sa.Column("winner_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("events", sa.Column("winner_name", sa.String(200), nullable=True))
    op.add_column("events", sa.Column("google_book_id", sa.String(100), nullable=True))
    op.create_foreign_key(
        "fk_events_winner_id_users",
        "events",
        "users",
        ["winner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_events_winner_id", "events", ["winner_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_events_winner_id", table_name="events")
    op.drop_constraint("fk_events_winner_id_users", "events", type_="foreignkey")
    op.drop_column("events", "has_winner")
    op.drop_column("events", "winner_id")
    op.drop_column("events", "winner_name")
    op.drop_column("events", "google_book_id")
