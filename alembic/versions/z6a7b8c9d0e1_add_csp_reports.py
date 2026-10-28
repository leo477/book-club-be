"""add csp_reports table

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
Create Date: 2026-10-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "z6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "y5z6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "csp_reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("env", sa.String(length=8), nullable=False),
        sa.Column("route", sa.String(length=120), nullable=False),
        sa.Column("directive", sa.String(length=64), nullable=False),
        sa.Column("disposition", sa.String(length=16), nullable=False),
        sa.Column("sink", sa.String(length=48), server_default="", nullable=False),
        sa.Column("blocked", sa.String(length=128), server_default="", nullable=False),
        sa.Column("policy_name", sa.String(length=64), server_default="", nullable=False),
        sa.Column("sample", sa.String(length=120), server_default="", nullable=False),
        sa.Column("count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "day",
            "env",
            "route",
            "directive",
            "disposition",
            "sink",
            "blocked",
            "policy_name",
            name="uq_csp_reports_key",
        ),
    )
    op.create_index("ix_csp_reports_created_at", "csp_reports", ["created_at"], unique=False)
    op.create_index("ix_csp_reports_day", "csp_reports", ["day"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_csp_reports_day", table_name="csp_reports")
    op.drop_index("ix_csp_reports_created_at", table_name="csp_reports")
    op.drop_table("csp_reports")
