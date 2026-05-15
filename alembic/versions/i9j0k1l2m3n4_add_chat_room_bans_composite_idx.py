"""add_chat_room_bans_composite_idx

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-05-15 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "i9j0k1l2m3n4"
down_revision: str | Sequence[str] | None = "h8i9j0k1l2m3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_chat_room_bans_room_id", table_name="chat_room_bans", if_exists=True)
    op.create_index(
        "ix_chat_room_bans_room_user",
        "chat_room_bans",
        ["room_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_room_bans_room_user", table_name="chat_room_bans")
    op.create_index("ix_chat_room_bans_room_id", "chat_room_bans", ["room_id"])
