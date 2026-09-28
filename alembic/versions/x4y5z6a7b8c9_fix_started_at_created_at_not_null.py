"""enforce NOT NULL on quiz_sessions.started_at and chat_room_bans.created_at

f6a7b8c9d0e1 created both columns with a server_default but without
nullable=False, so the DB schema allowed NULL while the ORM models
(QuizSession.started_at, ChatRoomBan.created_at via TimestampMixin) declare
them as non-nullable. This drift was invisible until CI started running
`alembic check` against the models. Both columns have a `server_default`
of `now()`, so every row inserted through normal application code already
has a value — this migration only needs to backfill any pre-existing NULLs
(there should be none) before enforcing the constraint.

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-09-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "x4y5z6a7b8c9"
down_revision: str | Sequence[str] | None = "w3x4y5z6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("quiz_sessions", "started_at"),
    ("chat_room_bans", "created_at"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(
            sa.text(
                f"UPDATE {table} SET {column} = now() WHERE {column} IS NULL"  # noqa: S608 — literal from _COLUMNS
            )
        )
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            existing_server_default=sa.text("now()"),
        )


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
            existing_server_default=sa.text("now()"),
        )
