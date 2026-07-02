"""backfill missing General chat room for clubs created before auto-creation existed

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-07-02 00:00:01.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "v2w3x4y5z6a7"
down_revision: str | None = "u1v2w3x4y5z6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO chat_rooms (id, club_id, name, event_id, created_at)
        SELECT gen_random_uuid(), c.id, left(c.name || ' · General', 100), NULL, now()
        FROM clubs c
        WHERE NOT EXISTS (
            SELECT 1 FROM chat_rooms r WHERE r.club_id = c.id AND r.event_id IS NULL
        )
        """
    )


def downgrade() -> None:
    # Not reversible: cannot distinguish backfilled rooms from ones created normally.
    pass
