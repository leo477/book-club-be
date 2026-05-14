"""add_event_id_to_chat_rooms

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-05-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h8i9j0k1l2m3"
down_revision: str | Sequence[str] | None = "g7h8i9j0k1l2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_rooms",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_chat_rooms_event_id",
        "chat_rooms",
        ["event_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_chat_rooms_event_id_events",
        "chat_rooms",
        "events",
        ["event_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_chat_rooms_event_id_events", "chat_rooms", type_="foreignkey")
    op.drop_index("ix_chat_rooms_event_id", table_name="chat_rooms")
    op.drop_column("chat_rooms", "event_id")
