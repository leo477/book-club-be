"""add expires_at to club_bans

Adds the ``expires_at`` column to ``club_bans``, defined in the
``ClubBan`` SQLAlchemy model (``app/models/club_ban.py``) as::

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

The column was present in the ORM model but was never included in any
previous migration, causing the production 500 on
``GET /api/v1/clubs/{club_id}/bans``::

    UndefinedColumnError: column club_bans.expires_at does not exist

This migration brings the database schema in line with the ORM model
without modifying any model code.

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-05-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "m3n4o5p6q7r8"
down_revision: str | Sequence[str] | None = "l2m3n4o5p6q7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "club_bans",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("club_bans", "expires_at")
