"""add support_submissions table

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-06-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "s9t0u1v2w3x4"
down_revision: str | None = "r8s9t0u1v2w3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # create_type=False: the types are created explicitly below with checkfirst,
    # so create_table must NOT emit a second CREATE TYPE (would fail on Postgres).
    type_enum = postgresql.ENUM(
        "complaint", "suggestion", "comment", name="support_submission_type_enum", create_type=False
    )
    status_enum = postgresql.ENUM(
        "open",
        "pending",
        "approved",
        "rejected",
        "in_progress",
        "done",
        name="support_submission_status_enum",
        create_type=False,
    )
    type_enum.create(op.get_bind(), checkfirst=True)
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "support_submissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("type", type_enum, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_submissions_author_id", "support_submissions", ["author_id"], unique=False)
    op.create_index("ix_support_submissions_type", "support_submissions", ["type"], unique=False)
    op.create_index("ix_support_submissions_status", "support_submissions", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_support_submissions_status", table_name="support_submissions")
    op.drop_index("ix_support_submissions_type", table_name="support_submissions")
    op.drop_index("ix_support_submissions_author_id", table_name="support_submissions")
    op.drop_table("support_submissions")
    sa.Enum(name="support_submission_status_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="support_submission_type_enum").drop(op.get_bind(), checkfirst=True)
