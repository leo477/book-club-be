"""add support_submission_likes table

Revision ID: t0u1v2w3x4y5
Revises: s9t0u1v2w3x4
Create Date: 2026-07-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "t0u1v2w3x4y5"
down_revision: str | None = "s9t0u1v2w3x4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "support_submission_likes",
        sa.Column("submission_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["support_submissions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("submission_id", "user_id"),
    )
    op.create_index(
        "ix_support_submission_likes_submission_id", "support_submission_likes", ["submission_id"], unique=False
    )
    op.create_index("ix_support_submission_likes_user_id", "support_submission_likes", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_support_submission_likes_user_id", table_name="support_submission_likes")
    op.drop_index("ix_support_submission_likes_submission_id", table_name="support_submission_likes")
    op.drop_table("support_submission_likes")
