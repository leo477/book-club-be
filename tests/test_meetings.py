import uuid

import pytest


@pytest.mark.asyncio
async def test_get_meetings_empty(async_client, register_user, auth_headers):
    await register_user(email="meet_org1@example.com", role="user")
    headers = await auth_headers(email="meet_org1@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "MeetClub1", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/events", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_meetings_club_not_found(async_client, register_user, auth_headers):
    await register_user(email="meet_user1@example.com")
    headers = await auth_headers(email="meet_user1@example.com")
    fake_club_id = str(uuid.uuid4())
    resp = await async_client.get(f"/api/v1/clubs/{fake_club_id}/events", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_meetings_unauthenticated(async_client, register_user, auth_headers):
    await register_user(email="meet_org2@example.com", role="user")
    headers = await auth_headers(email="meet_org2@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "MeetClub2", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/events")
    assert resp.status_code == 200
    assert resp.json() == []
