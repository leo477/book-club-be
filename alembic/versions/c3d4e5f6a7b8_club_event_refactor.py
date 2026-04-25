"""club_event_refactor

Rename clubs scheduling columns to a new events table.
The old Meeting/MeetingAttendee tables are dropped and replaced by Event/EventAttendee.

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-04-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create event_status enum
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE event_status_enum AS ENUM (
                'scheduled', 'active', 'held', 'cancelled', 'rescheduled'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )

    # 2. Create events table
    op.create_table(
        "events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("club_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "scheduled",
                "active",
                "held",
                "cancelled",
                "rescheduled",
                name="event_status_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="scheduled",
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("theme", sa.String(length=100), nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("after_meeting_venue", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_club_id", "events", ["club_id"], unique=False)
    op.create_index("ix_events_date", "events", ["date"], unique=False)

    # 3. Create event_attendees table
    op.create_table(
        "event_attendees",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("event_id", "user_id"),
    )
    op.create_index("ix_event_attendees_event_id", "event_attendees", ["event_id"], unique=False)
    op.create_index("ix_event_attendees_user_id", "event_attendees", ["user_id"], unique=False)

    # 4. Migrate existing scheduled club data into events (data preservation)
    op.execute(
        """
        INSERT INTO events (id, club_id, title, date, city, address, lat, lng,
                            theme, tags, duration_minutes, after_meeting_venue,
                            status, created_at)
        SELECT gen_random_uuid(), id, name, next_meeting_date, city,
               COALESCE(address, ''), lat, lng, theme,
               COALESCE(tags, '{}'), meeting_duration_minutes,
               after_meeting_venue, 'scheduled', created_at
        FROM clubs
        WHERE next_meeting_date IS NOT NULL
        """
    )

    # 5. Drop meeting_attendees and meetings (superseded by event_attendees/events)
    op.drop_index("ix_meeting_attendees_user_id", table_name="meeting_attendees")
    op.drop_index("ix_meeting_attendees_meeting_id", table_name="meeting_attendees")
    op.drop_table("meeting_attendees")
    op.drop_index("ix_meetings_club_id", table_name="meetings")
    op.drop_table("meetings")
    op.execute("DROP TYPE IF EXISTS meeting_status_enum")

    # 6. Strip scheduling columns from clubs
    op.drop_column("clubs", "city")
    op.drop_column("clubs", "theme")
    op.drop_column("clubs", "next_meeting_date")
    op.drop_column("clubs", "address")
    op.drop_column("clubs", "lat")
    op.drop_column("clubs", "lng")
    op.drop_column("clubs", "status")
    op.drop_column("clubs", "cancelled_at")
    op.drop_column("clubs", "tags")
    op.drop_column("clubs", "meeting_duration_minutes")
    op.drop_column("clubs", "after_meeting_venue")
    op.execute("DROP TYPE IF EXISTS club_status_enum")


def downgrade() -> None:
    """Destructive downgrade — scheduling data cannot be restored. Columns are recreated as nullable."""
    # Recreate scheduling columns on clubs (data loss — values will be NULL)
    op.add_column("clubs", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column("clubs", sa.Column("theme", sa.String(length=100), nullable=True))
    op.add_column("clubs", sa.Column("next_meeting_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("clubs", sa.Column("address", sa.String(length=300), nullable=True))
    op.add_column("clubs", sa.Column("lat", sa.Float(), nullable=True))
    op.add_column("clubs", sa.Column("lng", sa.Float(), nullable=True))
    op.execute("CREATE TYPE club_status_enum AS ENUM ('active', 'paused', 'cancelled')")
    op.add_column(
        "clubs",
        sa.Column(
            "status",
            postgresql.ENUM("active", "paused", "cancelled", name="club_status_enum", create_type=False),
            nullable=True,
        ),
    )
    op.add_column("clubs", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "clubs",
        sa.Column("tags", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'"), nullable=True),
    )
    op.add_column("clubs", sa.Column("meeting_duration_minutes", sa.Integer(), nullable=True))
    op.add_column(
        "clubs",
        sa.Column("after_meeting_venue", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # Recreate meetings table
    op.execute("CREATE TYPE meeting_status_enum AS ENUM ('held', 'cancelled', 'rescheduled')")
    op.create_table(
        "meetings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("club_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM("held", "cancelled", "rescheduled", name="meeting_status_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meetings_club_id", "meetings", ["club_id"], unique=False)

    op.create_table(
        "meeting_attendees",
        sa.Column("meeting_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("meeting_id", "user_id"),
    )
    op.create_index("ix_meeting_attendees_meeting_id", "meeting_attendees", ["meeting_id"], unique=False)
    op.create_index("ix_meeting_attendees_user_id", "meeting_attendees", ["user_id"], unique=False)

    # Drop event tables
    op.drop_index("ix_event_attendees_user_id", table_name="event_attendees")
    op.drop_index("ix_event_attendees_event_id", table_name="event_attendees")
    op.drop_table("event_attendees")
    op.drop_index("ix_events_date", table_name="events")
    op.drop_index("ix_events_club_id", table_name="events")
    op.drop_table("events")
    op.execute("DROP TYPE IF EXISTS event_status_enum")
