import uuid

import pytest
from sqlalchemy import select

from app.models.user import User


@pytest.mark.asyncio
async def test_list_clubs_empty(async_client):
    resp = await async_client.get("/api/v1/clubs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_clubs_authenticated_sees_private(async_client, register_user, auth_headers):
    await register_user(email="lc_org@example.com")
    org_headers = await auth_headers(email="lc_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org_headers, json={"role": "organizer"})
    priv_resp = await async_client.post(
        "/api/v1/clubs",
        headers=org_headers,
        json={"name": "Private Club LC", "description": "Desc", "city": "Kyiv", "isPublic": False},
    )
    club_id = priv_resp.json()["id"]

    # Unauthenticated — should NOT see private club
    anon_resp = await async_client.get("/api/v1/clubs")
    anon_ids = [c["id"] for c in anon_resp.json()]
    assert club_id not in anon_ids

    # Organizer (member) — SHOULD see their own private club
    auth_resp = await async_client.get("/api/v1/clubs", headers=org_headers)
    auth_ids = [c["id"] for c in auth_resp.json()]
    assert club_id in auth_ids


@pytest.mark.asyncio
async def test_list_clubs_search(async_client, register_user, auth_headers):
    await register_user(email="search_org@example.com")
    headers = await auth_headers(email="search_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "Unique Dragon Club", "description": "Dragons", "city": "Kyiv"},
    )

    resp = await async_client.get("/api/v1/clubs?search=Dragon")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert any("Dragon" in n for n in names)

    resp_none = await async_client.get("/api/v1/clubs?search=xyznotfound12345")
    assert resp_none.status_code == 200
    assert resp_none.json() == []


@pytest.mark.asyncio
async def test_join_club_banned(async_client, register_user, auth_headers):
    await register_user(email="ban_org@example.com")
    org_headers = await auth_headers(email="ban_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org_headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=org_headers,
        json={"name": "Ban Test Club", "description": "Desc", "city": "Kyiv"},
    )
    club_id = club_resp.json()["id"]

    await register_user(email="banned_user@example.com")
    user_headers = await auth_headers(email="banned_user@example.com")
    # User joins first so they can be banned
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)
    # Get user id
    me_resp = await async_client.get("/api/v1/users/me", headers=user_headers)
    user_id = me_resp.json()["id"]
    # Organizer bans the user permanently
    await async_client.post(
        f"/api/v1/clubs/{club_id}/members/{user_id}/ban",
        headers=org_headers,
        json={"duration": "permanent"},
    )

    # Banned user tries to rejoin
    rejoin_resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)
    assert rejoin_resp.status_code == 403
    assert "banned" in rejoin_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_leave_club_not_member(async_client, register_user, auth_headers):
    await register_user(email="leave_org2@example.com")
    org_headers = await auth_headers(email="leave_org2@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org_headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=org_headers,
        json={"name": "Leave Test Club", "description": "Desc", "city": "Kyiv"},
    )
    club_id = club_resp.json()["id"]

    await register_user(email="non_member@example.com")
    non_member_headers = await auth_headers(email="non_member@example.com")
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/leave", headers=non_member_headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_clubs_with_after_meeting_venue(async_client, register_user, auth_headers):
    await register_user(email="venue_org@example.com")
    headers = await auth_headers(email="venue_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={
            "name": "Venue Club",
            "description": "Desc",
            "city": "Kyiv",
            "afterMeetingVenue": {"name": "Café Books", "address": "St. 1", "description": "Nice café"},
        },
    )
    assert resp.status_code == 201
    club_id = resp.json()["id"]

    # Bulk path (list_clubs) should include afterMeetingVenue
    list_resp = await async_client.get("/api/v1/clubs", headers=headers)
    assert list_resp.status_code == 200
    club = next((c for c in list_resp.json() if c["id"] == club_id), None)
    assert club is not None
    assert club["afterMeetingVenue"] is not None
    assert club["afterMeetingVenue"]["name"] == "Café Books"

    # Single-club path (get_club) should also include afterMeetingVenue
    get_resp = await async_client.get(f"/api/v1/clubs/{club_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["afterMeetingVenue"]["name"] == "Café Books"


@pytest.mark.asyncio
async def test_create_club_as_organizer(async_client, register_user, auth_headers):
    await register_user(email="organizer_club@example.com")
    headers = await auth_headers(email="organizer_club@example.com")
    # Promote to organizer
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "SciFi Club", "description": "A club for sci-fi fans", "city": "Kyiv"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "SciFi Club"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_club_as_non_organizer(async_client, register_user, auth_headers):
    await register_user(email="nonorg_club@example.com")
    headers = await auth_headers(email="nonorg_club@example.com")
    resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "Book Club", "description": "A club", "city": "Kyiv"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_club_by_id(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "Test Club", "description": "Desc", "city": "Kyiv"}
    )
    club_id = resp.json()["id"]
    get_resp = await async_client.get(f"/api/v1/clubs/{club_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == club_id


@pytest.mark.asyncio
async def test_get_club_not_found(async_client):
    resp = await async_client.get("/api/v1/clubs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_join_club(async_client, register_user, auth_headers):
    # Organizer creates club
    await register_user()
    headers = await auth_headers()
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "Join Club", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]
    # Second user joins
    await register_user(email="user2@example.com")
    headers2 = await auth_headers(email="user2@example.com")
    join_resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    assert join_resp.status_code == 200
    assert "memberCount" in join_resp.json()


@pytest.mark.asyncio
async def test_join_club_already_member(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "JoinTwice", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]
    await register_user(email="user2@example.com")
    headers2 = await auth_headers(email="user2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_leave_club(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "Leave Club", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]
    await register_user(email="user2@example.com")
    headers2 = await auth_headers(email="user2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/leave", headers=headers2)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_my_clubs(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "My Club", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]
    resp = await async_client.get("/api/v1/clubs/my", headers=headers)
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert club_id in ids


@pytest.mark.asyncio
async def test_list_clubs_member_previews(async_client, register_user, auth_headers, db_session):
    await register_user(email="preview_org@example.com")
    org_headers = await auth_headers(email="preview_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org_headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=org_headers,
        json={"name": "Preview Club", "description": "Desc", "city": "Kyiv"},
    )
    assert club_resp.status_code == 201
    club_id = club_resp.json()["id"]

    # Set avatar_url directly on the organizer (who is already a member)
    me_resp = await async_client.get("/api/v1/users/me", headers=org_headers)
    user_id = me_resp.json()["id"]
    result = await db_session.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one()
    user.avatar_url = "https://example.com/avatar.png"
    await db_session.commit()

    resp = await async_client.get("/api/v1/clubs", headers=org_headers)
    assert resp.status_code == 200
    club = next(c for c in resp.json() if c["id"] == club_id)
    assert "https://example.com/avatar.png" in club["memberPreviews"]
