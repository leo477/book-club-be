import pytest

# Helper functions
async def register_user(async_client, email="test@example.com", password="password123", displayName="Test User", role="user"):
    return await async_client.post("/api/v1/auth/register", json={
        "email": email, "password": password, "displayName": displayName, "role": role
    })

async def get_auth_headers(async_client, email="test@example.com", password="password123"):
    resp = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}

@pytest.mark.asyncio
async def test_list_clubs_empty(async_client):
    resp = await async_client.get("/api/v1/clubs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

@pytest.mark.asyncio
async def test_create_club_as_organizer(async_client):
    await register_user(async_client, email="organizer_club@example.com")
    headers = await get_auth_headers(async_client, email="organizer_club@example.com")
    # Promote to organizer
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    resp = await async_client.post("/api/v1/clubs", headers=headers, json={"name": "SciFi Club", "description": "A club for sci-fi fans", "city": "Kyiv"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "SciFi Club"
    assert data["status"] == "active"
    assert "id" in data

@pytest.mark.asyncio
async def test_create_club_as_non_organizer(async_client):
    await register_user(async_client, email="nonorg_club@example.com")
    headers = await get_auth_headers(async_client, email="nonorg_club@example.com")
    resp = await async_client.post("/api/v1/clubs", headers=headers, json={"name": "Book Club", "description": "A club", "city": "Kyiv"})
    assert resp.status_code == 403

@pytest.mark.asyncio
async def test_get_club_by_id(async_client):
    await register_user(async_client)
    headers = await get_auth_headers(async_client)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    resp = await async_client.post("/api/v1/clubs", headers=headers, json={"name": "Test Club", "description": "Desc", "city": "Kyiv"})
    club_id = resp.json()["id"]
    get_resp = await async_client.get(f"/api/v1/clubs/{club_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == club_id

@pytest.mark.asyncio
async def test_get_club_not_found(async_client):
    resp = await async_client.get("/api/v1/clubs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404

@pytest.mark.asyncio
async def test_join_club(async_client):
    # Organizer creates club
    await register_user(async_client)
    headers = await get_auth_headers(async_client)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post("/api/v1/clubs", headers=headers, json={"name": "Join Club", "description": "Desc", "city": "Kyiv"})
    club_id = club_resp.json()["id"]
    # Second user joins
    await register_user(async_client, email="user2@example.com")
    headers2 = await get_auth_headers(async_client, email="user2@example.com")
    join_resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    assert join_resp.status_code == 200
    assert "memberCount" in join_resp.json()

@pytest.mark.asyncio
async def test_join_club_already_member(async_client):
    await register_user(async_client)
    headers = await get_auth_headers(async_client)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post("/api/v1/clubs", headers=headers, json={"name": "JoinTwice", "description": "Desc", "city": "Kyiv"})
    club_id = club_resp.json()["id"]
    await register_user(async_client, email="user2@example.com")
    headers2 = await get_auth_headers(async_client, email="user2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    assert resp.status_code == 409

@pytest.mark.asyncio
async def test_leave_club(async_client):
    await register_user(async_client)
    headers = await get_auth_headers(async_client)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post("/api/v1/clubs", headers=headers, json={"name": "Leave Club", "description": "Desc", "city": "Kyiv"})
    club_id = club_resp.json()["id"]
    await register_user(async_client, email="user2@example.com")
    headers2 = await get_auth_headers(async_client, email="user2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/leave", headers=headers2)
    assert resp.status_code == 204

@pytest.mark.asyncio
async def test_pause_club(async_client):
    await register_user(async_client)
    headers = await get_auth_headers(async_client)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post("/api/v1/clubs", headers=headers, json={"name": "Pause Club", "description": "Desc", "city": "Kyiv"})
    club_id = club_resp.json()["id"]
    resp = await async_client.patch(f"/api/v1/clubs/{club_id}/pause", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"

@pytest.mark.asyncio
async def test_cancel_club(async_client):
    await register_user(async_client)
    headers = await get_auth_headers(async_client)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post("/api/v1/clubs", headers=headers, json={"name": "Cancel Club", "description": "Desc", "city": "Kyiv"})
    club_id = club_resp.json()["id"]
    resp = await async_client.patch(f"/api/v1/clubs/{club_id}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

@pytest.mark.asyncio
async def test_my_clubs(async_client):
    await register_user(async_client)
    headers = await get_auth_headers(async_client)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post("/api/v1/clubs", headers=headers, json={"name": "My Club", "description": "Desc", "city": "Kyiv"})
    club_id = club_resp.json()["id"]
    resp = await async_client.get("/api/v1/clubs/my", headers=headers)
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert club_id in ids
