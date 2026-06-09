"""add club_join_requests table

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-06-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "q7r8s9t0u1v2"
down_revision: str | None = "p6q7r8s9t0u1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status_enum = sa.Enum("pending", "approved", "rejected", name="club_join_request_status_enum")
    source_enum = sa.Enum("manual", "event", name="club_join_request_source_enum")
    status_enum.create(op.get_bind(), checkfirst=True)
    source_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "club_join_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("club_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("status", status_enum, server_default=sa.text("'pending'"), nullable=False),
        sa.Column("source", source_enum, server_default=sa.text("'manual'"), nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("club_id", "user_id", name="uq_club_join_request"),
    )
    op.create_index("ix_club_join_requests_club_id", "club_join_requests", ["club_id"], unique=False)
    op.create_index("ix_club_join_requests_user_id", "club_join_requests", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_club_join_requests_user_id", table_name="club_join_requests")
    op.drop_index("ix_club_join_requests_club_id", table_name="club_join_requests")
    op.drop_table("club_join_requests")
    sa.Enum(name="club_join_request_source_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="club_join_request_status_enum").drop(op.get_bind(), checkfirst=True)
