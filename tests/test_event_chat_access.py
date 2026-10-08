import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event import EventAttendee


async def _setup(async_client, register_user, auth_headers, make_member, tag, is_public):
    await register_user(email=f"org_{tag}@example.com")
    org = await auth_headers(email=f"org_{tag}@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org, json={"role": "organizer"})
    club = await async_client.post(
        "/api/v1/clubs",
        headers=org,
        json={"name": f"Club {tag}", "description": "D", "city": "Kyiv", "isPublic": is_public},
    )
    club_id = club.json()["id"]
    event = await async_client.post(
        f"/api/v1/clubs/{club_id}/events",
        headers=org,
        json={"title": "Event", "date": "2030-01-01T18:00:00", "city": "Kyiv"},
    )
    event_id = event.json()["id"]

    await register_user(email=f"mem_{tag}@example.com")
    mem = await auth_headers(email=f"mem_{tag}@example.com")
    user_id = await make_member(club_id, mem)
    attend = await async_client.post(f"/api/v1/events/{event_id}/attend", headers=mem)
    assert attend.status_code == 201
    return org, mem, club_id, event_id, user_id


async def _attendee_count(async_client, org, event_id):
    resp = await async_client.get(f"/api/v1/events/{event_id}", headers=org)
    return resp.json()["attendeeCount"]


async def _room_id(async_client, org, event_id):
    resp = await async_client.get(f"/api/v1/events/{event_id}/chat/room", headers=org)
    assert resp.status_code == 200
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_member_attendee_gets_room(async_client, register_user, auth_headers, make_member):
    org, mem, _c, event_id, _u = await _setup(async_client, register_user, auth_headers, make_member, "ok", False)
    resp = await async_client.get(f"/api/v1/events/{event_id}/chat/room", headers=mem)
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("is_public", [False, True])
async def test_removed_member_loses_room_and_attendance(
    async_client, register_user, auth_headers, make_member, is_public
):
    org, mem, club_id, event_id, user_id = await _setup(
        async_client, register_user, auth_headers, make_member, f"rm{is_public}", is_public
    )
    before = await _attendee_count(async_client, org, event_id)
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/members/{user_id}", headers=org)
    assert resp.status_code == 204

    assert await _attendee_count(async_client, org, event_id) == before - 1
    resp = await async_client.get(f"/api/v1/events/{event_id}/chat/room", headers=mem)
    assert resp.status_code == (403 if is_public else 404)


@pytest.mark.asyncio
@pytest.mark.parametrize("is_public", [False, True])
async def test_left_member_loses_room_and_attendance(async_client, register_user, auth_headers, make_member, is_public):
    org, mem, club_id, event_id, _u = await _setup(
        async_client, register_user, auth_headers, make_member, f"lf{is_public}", is_public
    )
    before = await _attendee_count(async_client, org, event_id)
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/leave", headers=mem)
    assert resp.status_code == 204

    assert await _attendee_count(async_client, org, event_id) == before - 1
    resp = await async_client.get(f"/api/v1/events/{event_id}/chat/room", headers=mem)
    assert resp.status_code == (403 if is_public else 404)


@pytest.mark.asyncio
@pytest.mark.parametrize("is_public", [False, True])
async def test_banned_member_loses_room_and_attendance(
    async_client, register_user, auth_headers, make_member, is_public
):
    org, mem, club_id, event_id, user_id = await _setup(
        async_client, register_user, auth_headers, make_member, f"bn{is_public}", is_public
    )
    before = await _attendee_count(async_client, org, event_id)
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/members/{user_id}/ban", headers=org, json={"duration": 1})
    assert resp.status_code == 201

    assert await _attendee_count(async_client, org, event_id) == before - 1
    resp = await async_client.get(f"/api/v1/events/{event_id}/chat/room", headers=mem)
    assert resp.status_code == (403 if is_public else 404)


@pytest.mark.asyncio
async def test_banned_user_with_stale_attendee_row_denied_on_public_club(
    async_client, register_user, auth_headers, make_member, test_engine
):
    org, mem, club_id, event_id, user_id = await _setup(
        async_client, register_user, auth_headers, make_member, "stale", True
    )
    await async_client.post(f"/api/v1/clubs/{club_id}/members/{user_id}/ban", headers=org, json={"duration": 1})
    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        session.add(EventAttendee(event_id=uuid.UUID(event_id), user_id=uuid.UUID(user_id)))
        await session.commit()

    resp = await async_client.get(f"/api/v1/events/{event_id}/chat/room", headers=mem)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_non_member_cannot_read_or_post_messages(async_client, register_user, auth_headers, make_member):
    org, mem, _c, event_id, _u = await _setup(async_client, register_user, auth_headers, make_member, "msg", False)
    room_id = await _room_id(async_client, org, event_id)

    await register_user(email="outsider_msg@example.com")
    outsider = await auth_headers(email="outsider_msg@example.com")
    assert (await async_client.get(f"/api/v1/chat/rooms/{room_id}/messages", headers=outsider)).status_code == 403
    resp = await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=outsider, json={"text": "hi"})
    assert resp.status_code == 403

    assert (await async_client.get(f"/api/v1/chat/rooms/{room_id}/messages", headers=mem)).status_code == 200
    resp = await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=mem, json={"text": "hi"})
    assert resp.status_code == 201
    assert (await async_client.get(f"/api/v1/chat/rooms/{room_id}/messages", headers=org)).status_code == 200


@pytest.mark.asyncio
async def test_removed_member_cannot_use_message_endpoints(async_client, register_user, auth_headers, make_member):
    org, mem, club_id, event_id, user_id = await _setup(
        async_client, register_user, auth_headers, make_member, "msgrm", False
    )
    room_id = await _room_id(async_client, org, event_id)
    await async_client.delete(f"/api/v1/clubs/{club_id}/members/{user_id}", headers=org)

    assert (await async_client.get(f"/api/v1/chat/rooms/{room_id}/messages", headers=mem)).status_code == 403
    resp = await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=mem, json={"text": "hi"})
    assert resp.status_code == 403
