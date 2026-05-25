"""Unit tests for the chat-room dedup + cleanup migration logic (n4o5p6q7r8s9).

The migration does:
  1. For each (club_id, name) group with more than one room, keep the oldest
     room (by created_at ASC, id ASC), reassign all messages to it, delete
     the duplicates.
  2. Delete rooms that have zero messages AND were created more than 30 days ago.

These tests run against an isolated in-memory SQLite DB whose schema is built
WITHOUT the uq_chat_rooms_club_name unique constraint — exactly the pre-migration
state that the Alembic upgrade targets.  That lets the tests freely insert
duplicate-name rows to exercise the dedup SQL, then verify the cleanup SQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func, text

# ── Pre-migration schema (no unique constraint on chat_rooms) ─────────────────
#
# We define a minimal local Base / ORM separate from the main app's Base so that
# creating the test engine does NOT inherit the uq_chat_rooms_club_name constraint
# that was retroactively added to app.models.chat.ChatRoom.  This mirrors the
# real-world pre-migration state of the database.

SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "JSON"  # type: ignore[method-assign]
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "JSON"  # type: ignore[method-assign]


class _Base(DeclarativeBase):
    pass


class _User(_Base):
    __tablename__ = "mig_users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class _Club(_Base):
    __tablename__ = "mig_clubs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    organizer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mig_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class _ChatRoom(_Base):
    """No UniqueConstraint — represents the pre-migration schema."""

    __tablename__ = "mig_chat_rooms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mig_clubs.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class _ChatMessage(_Base):
    __tablename__ = "mig_chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mig_chat_rooms.id"), nullable=False
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mig_users.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── helpers ──────────────────────────────────────────────────────────────────


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


async def _make_user(session: AsyncSession) -> uuid.UUID:
    user_id = _uuid()
    session.add(_User(id=user_id, email=f"{user_id}@test.com", display_name="Test"))
    await session.flush()
    return user_id


async def _make_club(session: AsyncSession, organizer_id: uuid.UUID) -> uuid.UUID:
    club_id = _uuid()
    session.add(_Club(id=club_id, name=f"Club-{club_id}", organizer_id=organizer_id))
    await session.flush()
    return club_id


async def _make_room(
    session: AsyncSession,
    club_id: uuid.UUID,
    name: str,
    *,
    created_at: datetime | None = None,
) -> uuid.UUID:
    room_id = _uuid()
    room = _ChatRoom(id=room_id, club_id=club_id, name=name)
    if created_at is not None:
        room.created_at = created_at
    session.add(room)
    await session.flush()
    return room_id


async def _make_message(
    session: AsyncSession, room_id: uuid.UUID, sender_id: uuid.UUID
) -> uuid.UUID:
    msg_id = _uuid()
    session.add(_ChatMessage(id=msg_id, room_id=room_id, sender_id=sender_id, text="hi"))
    await session.flush()
    return msg_id


# ── migration logic replicated for local table names ─────────────────────────


async def _run_dedup(session: AsyncSession) -> None:
    """Merge duplicate (club_id, name) rows, keeping the oldest room."""
    dup_groups = (
        await session.execute(
            text(
                """
                SELECT club_id, name, MIN(created_at) AS oldest_ts
                FROM mig_chat_rooms
                GROUP BY club_id, name
                HAVING COUNT(*) > 1
                """
            )
        )
    ).fetchall()

    for club_id, name, _oldest_ts in dup_groups:
        keeper_row = (
            await session.execute(
                text(
                    """
                    SELECT id FROM mig_chat_rooms
                    WHERE club_id = :club_id AND name = :name
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                    """
                ),
                {"club_id": str(club_id), "name": name},
            )
        ).fetchone()
        keeper_id = keeper_row[0]

        await session.execute(
            text(
                """
                UPDATE mig_chat_messages
                SET room_id = :keeper_id
                WHERE room_id IN (
                    SELECT id FROM mig_chat_rooms
                    WHERE club_id = :club_id AND name = :name AND id != :keeper_id
                )
                """
            ),
            {"keeper_id": keeper_id, "club_id": str(club_id), "name": name},
        )

        await session.execute(
            text(
                """
                DELETE FROM mig_chat_rooms
                WHERE club_id = :club_id AND name = :name AND id != :keeper_id
                """
            ),
            {"keeper_id": keeper_id, "club_id": str(club_id), "name": name},
        )

    await session.flush()


async def _run_cleanup(session: AsyncSession, cutoff: datetime) -> None:
    """Delete empty rooms older than `cutoff`."""
    await session.execute(
        text(
            """
            DELETE FROM mig_chat_rooms
            WHERE id NOT IN (SELECT DISTINCT room_id FROM mig_chat_messages)
              AND created_at < :cutoff
            """
        ),
        {"cutoff": cutoff.isoformat()},
    )
    await session.flush()


# ── session fixture ───────────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="module")
async def mig_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def mig_session(mig_engine):
    maker = async_sessionmaker(
        bind=mig_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    async with maker() as s:
        yield s
        await s.rollback()
        # Wipe all tables between tests
        async with mig_engine.begin() as conn:
            for table in reversed(_Base.metadata.sorted_tables):
                await conn.execute(table.delete())


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dedup_keeps_oldest_room(mig_session: AsyncSession) -> None:
    """After dedup, only the oldest room (by created_at) survives."""
    organizer_id = await _make_user(mig_session)
    club_id = await _make_club(mig_session, organizer_id)

    now = datetime.now(UTC)
    old_id = await _make_room(mig_session, club_id, "igor", created_at=now - timedelta(days=5))
    await _make_room(mig_session, club_id, "igor", created_at=now - timedelta(days=3))
    await _make_room(mig_session, club_id, "igor", created_at=now - timedelta(days=1))
    await mig_session.commit()

    await _run_dedup(mig_session)
    await mig_session.commit()

    rows = (
        await mig_session.execute(text("SELECT id FROM mig_chat_rooms WHERE name='igor'"))
    ).fetchall()
    assert len(rows) == 1
    assert str(rows[0][0]).replace("-", "") == str(old_id).replace("-", "")


@pytest.mark.asyncio
async def test_dedup_reassigns_messages_to_keeper(mig_session: AsyncSession) -> None:
    """Messages from deleted duplicate rooms are reassigned to the keeper."""
    organizer_id = await _make_user(mig_session)
    club_id = await _make_club(mig_session, organizer_id)
    sender_id = await _make_user(mig_session)

    now = datetime.now(UTC)
    keeper_id = await _make_room(mig_session, club_id, "igor", created_at=now - timedelta(days=5))
    dup_id = await _make_room(mig_session, club_id, "igor", created_at=now - timedelta(days=2))

    await _make_message(mig_session, dup_id, sender_id)
    await _make_message(mig_session, dup_id, sender_id)
    await mig_session.commit()

    await _run_dedup(mig_session)
    await mig_session.commit()

    msg_rows = (
        await mig_session.execute(text("SELECT room_id FROM mig_chat_messages"))
    ).fetchall()
    assert len(msg_rows) == 2
    for row in msg_rows:
        assert str(row[0]).replace("-", "") == str(keeper_id).replace("-", ""), "All messages must be reassigned to the keeper room"


@pytest.mark.asyncio
async def test_dedup_leaves_unique_rooms_untouched(mig_session: AsyncSession) -> None:
    """Rooms with unique names in the club are untouched by the dedup step."""
    organizer_id = await _make_user(mig_session)
    club_id = await _make_club(mig_session, organizer_id)

    await _make_room(mig_session, club_id, "general")
    await _make_room(mig_session, club_id, "news")
    await mig_session.commit()

    await _run_dedup(mig_session)
    await mig_session.commit()

    rows = (
        await mig_session.execute(text("SELECT name FROM mig_chat_rooms ORDER BY name"))
    ).fetchall()
    names = [r[0] for r in rows]
    assert names == ["general", "news"]


@pytest.mark.asyncio
async def test_cleanup_deletes_old_empty_rooms(mig_session: AsyncSession) -> None:
    """Empty rooms older than the cutoff are deleted."""
    organizer_id = await _make_user(mig_session)
    club_id = await _make_club(mig_session, organizer_id)

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=30)

    old_empty_id = await _make_room(mig_session, club_id, "junk", created_at=now - timedelta(days=40))
    await _make_room(mig_session, club_id, "fresh", created_at=now - timedelta(days=5))
    await mig_session.commit()

    await _run_cleanup(mig_session, cutoff)
    await mig_session.commit()

    remaining = (await mig_session.execute(text("SELECT id FROM mig_chat_rooms"))).fetchall()
    remaining_ids = [str(r[0]) for r in remaining]
    assert str(old_empty_id) not in remaining_ids, "Old empty room must be deleted"
    assert len(remaining_ids) == 1, "Fresh empty room must survive"


@pytest.mark.asyncio
async def test_cleanup_preserves_old_room_with_messages(mig_session: AsyncSession) -> None:
    """An old room that has messages is NOT deleted."""
    organizer_id = await _make_user(mig_session)
    club_id = await _make_club(mig_session, organizer_id)
    sender_id = await _make_user(mig_session)

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=30)

    old_room_id = await _make_room(
        mig_session, club_id, "history", created_at=now - timedelta(days=60)
    )
    await _make_message(mig_session, old_room_id, sender_id)
    await mig_session.commit()

    await _run_cleanup(mig_session, cutoff)
    await mig_session.commit()

    rows = (await mig_session.execute(text("SELECT id FROM mig_chat_rooms"))).fetchall()
    assert len(rows) == 1, "Room with messages must not be deleted"


@pytest.mark.asyncio
async def test_cleanup_preserves_recent_empty_rooms(mig_session: AsyncSession) -> None:
    """Empty rooms created within the last 30 days are preserved."""
    organizer_id = await _make_user(mig_session)
    club_id = await _make_club(mig_session, organizer_id)

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=30)

    await _make_room(mig_session, club_id, "dd", created_at=now - timedelta(days=1))
    await _make_room(mig_session, club_id, "fd", created_at=now - timedelta(days=2))
    await _make_room(mig_session, club_id, "ikzgf", created_at=now - timedelta(days=10))
    await mig_session.commit()

    await _run_cleanup(mig_session, cutoff)
    await mig_session.commit()

    rows = (await mig_session.execute(text("SELECT name FROM mig_chat_rooms ORDER BY name"))).fetchall()
    assert len(rows) == 3
