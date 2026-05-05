"""add quiz sessions, question position, club extended fields, chat room bans

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── clubs: extended fields ────────────────────────────────────────────────
    op.add_column("clubs", sa.Column("status", sa.String(50), nullable=False, server_default="active"))
    op.add_column("clubs", sa.Column("city", sa.String(200), nullable=True))
    op.add_column("clubs", sa.Column("next_meeting_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("clubs", sa.Column("address", sa.String(500), nullable=True))
    op.add_column("clubs", sa.Column("lat", sa.Float(), nullable=True))
    op.add_column("clubs", sa.Column("lng", sa.Float(), nullable=True))
    op.add_column("clubs", sa.Column("theme", sa.String(200), nullable=True))
    op.add_column("clubs", sa.Column("current_book", sa.String(500), nullable=True))
    op.add_column("clubs", sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"))
    op.add_column("clubs", sa.Column("meeting_duration_minutes", sa.Integer(), nullable=True))
    op.add_column("clubs", sa.Column("after_meeting_venue", postgresql.JSONB(), nullable=True))
    op.add_column("clubs", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))

    # ── quiz_questions: position for ordering ─────────────────────────────────
    op.add_column("quiz_questions", sa.Column("position", sa.Integer(), nullable=False, server_default="0"))

    # ── quiz_sessions: new table ──────────────────────────────────────────────
    op.create_table(
        "quiz_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("quiz_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("quizzes.id"), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=True),
        sa.Column("started_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_quiz_sessions_quiz_id", "quiz_sessions", ["quiz_id"])

    # ── chat_room_bans: new table ─────────────────────────────────────────────
    op.create_table(
        "chat_room_bans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("room_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chat_rooms.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("banned_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("banned_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chat_room_bans_room_id", "chat_room_bans", ["room_id"])


def downgrade() -> None:
    op.drop_table("chat_room_bans")
    op.drop_table("quiz_sessions")

    op.drop_column("quiz_questions", "position")

    op.drop_column("clubs", "cancelled_at")
    op.drop_column("clubs", "after_meeting_venue")
    op.drop_column("clubs", "meeting_duration_minutes")
    op.drop_column("clubs", "tags")
    op.drop_column("clubs", "current_book")
    op.drop_column("clubs", "theme")
    op.drop_column("clubs", "lng")
    op.drop_column("clubs", "lat")
    op.drop_column("clubs", "address")
    op.drop_column("clubs", "next_meeting_date")
    op.drop_column("clubs", "city")
    op.drop_column("clubs", "status")
