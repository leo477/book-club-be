import uuid

import pytest


# Helpers
async def register_user(
    async_client, email="test@example.com", password="password123", displayName="Test User", role="user"
):
    return await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "displayName": displayName, "role": role},
    )


async def get_auth_headers(async_client, email="test@example.com", password="password123"):
    resp = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_get_meetings_empty(async_client):
    await register_user(async_client, email="meet_org1@example.com", role="user")
    headers = await get_auth_headers(async_client, email="meet_org1@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "MeetClub1", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/meetings", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_meetings_club_not_found(async_client):
    await register_user(async_client, email="meet_user1@example.com")
    headers = await get_auth_headers(async_client, email="meet_user1@example.com")
    fake_club_id = str(uuid.uuid4())
    resp = await async_client.get(f"/api/v1/clubs/{fake_club_id}/meetings", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_meetings_unauthenticated(async_client):
    await register_user(async_client, email="meet_org2@example.com", role="user")
    headers = await get_auth_headers(async_client, email="meet_org2@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "MeetClub2", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/meetings")
    assert resp.status_code == 401
