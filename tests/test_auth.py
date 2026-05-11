import pytest


@pytest.mark.asyncio
async def test_register_success(async_client, register_user):
    resp = await register_user()
    assert resp.status_code == 201
    data = resp.json()
    assert "user" in data and "accessToken" in data
    assert "refreshToken" not in data
    assert data["user"]["email"] == "test@example.com"
    assert "id" in data["user"]


@pytest.mark.asyncio
async def test_register_duplicate_email(async_client, register_user):
    await register_user()
    resp = await register_user()
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_register_invalid_email(async_client, register_user):
    resp = await register_user(email="not-an-email")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_email_as_display_name(async_client, register_user):
    resp = await register_user(displayName="john@example.com")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_DISPLAY_NAME"


@pytest.mark.asyncio
async def test_register_sets_httponly_cookie(async_client, register_user):
    resp = await register_user()
    assert resp.status_code == 201
    assert "refresh_token" in resp.cookies


@pytest.mark.asyncio
async def test_login_success(async_client, register_user):
    await register_user()
    resp = await async_client.post("/api/v1/auth/login", json={"email": "test@example.com", "password": "password123"})
    assert resp.status_code == 200
    data = resp.json()
    assert "accessToken" in data


@pytest.mark.asyncio
async def test_login_wrong_password(async_client, register_user):
    await register_user()
    resp = await async_client.post("/api/v1/auth/login", json={"email": "test@example.com", "password": "wrongpass"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_email(async_client):
    resp = await async_client.post(
        "/api/v1/auth/login", json={"email": "unknown@example.com", "password": "password123"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_authenticated(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    resp = await async_client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_me_unauthenticated(async_client):
    resp = await async_client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    resp = await async_client.post("/api/v1/auth/logout", headers=headers)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_refresh_no_cookie(async_client):
    resp = await async_client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MISSING_REFRESH_TOKEN"


@pytest.mark.asyncio
async def test_refresh_invalid_token(async_client):
    resp = await async_client.post("/api/v1/auth/refresh", cookies={"refresh_token": "bad-token"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "INVALID_REFRESH_TOKEN"


@pytest.mark.asyncio
async def test_refresh_success(async_client, register_user):
    await register_user()
    resp = await async_client.post("/api/v1/auth/refresh", cookies={"refresh_token": "fake-refresh-token"})
    assert resp.status_code == 200
    assert "accessToken" in resp.json()
