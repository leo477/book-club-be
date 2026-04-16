import pytest


async def create_organizer_with_club(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "Randomizer Club", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]
    return headers, club_id


@pytest.mark.asyncio
async def test_randomizer_history_empty(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/randomizer/history", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_randomizer_session(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    candidates = [{"userId": "u1", "displayName": "Alice", "avatarUrl": None}]
    payload = {"purpose": "Pick a winner", "candidates": candidates, "result": None}
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/randomizer/sessions", headers=headers, json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data and data["purpose"] == "Pick a winner"
    assert data["candidates"][0]["displayName"] == "Alice"


@pytest.mark.asyncio
async def test_randomizer_history_after_create(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    candidates = [{"userId": "u1", "displayName": "Alice", "avatarUrl": None}]
    payload = {"purpose": "Pick a winner", "candidates": candidates, "result": None}
    await async_client.post(f"/api/v1/clubs/{club_id}/randomizer/sessions", headers=headers, json=payload)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/randomizer/history", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1
