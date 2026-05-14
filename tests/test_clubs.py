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


# ---------------------------------------------------------------------------
# pause / cancel / reschedule / update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_club(async_client, register_user, auth_headers):
    """Organizer can pause their club; status becomes 'paused'."""
    await register_user(email="pause_org@example.com")
    headers = await auth_headers(email="pause_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "Pause Test Club", "description": "Desc", "city": "Kyiv"},
    )
    assert club_resp.status_code == 201
    club_id = club_resp.json()["id"]

    resp = await async_client.patch(f"/api/v1/clubs/{club_id}/pause", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"


@pytest.mark.asyncio
async def test_pause_club_non_organizer(async_client, register_user, auth_headers):
    """A regular member cannot pause a club → 403."""
    await register_user(email="pause_org2@example.com")
    org_headers = await auth_headers(email="pause_org2@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org_headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=org_headers,
        json={"name": "Pause Perm Club", "description": "Desc", "city": "Kyiv"},
    )
    club_id = club_resp.json()["id"]

    await register_user(email="pause_member@example.com")
    member_headers = await auth_headers(email="pause_member@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=member_headers)

    resp = await async_client.patch(f"/api/v1/clubs/{club_id}/pause", headers=member_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cancel_club(async_client, register_user, auth_headers):
    """Organizer can cancel their club; status becomes 'cancelled' and cancelledAt is set."""
    await register_user(email="cancel_org@example.com")
    headers = await auth_headers(email="cancel_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "Cancel Test Club", "description": "Desc", "city": "Lviv"},
    )
    assert club_resp.status_code == 201
    club_id = club_resp.json()["id"]

    resp = await async_client.patch(f"/api/v1/clubs/{club_id}/cancel", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"
    assert data["cancelledAt"] is not None


@pytest.mark.asyncio
async def test_cancel_club_then_list_shows_cancelled_at(async_client, register_user, auth_headers):
    """After cancellation, listing clubs returns the club with cancelledAt populated (exercises build_club_responses_bulk)."""
    await register_user(email="cancel_list_org@example.com")
    headers = await auth_headers(email="cancel_list_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "CancelledBulk Club", "description": "Desc", "city": "Kyiv"},
    )
    club_id = club_resp.json()["id"]

    await async_client.patch(f"/api/v1/clubs/{club_id}/cancel", headers=headers)

    list_resp = await async_client.get("/api/v1/clubs", headers=headers)
    assert list_resp.status_code == 200
    club = next((c for c in list_resp.json() if c["id"] == club_id), None)
    assert club is not None
    assert club["status"] == "cancelled"
    assert club["cancelledAt"] is not None


@pytest.mark.asyncio
async def test_reschedule_club(async_client, register_user, auth_headers):
    """Organizer can reschedule; nextMeetingDate is updated and status becomes 'active'."""
    await register_user(email="reschedule_org@example.com")
    headers = await auth_headers(email="reschedule_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "Reschedule Club", "description": "Desc", "city": "Odesa"},
    )
    assert club_resp.status_code == 201
    club_id = club_resp.json()["id"]

    # First pause it so we can verify status reverts to active
    await async_client.patch(f"/api/v1/clubs/{club_id}/pause", headers=headers)

    new_date = "2030-06-15T18:00:00"
    resp = await async_client.patch(
        f"/api/v1/clubs/{club_id}/reschedule",
        headers=headers,
        json={"newDate": new_date},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert data["nextMeetingDate"] is not None
    assert "2030-06-15" in data["nextMeetingDate"]


@pytest.mark.asyncio
async def test_update_club_name(async_client, register_user, auth_headers):
    """Organizer can patch club name; other fields remain unchanged."""
    await register_user(email="update_org@example.com")
    headers = await auth_headers(email="update_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "Original Name", "description": "Some desc", "city": "Kharkiv"},
    )
    assert club_resp.status_code == 201
    club_id = club_resp.json()["id"]
    original_description = club_resp.json()["description"]

    resp = await async_client.patch(
        f"/api/v1/clubs/{club_id}",
        headers=headers,
        json={"name": "Updated Name"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Updated Name"
    # description should be unchanged (true PATCH)
    assert data["description"] == original_description


@pytest.mark.asyncio
async def test_update_club_non_organizer(async_client, register_user, auth_headers):
    """Non-organizer member cannot update a club → 403."""
    await register_user(email="upd_org@example.com")
    org_headers = await auth_headers(email="upd_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org_headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=org_headers,
        json={"name": "NoUpdate Club", "description": "Desc", "city": "Kyiv"},
    )
    club_id = club_resp.json()["id"]

    await register_user(email="upd_member@example.com")
    member_headers = await auth_headers(email="upd_member@example.com")

    resp = await async_client.patch(
        f"/api/v1/clubs/{club_id}",
        headers=member_headers,
        json={"name": "Hacked Name"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Club service: build_club_responses_bulk edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_club_responses_bulk_no_optional_fields(async_client, register_user, auth_headers):
    """Club created with all optional fields omitted is listed without errors."""
    await register_user(email="minimal_org@example.com")
    headers = await auth_headers(email="minimal_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})

    # Create club with minimal fields (no city, no tags, no afterMeetingVenue)
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "Minimal Club", "description": None},
    )
    assert club_resp.status_code == 201
    club_id = club_resp.json()["id"]

    list_resp = await async_client.get("/api/v1/clubs", headers=headers)
    assert list_resp.status_code == 200
    club = next((c for c in list_resp.json() if c["id"] == club_id), None)
    assert club is not None
    assert club["city"] is None
    assert club["tags"] == []
    assert club["afterMeetingVenue"] is None


@pytest.mark.asyncio
async def test_list_clubs_empty_returns_empty_list(async_client):
    """When no clubs exist the list endpoint returns an empty list (not 404 / error)."""
    resp = await async_client.get("/api/v1/clubs")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_delete_club(async_client, register_user, auth_headers):
    """Organizer can delete their club → 204, subsequent GET returns 404."""
    await register_user(email="delete_org@example.com")
    headers = await auth_headers(email="delete_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "Delete Me Club", "description": "Desc", "city": "Kyiv"},
    )
    assert club_resp.status_code == 201
    club_id = club_resp.json()["id"]

    del_resp = await async_client.delete(f"/api/v1/clubs/{club_id}", headers=headers)
    assert del_resp.status_code == 204

    get_resp = await async_client.get(f"/api/v1/clubs/{club_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_create_second_club_returns_409(async_client, register_user, auth_headers):
    """Organizer cannot create a second club — must receive 409 with ORGANIZER_ALREADY_HAS_CLUB."""
    await register_user(email="second_club_org@example.com")
    headers = await auth_headers(email="second_club_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})

    first_resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "First Club", "description": "Desc", "city": "Kyiv"},
    )
    assert first_resp.status_code == 201

    second_resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "Second Club", "description": "Desc", "city": "Lviv"},
    )
    assert second_resp.status_code == 409
    assert second_resp.json()["detail"]["code"] == "ORGANIZER_ALREADY_HAS_CLUB"
