import pytest


async def create_organizer_with_club(async_client, register_user, auth_headers, email="org_mgmt@example.com"):
    await register_user(email=email)
    headers = await auth_headers(email=email)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "Mgmt Club", "description": "Desc", "city": "Kyiv"},
    )
    club_id = club_resp.json()["id"]
    return headers, club_id


# --- PATCH /clubs/{club_id} ---


@pytest.mark.asyncio
async def test_update_club(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    resp = await async_client.patch(
        f"/api/v1/clubs/{club_id}",
        headers=headers,
        json={"name": "Renamed Club", "description": "Updated", "isPublic": True, "city": "Lviv"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed Club"


@pytest.mark.asyncio
async def test_update_club_not_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="uc_org@example.com"
    )
    await register_user(email="uc_member@example.com")
    headers2 = await auth_headers(email="uc_member@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.patch(
        f"/api/v1/clubs/{club_id}",
        headers=headers2,
        json={"name": "Hack", "description": "", "isPublic": True, "city": "Lviv"},
    )
    assert resp.status_code == 403


# --- PATCH /clubs/{club_id}/pause ---


@pytest.mark.asyncio
async def test_pause_club(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="pause_org@example.com"
    )
    resp = await async_client.patch(f"/api/v1/clubs/{club_id}/pause", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"


@pytest.mark.asyncio
async def test_pause_club_not_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="pause_org2@example.com"
    )
    await register_user(email="pause_member@example.com")
    headers2 = await auth_headers(email="pause_member@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.patch(f"/api/v1/clubs/{club_id}/pause", headers=headers2)
    assert resp.status_code == 403


# --- PATCH /clubs/{club_id}/cancel ---


@pytest.mark.asyncio
async def test_cancel_club(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="cancel_org@example.com"
    )
    resp = await async_client.patch(f"/api/v1/clubs/{club_id}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_club_not_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="cancel_org2@example.com"
    )
    await register_user(email="cancel_member@example.com")
    headers2 = await auth_headers(email="cancel_member@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.patch(f"/api/v1/clubs/{club_id}/cancel", headers=headers2)
    assert resp.status_code == 403


# --- PATCH /clubs/{club_id}/reschedule ---


@pytest.mark.asyncio
async def test_reschedule_club(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="resched_org@example.com"
    )
    resp = await async_client.patch(
        f"/api/v1/clubs/{club_id}/reschedule",
        headers=headers,
        json={"newDate": "2026-09-01T18:00:00+00:00"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_reschedule_club_not_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="resched_org2@example.com"
    )
    await register_user(email="resched_member@example.com")
    headers2 = await auth_headers(email="resched_member@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.patch(
        f"/api/v1/clubs/{club_id}/reschedule",
        headers=headers2,
        json={"newDate": "2026-09-01T18:00:00+00:00"},
    )
    assert resp.status_code == 403


# --- GET /clubs/{club_id}/events ---


@pytest.mark.asyncio
async def test_list_club_events_empty(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="events_org@example.com"
    )
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/events", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []
