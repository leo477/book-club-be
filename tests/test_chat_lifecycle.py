import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import SYSTEM_USER_ID
from app.models.chat import ChatMessage, ChatRoom
from app.models.user import User
from app.tasks.cleanup import run_event_chat_lifecycle_pass


async def _ensure_system_user(test_engine) -> None:
    TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with TestSessionLocal() as session:
        existing = await session.get(User, SYSTEM_USER_ID)
        if existing is None:
            session.add(
                User(
                    id=SYSTEM_USER_ID,
                    email="system@bookclub.internal",
                    display_name="Book Club Bot",
                    password_hash=None,
                    supabase_user_id=None,
                    role="user",
                )
            )
            await session.commit()


async def _create_event(async_client, register_user, auth_headers, email, *, days_from_now: int):
    await register_user(email=email)
    headers = await auth_headers(email=email)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "Test Club", "description": "D", "city": "Kyiv"}
    )
    club_id = club.json()["id"]
    event_date = (datetime.now(UTC) + timedelta(days=days_from_now)).replace(microsecond=0).isoformat()
    event = await async_client.post(
        f"/api/v1/clubs/{club_id}/events",
        headers=headers,
        json={"title": "Test Event", "date": event_date, "city": "Kyiv"},
    )
    return headers, club_id, event.json()["id"]


@pytest.mark.asyncio
async def test_event_creation_auto_creates_chat_room(async_client, register_user, auth_headers):
    """Event chat room exists immediately after event creation, no manual step needed."""
    headers, club_id, event_id = await _create_event(
        async_client, register_user, auth_headers, "lifecycle_create@example.com", days_from_now=30
    )
    resp = await async_client.get(f"/api/v1/events/{event_id}/chat/room", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["eventId"] == event_id


@pytest.mark.asyncio
async def test_lifecycle_pass_posts_countdown_for_recently_ended_event(
    async_client, register_user, auth_headers, test_engine, db_session
):
    await _ensure_system_user(test_engine)
    headers, club_id, event_id = await _create_event(
        async_client, register_user, auth_headers, "lifecycle_countdown@example.com", days_from_now=-2
    )

    await run_event_chat_lifecycle_pass(db_session)

    room = (await db_session.execute(select(ChatRoom).where(ChatRoom.event_id == uuid.UUID(event_id)))).scalar_one()
    messages = (await db_session.execute(select(ChatMessage).where(ChatMessage.room_id == room.id))).scalars().all()
    bot_messages = [m for m in messages if m.sender_id == SYSTEM_USER_ID]
    assert len(bot_messages) == 1
    # 5 - 2 days elapsed = ~3 days left; floor() from timedelta.days can read 2 or 3
    # depending on the exact microsecond gap between event creation and this assertion.
    assert any(str(n) in bot_messages[0].text for n in (2, 3))


@pytest.mark.asyncio
async def test_lifecycle_pass_does_not_duplicate_countdown_same_day(
    async_client, register_user, auth_headers, test_engine, db_session
):
    await _ensure_system_user(test_engine)
    headers, club_id, event_id = await _create_event(
        async_client, register_user, auth_headers, "lifecycle_nodup@example.com", days_from_now=-1
    )

    await run_event_chat_lifecycle_pass(db_session)
    await run_event_chat_lifecycle_pass(db_session)

    room = (await db_session.execute(select(ChatRoom).where(ChatRoom.event_id == uuid.UUID(event_id)))).scalar_one()
    messages = (await db_session.execute(select(ChatMessage).where(ChatMessage.room_id == room.id))).scalars().all()
    bot_messages = [m for m in messages if m.sender_id == SYSTEM_USER_ID]
    assert len(bot_messages) == 1


@pytest.mark.asyncio
async def test_lifecycle_pass_deletes_room_past_grace_period(
    async_client, register_user, auth_headers, test_engine, db_session
):
    await _ensure_system_user(test_engine)
    headers, club_id, event_id = await _create_event(
        async_client, register_user, auth_headers, "lifecycle_delete@example.com", days_from_now=-6
    )

    await run_event_chat_lifecycle_pass(db_session)

    room = await db_session.scalar(select(ChatRoom).where(ChatRoom.event_id == uuid.UUID(event_id)))
    assert room is None


@pytest.mark.asyncio
async def test_lifecycle_pass_ignores_future_and_unrelated_rooms(
    async_client, register_user, auth_headers, test_engine, db_session
):
    """A club's General room (event_id is NULL) and a not-yet-happened event's room
    must be left untouched by the event-chat lifecycle pass."""
    await _ensure_system_user(test_engine)
    headers, club_id, event_id = await _create_event(
        async_client, register_user, auth_headers, "lifecycle_future@example.com", days_from_now=30
    )

    await run_event_chat_lifecycle_pass(db_session)

    rooms = (await db_session.execute(select(ChatRoom).where(ChatRoom.club_id == uuid.UUID(club_id)))).scalars().all()
    assert len(rooms) == 2  # General + event room, both untouched
    for room in rooms:
        messages = (await db_session.execute(select(ChatMessage).where(ChatMessage.room_id == room.id))).scalars().all()
        assert messages == []


@pytest.mark.asyncio
async def test_system_user_cannot_login(async_client, test_engine):
    await _ensure_system_user(test_engine)
    resp = await async_client.post(
        "/api/v1/auth/login", json={"email": "system@bookclub.internal", "password": "anything"}
    )
    assert resp.status_code in (401, 400)
