"""add unique constraints on club_bans and quiz_attempts

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-05-11 00:00:00.000000

MN-13/MN-15: UniqueConstraint(club_id, user_id) on club_bans
MN-14: UniqueConstraint(quiz_id, user_id) on quiz_attempts
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g7h8i9j0k1l2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # MN-13 & MN-15: prevent duplicate ban rows for (club, user)
    op.create_unique_constraint("uq_club_bans_club_user", "club_bans", ["club_id", "user_id"])

    # MN-14: each user may only have one attempt per quiz
    op.create_unique_constraint("uq_quiz_attempts_quiz_user", "quiz_attempts", ["quiz_id", "user_id"])


def downgrade() -> None:
    op.drop_constraint("uq_quiz_attempts_quiz_user", "quiz_attempts", type_="unique")
    op.drop_constraint("uq_club_bans_club_user", "club_bans", type_="unique")
