"""chat_room_dedup_cleanup

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-05-25 00:00:00.000000

1. Merges duplicate rooms (same club_id + name): keeps the oldest row,
   reassigns all messages to it, then deletes the duplicates.
2. Deletes empty rooms (no messages) that are older than 30 days.
3. Adds a UNIQUE constraint on (club_id, name) for chat_rooms.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "n4o5p6q7r8s9"
down_revision: str | Sequence[str] | None = "m3n4o5p6q7r8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Merge duplicates ───────────────────────────────────────────────────
    # Find every (club_id, name) group that has more than one room.
    # For each group keep the oldest room (MIN id as tie-breaker on created_at).
    dup_groups = conn.execute(
        sa.text(
            """
            SELECT club_id, name, MIN(created_at) AS oldest_ts
            FROM chat_rooms
            GROUP BY club_id, name
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()

    for club_id, name, _oldest_ts in dup_groups:
        # Identify the canonical (oldest) room to keep.
        keeper_row = conn.execute(
            sa.text(
                """
                SELECT id FROM chat_rooms
                WHERE club_id = :club_id AND name = :name
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """
            ),
            {"club_id": club_id, "name": name},
        ).fetchone()
        keeper_id = keeper_row[0]

        # Reassign all messages from the duplicate rooms to the keeper.
        conn.execute(
            sa.text(
                """
                UPDATE chat_messages
                SET room_id = :keeper_id
                WHERE room_id IN (
                    SELECT id FROM chat_rooms
                    WHERE club_id = :club_id AND name = :name AND id != :keeper_id
                )
                """
            ),
            {"keeper_id": keeper_id, "club_id": club_id, "name": name},
        )

        # Delete the duplicate rooms.
        conn.execute(
            sa.text(
                """
                DELETE FROM chat_rooms
                WHERE club_id = :club_id AND name = :name AND id != :keeper_id
                """
            ),
            {"keeper_id": keeper_id, "club_id": club_id, "name": name},
        )

    # ── 2. Delete old empty rooms ─────────────────────────────────────────────
    conn.execute(
        sa.text(
            """
            DELETE FROM chat_rooms
            WHERE id NOT IN (SELECT DISTINCT room_id FROM chat_messages)
              AND created_at < NOW() - INTERVAL '30 days'
            """
        )
    )

    # ── 3. Add unique constraint ──────────────────────────────────────────────
    op.create_unique_constraint("uq_chat_rooms_club_name", "chat_rooms", ["club_id", "name"])


def downgrade() -> None:
    op.drop_constraint("uq_chat_rooms_club_name", "chat_rooms", type_="unique")
