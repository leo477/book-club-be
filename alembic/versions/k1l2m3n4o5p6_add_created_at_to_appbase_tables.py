"""add created_at to AppBase tables missing the column

Backfills the ``created_at`` column on tables whose models inherit from
``AppBase`` (which provides ``id`` + ``created_at`` via ``TimestampMixin``)
but whose CREATE TABLE migrations predated that mixin:

  - ``club_members``    (backfilled from existing ``joined_at``)
  - ``club_bans``       (backfilled from existing ``banned_at``)
  - ``quiz_sessions``   (backfilled from existing ``started_at``)
  - ``quiz_questions``  (no sensible existing timestamp; backfilled with ``now()``)

The root cause of the production 500 on ``GET /api/v1/clubs/{id}/members``
was ``UndefinedColumnError: column club_members.created_at does not exist``.
This migration brings the database in line with the ORM models without
modifying any model code.

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-05-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k1l2m3n4o5p6"
down_revision: str | Sequence[str] | None = "j0k1l2m3n4o5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, backfill_source) — backfill_source is the name of an existing column
# on the table that represents the same logical "creation" moment, or ``None``
# if no such column exists (in which case ``now()`` is used).
_TABLES: tuple[tuple[str, str | None], ...] = (
    ("club_members", "joined_at"),
    ("club_bans", "banned_at"),
    ("quiz_sessions", "started_at"),
    ("quiz_questions", None),
)


def upgrade() -> None:
    for table, source in _TABLES:
        # 1. Add as nullable so the ALTER doesn't fail on existing rows.
        op.add_column(
            table,
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )

        # 2. Backfill from a sibling timestamp where it makes semantic sense,
        #    otherwise fall back to ``now()``.
        if source is not None:
            op.execute(
                sa.text(
                    f"UPDATE {table} "  # noqa: S608 — table name is a literal from _TABLES
                    f"SET created_at = {source} "
                    f"WHERE created_at IS NULL"
                )
            )
        else:
            op.execute(
                sa.text(
                    f"UPDATE {table} "  # noqa: S608 — table name is a literal from _TABLES
                    f"SET created_at = now() "
                    f"WHERE created_at IS NULL"
                )
            )

        # 3. Enforce NOT NULL and attach a server default for future inserts.
        op.alter_column(
            table,
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        )


def downgrade() -> None:
    for table, _ in reversed(_TABLES):
        op.drop_column(table, "created_at")
