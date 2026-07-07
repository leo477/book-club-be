"""add book vote rounds, options and votes tables

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-07-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "w3x4y5z6a7b8"
down_revision: str | None = "v2w3x4y5z6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status_enum = postgresql.ENUM("open", "closed", name="book_vote_round_status_enum", create_type=False)
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "book_vote_rounds",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("club_id", sa.UUID(), nullable=False),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("winner_option_id", sa.UUID(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_book_vote_rounds_club_id", "book_vote_rounds", ["club_id"], unique=False)

    op.create_table(
        "book_vote_options",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("round_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("author", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(["round_id"], ["book_vote_rounds.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_book_vote_options_round_id", "book_vote_options", ["round_id"], unique=False)

    op.create_table(
        "book_vote_votes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("round_id", sa.UUID(), nullable=False),
        sa.Column("option_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["round_id"], ["book_vote_rounds.id"]),
        sa.ForeignKeyConstraint(["option_id"], ["book_vote_options.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", "user_id", name="uq_book_vote_round_user"),
    )
    op.create_index("ix_book_vote_votes_round_id", "book_vote_votes", ["round_id"], unique=False)
    op.create_index("ix_book_vote_votes_option_id", "book_vote_votes", ["option_id"], unique=False)
    op.create_index("ix_book_vote_votes_user_id", "book_vote_votes", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_book_vote_votes_user_id", table_name="book_vote_votes")
    op.drop_index("ix_book_vote_votes_option_id", table_name="book_vote_votes")
    op.drop_index("ix_book_vote_votes_round_id", table_name="book_vote_votes")
    op.drop_table("book_vote_votes")
    op.drop_index("ix_book_vote_options_round_id", table_name="book_vote_options")
    op.drop_table("book_vote_options")
    op.drop_index("ix_book_vote_rounds_club_id", table_name="book_vote_rounds")
    op.drop_table("book_vote_rounds")
    sa.Enum(name="book_vote_round_status_enum").drop(op.get_bind(), checkfirst=True)
