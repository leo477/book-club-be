import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def no_redirect_client(override_get_db):
    """Client that does not follow redirects, for asserting 302 Location/cookies."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False) as ac:
        yield ac


@pytest.mark.asyncio
async def test_oauth_google_redirects_to_provider(no_redirect_client):
    resp = await no_redirect_client.get("/api/v1/auth/oauth/google")
    assert resp.status_code == 302
    assert "authorize?provider=google" in resp.headers["location"]


@pytest.mark.asyncio
async def test_oauth_callback_missing_code(no_redirect_client):
    resp = await no_redirect_client.get("/api/v1/auth/callback")
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/login?oauth=failed")


@pytest.mark.asyncio
async def test_oauth_callback_invalid_code(no_redirect_client):
    resp = await no_redirect_client.get("/api/v1/auth/callback", params={"code": "bad-code"})
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/login?oauth=failed")


@pytest.mark.asyncio
async def test_oauth_callback_success_sets_cookie_and_creates_user(no_redirect_client):
    resp = await no_redirect_client.get("/api/v1/auth/callback", params={"code": "good-code"})
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/auth/callback")
    assert "refresh_token" in resp.cookies


_VALID_ORIGIN = "https://book-club-preview-abc123.vercel.app"


def _fe_origin_cookie(resp) -> str | None:
    for header in resp.headers.get_list("set-cookie"):
        if header.startswith("fe_origin="):
            return header.split("=", 1)[1].split(";", 1)[0].strip('"')
    return None


@pytest.mark.asyncio
async def test_oauth_google_valid_origin_param_sets_cookie(no_redirect_client):
    resp = await no_redirect_client.get("/api/v1/auth/oauth/google", params={"origin": _VALID_ORIGIN})
    assert resp.status_code == 302
    assert _fe_origin_cookie(resp) == _VALID_ORIGIN


@pytest.mark.asyncio
async def test_oauth_google_evil_origin_no_cookie(no_redirect_client):
    resp = await no_redirect_client.get("/api/v1/auth/oauth/google", params={"origin": "https://evil.com"})
    assert resp.status_code == 302
    assert _fe_origin_cookie(resp) is None


@pytest.mark.asyncio
async def test_oauth_google_resolves_origin_from_referer(no_redirect_client):
    resp = await no_redirect_client.get("/api/v1/auth/oauth/google", headers={"Referer": f"{_VALID_ORIGIN}/login"})
    assert resp.status_code == 302
    assert _fe_origin_cookie(resp) == _VALID_ORIGIN


@pytest.mark.asyncio
async def test_oauth_callback_success_redirects_to_resolved_origin(no_redirect_client):
    resp = await no_redirect_client.get(
        "/api/v1/auth/callback",
        params={"code": "good-code"},
        cookies={"fe_origin": _VALID_ORIGIN},
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == f"{_VALID_ORIGIN}/auth/callback"
    assert "refresh_token" in resp.cookies


@pytest.mark.asyncio
async def test_oauth_callback_failure_uses_resolved_origin(no_redirect_client):
    resp = await no_redirect_client.get(
        "/api/v1/auth/callback",
        params={"code": "bad-code"},
        cookies={"fe_origin": _VALID_ORIGIN},
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == f"{_VALID_ORIGIN}/login?oauth=failed"


@pytest.mark.asyncio
async def test_oauth_callback_evil_cookie_falls_back_to_frontend_url(no_redirect_client):
    resp = await no_redirect_client.get(
        "/api/v1/auth/callback",
        params={"code": "good-code"},
        cookies={"fe_origin": "https://evil.com"},
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:4200/auth/callback"


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
