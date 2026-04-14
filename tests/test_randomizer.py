import pytest


# Helpers
async def register_user(
    async_client, email="test@example.com", password="password123", displayName="Test User", role="user"
):
    return await async_client.post(
        "/api/v1/auth/register", json={"email": email, "password": password, "displayName": displayName, "role": role}
    )


async def get_auth_headers(async_client, email="test@example.com", password="password123"):
    resp = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


async def create_organizer_with_club(async_client):
    await register_user(async_client)
    headers = await get_auth_headers(async_client)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "Randomizer Club", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]
    return headers, club_id


@pytest.mark.asyncio
async def test_randomizer_history_empty(async_client):
    headers, club_id = await create_organizer_with_club(async_client)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/randomizer/history", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_randomizer_session(async_client):
    headers, club_id = await create_organizer_with_club(async_client)
    candidates = [{"userId": "u1", "displayName": "Alice", "avatarUrl": None}]
    payload = {"purpose": "Pick a winner", "candidates": candidates, "result": None}
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/randomizer/sessions", headers=headers, json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data and data["purpose"] == "Pick a winner"
    assert data["candidates"][0]["displayName"] == "Alice"


@pytest.mark.asyncio
async def test_randomizer_history_after_create(async_client):
    headers, club_id = await create_organizer_with_club(async_client)
    candidates = [{"userId": "u1", "displayName": "Alice", "avatarUrl": None}]
    payload = {"purpose": "Pick a winner", "candidates": candidates, "result": None}
    await async_client.post(f"/api/v1/clubs/{club_id}/randomizer/sessions", headers=headers, json=payload)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/randomizer/history", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1
