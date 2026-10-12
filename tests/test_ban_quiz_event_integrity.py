import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.club_ban import ClubBan


async def _organizer_club_event(async_client, register_user, auth_headers, tag, is_public=True):
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
    return org, club_id, event.json()["id"]


async def _user(register_user, auth_headers, email):
    await register_user(email=email)
    return await auth_headers(email=email)


@pytest.mark.asyncio
async def test_banned_user_cannot_attend_public_club_event(async_client, register_user, auth_headers, make_member):
    org, club_id, event_id = await _organizer_club_event(async_client, register_user, auth_headers, "ban1")
    user = await _user(register_user, auth_headers, "banned1@example.com")
    user_id = await make_member(club_id, user)
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/members/{user_id}/ban", headers=org, json={"duration": 1})
    assert resp.status_code == 201

    resp = await async_client.post(f"/api/v1/events/{event_id}/attend", headers=user)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "CLUB_BANNED"


@pytest.mark.asyncio
async def test_permanently_banned_non_member_cannot_attend(async_client, register_user, auth_headers, test_engine):
    org, club_id, event_id = await _organizer_club_event(async_client, register_user, auth_headers, "ban2")
    user = await _user(register_user, auth_headers, "banned2@example.com")
    me = await async_client.get("/api/v1/users/me", headers=user)
    org_me = await async_client.get("/api/v1/users/me", headers=org)
    factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(
            ClubBan(
                id=uuid.uuid4(),
                club_id=uuid.UUID(club_id),
                user_id=uuid.UUID(me.json()["id"]),
                banned_by=uuid.UUID(org_me.json()["id"]),
                duration="permanent",
                expires_at=None,
            )
        )
        await session.commit()

    resp = await async_client.post(f"/api/v1/events/{event_id}/attend", headers=user)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_expired_ban_does_not_block_attend(async_client, register_user, auth_headers, test_engine):
    org, club_id, event_id = await _organizer_club_event(async_client, register_user, auth_headers, "ban3")
    user = await _user(register_user, auth_headers, "banned3@example.com")
    me = await async_client.get("/api/v1/users/me", headers=user)
    org_me = await async_client.get("/api/v1/users/me", headers=org)
    factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(
            ClubBan(
                id=uuid.uuid4(),
                club_id=uuid.UUID(club_id),
                user_id=uuid.UUID(me.json()["id"]),
                banned_by=uuid.UUID(org_me.json()["id"]),
                duration="1",
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await session.commit()

    resp = await async_client.post(f"/api/v1/events/{event_id}/attend", headers=user)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_unbanned_public_user_can_attend(async_client, register_user, auth_headers):
    _org, _club_id, event_id = await _organizer_club_event(async_client, register_user, auth_headers, "ban4")
    user = await _user(register_user, auth_headers, "plain4@example.com")
    resp = await async_client.post(f"/api/v1/events/{event_id}/attend", headers=user)
    assert resp.status_code == 201


async def _quiz(async_client, org, club_id):
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=org, json={"title": "Quiz"})
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_session_with_event_of_same_club(async_client, register_user, auth_headers):
    org, club_id, event_id = await _organizer_club_event(async_client, register_user, auth_headers, "qz1")
    quiz_id = await _quiz(async_client, org, club_id)
    resp = await async_client.post(f"/api/v1/quizzes/{quiz_id}/sessions", headers=org, json={"eventId": event_id})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_session_with_event_of_other_club_rejected(async_client, register_user, auth_headers):
    org, club_id, _event_id = await _organizer_club_event(async_client, register_user, auth_headers, "qz2")
    _other_org, _other_club, other_event_id = await _organizer_club_event(
        async_client, register_user, auth_headers, "qz2b", is_public=False
    )
    quiz_id = await _quiz(async_client, org, club_id)
    resp = await async_client.post(f"/api/v1/quizzes/{quiz_id}/sessions", headers=org, json={"eventId": other_event_id})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "EVENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_session_with_unknown_event_rejected(async_client, register_user, auth_headers):
    org, club_id, _event_id = await _organizer_club_event(async_client, register_user, auth_headers, "qz3")
    quiz_id = await _quiz(async_client, org, club_id)
    resp = await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/sessions", headers=org, json={"eventId": str(uuid.uuid4())}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "EVENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_session_without_event_still_allowed(async_client, register_user, auth_headers):
    org, club_id, _event_id = await _organizer_club_event(async_client, register_user, auth_headers, "qz4")
    quiz_id = await _quiz(async_client, org, club_id)
    resp = await async_client.post(f"/api/v1/quizzes/{quiz_id}/sessions", headers=org, json={})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_non_organizer_gets_403_before_event_validation(async_client, register_user, auth_headers, make_member):
    org, club_id, _event_id = await _organizer_club_event(async_client, register_user, auth_headers, "qz5")
    quiz_id = await _quiz(async_client, org, club_id)
    member = await _user(register_user, auth_headers, "qz5_member@example.com")
    await make_member(club_id, member)
    resp = await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/sessions", headers=member, json={"eventId": str(uuid.uuid4())}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_room_unknown_and_invisible_event_indistinguishable(async_client, register_user, auth_headers):
    _org, _club_id, private_event_id = await _organizer_club_event(
        async_client, register_user, auth_headers, "rm1", is_public=False
    )
    outsider = await _user(register_user, auth_headers, "rm1_outsider@example.com")

    unknown = await async_client.post(f"/api/v1/events/{uuid.uuid4()}/chat/room", headers=outsider)
    hidden = await async_client.post(f"/api/v1/events/{private_event_id}/chat/room", headers=outsider)
    assert unknown.status_code == hidden.status_code == 404
    assert unknown.json() == hidden.json()


@pytest.mark.asyncio
async def test_create_room_visible_non_organizer_still_forbidden(
    async_client, register_user, auth_headers, make_member
):
    _org, club_id, event_id = await _organizer_club_event(
        async_client, register_user, auth_headers, "rm2", is_public=False
    )
    member = await _user(register_user, auth_headers, "rm2_member@example.com")
    await make_member(club_id, member)
    resp = await async_client.post(f"/api/v1/events/{event_id}/chat/room", headers=member)
    assert resp.status_code == 403
