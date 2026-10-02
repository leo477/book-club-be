"""add analytics_events table

Revision ID: y5z6a7b8c9d0
Revises: x4y5z6a7b8c9
Create Date: 2026-10-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "y5z6a7b8c9d0"
down_revision: str | Sequence[str] | None = "x4y5z6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("app", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("bucket", sa.String(length=8), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=True),
        sa.Column("message", sa.String(length=120), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analytics_events_created_at", "analytics_events", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_analytics_events_created_at", table_name="analytics_events")
    op.drop_table("analytics_events")
