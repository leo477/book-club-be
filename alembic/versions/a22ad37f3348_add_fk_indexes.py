"""add_fk_indexes

Revision ID: a22ad37f3348
Revises: de70ba150c30
Create Date: 2026-04-16 19:14:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a22ad37f3348"
down_revision: str | Sequence[str] | None = "de70ba150c30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add FK indexes for foreign key columns."""
    op.create_index("ix_clubs_organizer_id", "clubs", ["organizer_id"], unique=False)
    op.create_index("ix_club_members_club_id", "club_members", ["club_id"], unique=False)
    op.create_index("ix_club_members_user_id", "club_members", ["user_id"], unique=False)
    op.create_index("ix_meetings_club_id", "meetings", ["club_id"], unique=False)
    op.create_index("ix_meeting_attendees_meeting_id", "meeting_attendees", ["meeting_id"], unique=False)
    op.create_index("ix_meeting_attendees_user_id", "meeting_attendees", ["user_id"], unique=False)
    op.create_index("ix_quizzes_club_id", "quizzes", ["club_id"], unique=False)
    op.create_index("ix_quizzes_created_by", "quizzes", ["created_by"], unique=False)
    op.create_index("ix_quiz_questions_quiz_id", "quiz_questions", ["quiz_id"], unique=False)
    op.create_index("ix_quiz_attempts_quiz_id", "quiz_attempts", ["quiz_id"], unique=False)
    op.create_index("ix_quiz_attempts_user_id", "quiz_attempts", ["user_id"], unique=False)
    op.create_index("ix_club_bans_club_id", "club_bans", ["club_id"], unique=False)
    op.create_index("ix_club_bans_user_id", "club_bans", ["user_id"], unique=False)
    op.create_index("ix_randomizer_sessions_club_id", "randomizer_sessions", ["club_id"], unique=False)
    op.create_index("ix_randomizer_sessions_created_by", "randomizer_sessions", ["created_by"], unique=False)
    op.create_index("ix_chat_rooms_club_id", "chat_rooms", ["club_id"], unique=False)


def downgrade() -> None:
    """Drop FK indexes."""
    op.drop_index("ix_chat_rooms_club_id", table_name="chat_rooms")
    op.drop_index("ix_randomizer_sessions_created_by", table_name="randomizer_sessions")
    op.drop_index("ix_randomizer_sessions_club_id", table_name="randomizer_sessions")
    op.drop_index("ix_club_bans_user_id", table_name="club_bans")
    op.drop_index("ix_club_bans_club_id", table_name="club_bans")
    op.drop_index("ix_quiz_attempts_user_id", table_name="quiz_attempts")
    op.drop_index("ix_quiz_attempts_quiz_id", table_name="quiz_attempts")
    op.drop_index("ix_quiz_questions_quiz_id", table_name="quiz_questions")
    op.drop_index("ix_quizzes_created_by", table_name="quizzes")
    op.drop_index("ix_quizzes_club_id", table_name="quizzes")
    op.drop_index("ix_meeting_attendees_user_id", table_name="meeting_attendees")
    op.drop_index("ix_meeting_attendees_meeting_id", table_name="meeting_attendees")
    op.drop_index("ix_meetings_club_id", table_name="meetings")
    op.drop_index("ix_club_members_user_id", table_name="club_members")
    op.drop_index("ix_club_members_club_id", table_name="club_members")
    op.drop_index("ix_clubs_organizer_id", table_name="clubs")
