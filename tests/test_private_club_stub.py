import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import User

EVENT = {"title": "Book Night", "date": "2099-12-31T10:00:00+00:00", "city": "Kyiv", "description": "Read"}
STUB_KEYS = {"id", "name", "isPublic", "memberCount"}


async def _promote_admin(test_engine, async_client, headers) -> None:
    me = await async_client.get("/api/v1/users/me", headers=headers)
    Session = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        user = (await session.execute(select(User).where(User.id == uuid.UUID(me.json()["id"])))).scalar_one()
        user.role = "admin"
        await session.commit()


@pytest.fixture
def make_club(async_client, auth_headers):
    async def _make(is_public: bool):
        org = await auth_headers(email=f"org{uuid.uuid4().hex[:6]}@example.com")
        await async_client.patch("/api/v1/users/me/role", headers=org, json={"role": "organizer"})
        resp = await async_client.post(
            "/api/v1/clubs",
            headers=org,
            json={"name": "Secret Club", "description": "hidden", "city": "Kyiv", "isPublic": is_public},
        )
        club_id = resp.json()["id"]
        ev = await async_client.post(f"/api/v1/clubs/{club_id}/events", headers=org, json=EVENT)
        assert ev.status_code == 201, ev.text
        return club_id, org, ev.json()["id"]

    return _make


@pytest.mark.asyncio
async def test_private_club_anonymous_gets_stub(async_client, make_club):
    club_id, _, _ = await make_club(False)
    async_client.cookies.clear()
    resp = await async_client.get(f"/api/v1/clubs/{club_id}")
    assert resp.status_code == 200
    assert resp.json() == {"id": club_id, "name": "Secret Club", "isPublic": False, "memberCount": 1}
    events = await async_client.get(f"/api/v1/clubs/{club_id}/events")
    assert events.status_code == 200
    assert events.json() == []


@pytest.mark.asyncio
async def test_private_club_non_member_gets_stub(async_client, auth_headers, make_club):
    club_id, _, _ = await make_club(False)
    other = await auth_headers(email="outsider@example.com")
    resp = await async_client.get(f"/api/v1/clubs/{club_id}", headers=other)
    assert set(resp.json()) == STUB_KEYS
    events = await async_client.get(f"/api/v1/clubs/{club_id}/events", headers=other)
    assert events.json() == []


@pytest.mark.asyncio
async def test_private_club_organizer_sees_full(async_client, make_club):
    club_id, org, _ = await make_club(False)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}", headers=org)
    body = resp.json()
    assert body["description"] == "hidden"
    assert "organizerId" in body
    events = await async_client.get(f"/api/v1/clubs/{club_id}/events", headers=org)
    assert len(events.json()) == 1


@pytest.mark.asyncio
async def test_private_club_member_sees_full(async_client, auth_headers, make_member, make_club):
    club_id, _, _ = await make_club(False)
    member = await auth_headers(email="member@example.com")
    await make_member(club_id, member)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}", headers=member)
    assert resp.json()["description"] == "hidden"
    events = await async_client.get(f"/api/v1/clubs/{club_id}/events", headers=member)
    assert len(events.json()) == 1


@pytest.mark.asyncio
async def test_private_club_admin_sees_full(async_client, auth_headers, test_engine, make_club):
    club_id, _, _ = await make_club(False)
    admin = await auth_headers(email="admin@example.com")
    await _promote_admin(test_engine, async_client, admin)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}", headers=admin)
    assert resp.json()["description"] == "hidden"
    events = await async_client.get(f"/api/v1/clubs/{club_id}/events", headers=admin)
    assert len(events.json()) == 1


@pytest.mark.asyncio
async def test_public_club_unchanged_for_anonymous(async_client, make_club):
    club_id, _, _ = await make_club(True)
    async_client.cookies.clear()
    resp = await async_client.get(f"/api/v1/clubs/{club_id}")
    assert resp.json()["description"] == "hidden"
    assert resp.json()["isPublic"] is True
    events = await async_client.get(f"/api/v1/clubs/{club_id}/events")
    assert len(events.json()) == 1


@pytest.mark.asyncio
async def test_private_club_join_flow_still_works(async_client, auth_headers, make_club):
    club_id, _, _ = await make_club(False)
    other = await auth_headers(email="joiner@example.com")
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=other)
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    mine = await async_client.get(f"/api/v1/clubs/{club_id}/my-membership", headers=other)
    assert mine.json()["joinRequestStatus"] == "pending"


@pytest.mark.asyncio
async def test_private_club_members_list_forbidden_for_non_member(async_client, auth_headers, make_club):
    club_id, org, _ = await make_club(False)
    other = await auth_headers(email="peeker@example.com")
    assert (await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=other)).status_code == 403
    assert (await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=org)).status_code == 200


@pytest.mark.asyncio
async def test_public_club_members_list_open_to_any_user(async_client, auth_headers, make_club):
    club_id, _, _ = await make_club(True)
    other = await auth_headers(email="reader@example.com")
    assert (await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=other)).status_code == 200


@pytest.mark.asyncio
async def test_private_club_event_hidden_from_global_endpoints(async_client, auth_headers, make_club):
    club_id, org, event_id = await make_club(False)
    async_client.cookies.clear()
    assert (await async_client.get("/api/v1/events")).json() == []
    assert (await async_client.get(f"/api/v1/events/{event_id}")).status_code == 404
    other = await auth_headers(email="stranger@example.com")
    assert (await async_client.get("/api/v1/events", headers=other)).json() == []
    assert (await async_client.get(f"/api/v1/events/{event_id}", headers=other)).status_code == 404
    assert len((await async_client.get("/api/v1/events", headers=org)).json()) == 1
    assert (await async_client.get(f"/api/v1/events/{event_id}", headers=org)).status_code == 200


@pytest.mark.asyncio
async def test_private_club_event_attend_and_cancel_denied_for_non_member(async_client, auth_headers, make_club):
    _, _, event_id = await make_club(False)
    stranger = await auth_headers(email="stranger2@example.com")
    resp = await async_client.post(f"/api/v1/events/{event_id}/attend", headers=stranger)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "EVENT_NOT_FOUND"
    resp = await async_client.delete(f"/api/v1/events/{event_id}/attend", headers=stranger)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "EVENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_private_club_event_attend_and_cancel_allowed_for_member(
    async_client, auth_headers, make_member, make_club
):
    club_id, _, event_id = await make_club(False)
    member = await auth_headers(email="member2@example.com")
    await make_member(club_id, member)
    resp = await async_client.post(f"/api/v1/events/{event_id}/attend", headers=member)
    assert resp.status_code == 201
    assert resp.json()["joinRequestStatus"] == "member"
    assert (await async_client.delete(f"/api/v1/events/{event_id}/attend", headers=member)).status_code == 204


@pytest.mark.asyncio
async def test_public_club_event_attend_and_cancel_allowed_for_non_member(async_client, auth_headers, make_club):
    _, _, event_id = await make_club(True)
    other = await auth_headers(email="other2@example.com")
    assert (await async_client.post(f"/api/v1/events/{event_id}/attend", headers=other)).status_code == 201
    assert (await async_client.delete(f"/api/v1/events/{event_id}/attend", headers=other)).status_code == 204
