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
async def test_register_success(async_client):
    resp = await register_user(async_client)
    assert resp.status_code == 201
    data = resp.json()
    assert "user" in data and "accessToken" in data
    assert data["user"]["email"] == "test@example.com"
    assert "id" in data["user"]

@pytest.mark.asyncio
async def test_register_duplicate_email(async_client):
    await register_user(async_client)
    resp = await register_user(async_client)
    assert resp.status_code == 409

@pytest.mark.asyncio
async def test_register_invalid_email(async_client):
    resp = await register_user(async_client, email="not-an-email")
    assert resp.status_code == 422

@pytest.mark.asyncio
async def test_login_success(async_client):
    await register_user(async_client)
    resp = await async_client.post("/api/v1/auth/login", json={"email": "test@example.com", "password": "password123"})
    assert resp.status_code == 200
    data = resp.json()
    assert "accessToken" in data

@pytest.mark.asyncio
async def test_login_wrong_password(async_client):
    await register_user(async_client)
    resp = await async_client.post("/api/v1/auth/login", json={"email": "test@example.com", "password": "wrongpass"})
    assert resp.status_code == 401

@pytest.mark.asyncio
async def test_login_unknown_email(async_client):
    resp = await async_client.post("/api/v1/auth/login", json={"email": "unknown@example.com", "password": "password123"})
    assert resp.status_code == 401

@pytest.mark.asyncio
async def test_me_authenticated(async_client):
    await register_user(async_client)
    headers = await get_auth_headers(async_client)
    resp = await async_client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"

@pytest.mark.asyncio
async def test_me_unauthenticated(async_client):
    resp = await async_client.get("/api/v1/auth/me")
    assert resp.status_code == 401

@pytest.mark.asyncio
async def test_logout(async_client):
    await register_user(async_client)
    headers = await get_auth_headers(async_client)
    resp = await async_client.post("/api/v1/auth/logout", headers=headers)
    assert resp.status_code == 204
