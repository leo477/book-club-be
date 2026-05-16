"""add created_at to chat_messages

Backfills the ``created_at`` column on ``chat_messages``, whose model inherits
from ``AppBase`` (which provides ``id`` + ``created_at`` via ``TimestampMixin``)
but whose CREATE TABLE migration predated that mixin.

  - ``chat_messages``  (backfilled from existing ``timestamp`` column, which
                        carries the same semantic meaning — the moment the row
                        was created)

The root cause of the production 500 on
``GET /api/v1/chat/rooms/{id}/messages`` was
``UndefinedColumnError: column chat_messages.created_at does not exist``.
This migration brings the database in line with the ORM model without
modifying any model code.

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-05-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "l2m3n4o5p6q7"
down_revision: str | Sequence[str] | None = "k1l2m3n4o5p6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add as nullable so the ALTER doesn't fail on existing rows.
    op.add_column(
        "chat_messages",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # 2. Backfill from the existing ``timestamp`` column, which semantically
    #    represents the same moment (message creation time).
    op.execute(
        sa.text(
            "UPDATE chat_messages "
            'SET created_at = "timestamp" '
            "WHERE created_at IS NULL"
        )
    )

    # 3. Enforce NOT NULL and attach a server default for future inserts.
    op.alter_column(
        "chat_messages",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "created_at")
