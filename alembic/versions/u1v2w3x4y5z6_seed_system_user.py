"""seed system/bot user for automated chat messages

Revision ID: u1v2w3x4y5z6
Revises: t0u1v2w3x4y5
Create Date: 2026-07-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u1v2w3x4y5z6"
down_revision: str | None = "t0u1v2w3x4y5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep in sync with app.config.SYSTEM_USER_ID (uuid5(NAMESPACE_DNS, "bot.bookclub.internal")).
_SYSTEM_USER_ID = "692963e2-19bc-5167-b0fd-333a528f39eb"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO users (id, email, display_name, password_hash, supabase_user_id, role,
                                socials_public, created_at)
            VALUES (:id, 'system@bookclub.internal', 'Book Club Bot', NULL, NULL,
                    'user', false, now())
            ON CONFLICT (id) DO NOTHING
            """
        ).bindparams(id=_SYSTEM_USER_ID)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM users WHERE id = :id").bindparams(id=_SYSTEM_USER_ID))
