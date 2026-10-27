import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs
from supabase_auth.errors import AuthApiError

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
async def test_oauth_callback_success_redirects_with_handoff_code(no_redirect_client):
    resp = await no_redirect_client.get("/api/v1/auth/callback", params={"code": "good-code"})
    assert resp.status_code == 302
    assert "/auth/callback?code=" in resp.headers["location"]
    # oauth_callback no longer sets cookies on the backend domain; the SPA gets the
    # session via POST /oauth/exchange using the handoff code above.
    assert "refresh_token" not in resp.cookies


_VALID_ORIGIN = "https://book-club-planer.vercel.app"


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
        headers={"Cookie": f"fe_origin={_VALID_ORIGIN}"},
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith(f"{_VALID_ORIGIN}/auth/callback?code=")
    assert "refresh_token" not in resp.cookies


@pytest.mark.asyncio
async def test_oauth_callback_failure_uses_resolved_origin(no_redirect_client):
    resp = await no_redirect_client.get(
        "/api/v1/auth/callback",
        params={"code": "bad-code"},
        headers={"Cookie": f"fe_origin={_VALID_ORIGIN}"},
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == f"{_VALID_ORIGIN}/login?oauth=failed"


@pytest.mark.asyncio
async def test_oauth_callback_evil_cookie_falls_back_to_frontend_url(no_redirect_client):
    resp = await no_redirect_client.get(
        "/api/v1/auth/callback",
        params={"code": "good-code"},
        headers={"Cookie": "fe_origin=https://evil.com"},
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("http://localhost:4200/auth/callback?code=")


@pytest.mark.asyncio
async def test_register_success(async_client, register_user):
    resp = await register_user()
    assert resp.status_code == 201
    data = resp.json()
    assert "user" in data and "accessToken" in data
    assert data["refreshToken"] == "fake-refresh-token"
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
    assert data["refreshToken"] == "fake-refresh-token"


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
    resp = await async_client.post("/api/v1/auth/refresh", headers={"Cookie": "refresh_token=bad-token"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "INVALID_REFRESH_TOKEN"


@pytest.mark.asyncio
async def test_refresh_success(async_client, register_user):
    await register_user()
    resp = await async_client.post("/api/v1/auth/refresh", headers={"Cookie": "refresh_token=fake-refresh-token"})
    assert resp.status_code == 200
    assert "accessToken" in resp.json()


@pytest.mark.asyncio
async def test_oauth_callback_unexpected_exchange_error_redirects_failed(no_redirect_client):
    from unittest.mock import AsyncMock, patch

    with patch("app.routers.auth.supabase_exchange_code", new=AsyncMock(side_effect=RuntimeError("boom"))):
        resp = await no_redirect_client.get("/api/v1/auth/callback", params={"code": "good-code"})
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/login?oauth=failed")


@pytest.mark.asyncio
async def test_oauth_callback_missing_user_redirects_failed(no_redirect_client):
    from unittest.mock import AsyncMock, MagicMock, patch

    empty = MagicMock()
    empty.user = None
    empty.session = None
    with patch("app.routers.auth.supabase_exchange_code", new=AsyncMock(return_value=empty)):
        resp = await no_redirect_client.get("/api/v1/auth/callback", params={"code": "good-code"})
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/login?oauth=failed")


@pytest.mark.asyncio
async def test_oauth_callback_links_existing_email(no_redirect_client, db_session):
    """OAuth for an email that already has a local user links the Supabase id instead of 500."""
    import uuid

    from sqlalchemy import select

    from app.models.user import User

    stale_id = uuid.uuid4()  # e.g. left over from an earlier email/password signup
    existing = User(
        id=uuid.uuid4(),
        supabase_user_id=stale_id,
        email="oauth@example.com",
        display_name="Existing Reader",
        role="user",
        socials_public=False,
    )
    db_session.add(existing)
    await db_session.commit()

    resp = await no_redirect_client.get("/api/v1/auth/callback", params={"code": "good-code"})
    assert resp.status_code == 302
    assert "/auth/callback?code=" in resp.headers["location"]
    assert "refresh_token" not in resp.cookies

    db_session.expire_all()  # request committed in its own session; force a fresh DB read
    row = await db_session.execute(select(User).where(User.email == "oauth@example.com"))
    linked = row.scalar_one()
    # supabase_user_id must be re-linked to the OAuth identity, not the stale one,
    # otherwise get_current_user (resolves by supabase_user_id) would 401 on /me.
    assert linked.supabase_user_id is not None
    assert linked.supabase_user_id != stale_id
    assert linked.display_name == "Existing Reader"


@pytest.mark.asyncio
async def test_oauth_callback_provision_failure_redirects_failed(no_redirect_client):
    """A DB/provisioning error after a valid code exchange redirects instead of leaking a 500."""
    from unittest.mock import AsyncMock, patch

    with patch("app.routers.auth._get_or_create_user", new=AsyncMock(side_effect=RuntimeError("db down"))):
        resp = await no_redirect_client.get("/api/v1/auth/callback", params={"code": "good-code"})
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/login?oauth=failed")


@pytest.mark.asyncio
async def test_oauth_exchange_invalid_code(async_client):
    resp = await async_client.post("/api/v1/auth/oauth/exchange", json={"code": "missing"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_OAUTH_CODE"


@pytest.mark.asyncio
async def test_oauth_exchange_returns_tokens_and_is_one_time(async_client):
    import json
    from unittest.mock import AsyncMock

    from app.dependencies import get_redis

    payload = json.dumps({"accessToken": "access-x", "refreshToken": "refresh-x", "userId": "uid"})
    mock_redis = AsyncMock()
    mock_redis.getdel = AsyncMock(return_value=payload)
    app.dependency_overrides[get_redis] = lambda: mock_redis

    resp = await async_client.post("/api/v1/auth/oauth/exchange", json={"code": "handoff"})
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"accessToken": "access-x", "refreshToken": "refresh-x"}
    mock_redis.getdel.assert_awaited_once_with("oauth:handoff:handoff")


@pytest.mark.asyncio
async def test_refresh_with_body_token(async_client, register_user):
    await register_user()
    resp = await async_client.post("/api/v1/auth/refresh", json={"refreshToken": "fake-refresh-token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["accessToken"]
    assert data["refreshToken"] == "fake-refresh-token"


def _set_cookie_header(resp, name: str) -> str | None:
    for header in resp.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    return None


@pytest.mark.asyncio
async def test_login_sets_both_session_cookies_samesite_lax(async_client, register_user):
    await register_user()
    resp = await async_client.post("/api/v1/auth/login", json={"email": "test@example.com", "password": "password123"})
    assert resp.status_code == 200
    for name in ("refresh_token", "access_token"):
        header = _set_cookie_header(resp, name)
        assert header is not None, f"{name} cookie not set"
        assert "samesite=lax" in header.lower()
    assert "httponly" in _set_cookie_header(resp, "refresh_token").lower()
    assert "httponly" in _set_cookie_header(resp, "access_token").lower()


@pytest.mark.asyncio
async def test_refresh_sets_both_session_cookies(async_client, register_user):
    await register_user()
    resp = await async_client.post("/api/v1/auth/refresh", headers={"Cookie": "refresh_token=fake-refresh-token"})
    assert resp.status_code == 200
    for name in ("refresh_token", "access_token"):
        assert _set_cookie_header(resp, name) is not None


@pytest.mark.asyncio
async def test_oauth_exchange_sets_both_session_cookies(async_client):
    import json
    from unittest.mock import AsyncMock

    from app.dependencies import get_redis

    payload = json.dumps({"accessToken": "access-x", "refreshToken": "refresh-x", "userId": "uid"})
    mock_redis = AsyncMock()
    mock_redis.getdel = AsyncMock(return_value=payload)
    app.dependency_overrides[get_redis] = lambda: mock_redis

    resp = await async_client.post("/api/v1/auth/oauth/exchange", json={"code": "handoff"})
    assert resp.status_code == 200
    for name in ("refresh_token", "access_token"):
        assert _set_cookie_header(resp, name) is not None


@pytest.mark.asyncio
async def test_logout_clears_both_session_cookies(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    resp = await async_client.post("/api/v1/auth/logout", headers=headers)
    assert resp.status_code == 204
    for name in ("refresh_token", "access_token"):
        header = _set_cookie_header(resp, name)
        assert header is not None
        assert f'{name}=""' in header or f"{name}=;" in header or "01 Jan 1970" in header


@pytest.mark.asyncio
async def test_get_current_user_accepts_cookie_auth(async_client, register_user):
    resp = await register_user()
    token = resp.json()["accessToken"]
    async_client.cookies.clear()
    async_client.cookies.set("access_token", token)
    me_resp = await async_client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_auth_endpoints_send_no_store_cache_header(async_client, register_user):
    resp = await register_user()
    assert resp.headers["cache-control"] == "no-store"
    me_resp = await async_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {resp.json()['accessToken']}"}
    )
    assert me_resp.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_session_status_false_without_cookie(async_client):
    resp = await async_client.get("/api/v1/auth/session-status")
    assert resp.status_code == 200
    assert resp.json() == {"hasSession": False}
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_session_status_true_with_refresh_cookie(async_client):
    async_client.cookies.set("refresh_token", "some-refresh-token")
    resp = await async_client.get("/api/v1/auth/session-status")
    assert resp.status_code == 200
    assert resp.json() == {"hasSession": True}
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_csrf_blocks_cross_origin_cookie_authenticated_post(async_client, register_user):
    resp = await register_user()
    token = resp.json()["accessToken"]
    async_client.cookies.clear()
    async_client.cookies.set("access_token", token)
    bad = await async_client.post("/api/v1/auth/logout", headers={"Origin": "https://evil.com"})
    assert bad.status_code == 403
    assert bad.json()["detail"]["code"] == "CSRF_ORIGIN_MISMATCH"


@pytest.mark.asyncio
async def test_csrf_blocks_cross_origin_post_with_only_a_refresh_cookie(async_client):
    async_client.cookies.set("refresh_token", "some-refresh-token")
    bad = await async_client.post("/api/v1/auth/refresh", headers={"Origin": "https://evil.com"})
    assert bad.status_code == 403
    assert bad.json()["detail"]["code"] == "CSRF_ORIGIN_MISMATCH"


@pytest.mark.asyncio
async def test_csrf_lets_a_trusted_origin_refresh_with_only_a_refresh_cookie(async_client):
    async_client.cookies.set("refresh_token", "some-refresh-token")
    resp = await async_client.post("/api/v1/auth/refresh", headers={"Origin": "http://localhost:4200"})
    assert resp.status_code != 403 or resp.json()["detail"]["code"] != "CSRF_ORIGIN_MISMATCH"


@pytest.mark.asyncio
async def test_csrf_allows_same_origin_cookie_authenticated_post(async_client, register_user):
    resp = await register_user()
    token = resp.json()["accessToken"]
    async_client.cookies.clear()
    async_client.cookies.set("access_token", token)
    ok = await async_client.post("/api/v1/auth/logout", headers={"Origin": "http://localhost:4200"})
    assert ok.status_code == 204


@pytest.mark.asyncio
async def test_csrf_allows_bearer_authenticated_post_cross_origin(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    async_client.cookies.clear()
    ok = await async_client.post("/api/v1/auth/logout", headers={**headers, "Origin": "https://evil.com"})
    assert ok.status_code == 204


@pytest.mark.asyncio
async def test_ws_ticket_mints_and_is_single_use(async_client, register_user):
    from unittest.mock import AsyncMock

    from app.dependencies import get_redis

    resp = await register_user()
    token = resp.json()["accessToken"]

    store: dict[str, str] = {}

    async def _set(key, value, ex=None):
        store[key] = value

    async def _getdel(key):
        return store.pop(key, None)

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(side_effect=_set)
    mock_redis.getdel = AsyncMock(side_effect=_getdel)
    app.dependency_overrides[get_redis] = lambda: mock_redis

    ticket_resp = await async_client.post("/api/v1/auth/ws-ticket", headers={"Authorization": f"Bearer {token}"})
    assert ticket_resp.status_code == 200
    ticket = ticket_resp.json()["ticket"]
    assert f"ws:ticket:{ticket}" in store

    consumed = await mock_redis.getdel(f"ws:ticket:{ticket}")
    assert consumed is not None
    assert await mock_redis.getdel(f"ws:ticket:{ticket}") is None


_GOOD_ORIGINS = [
    "https://book-club-planer.vercel.app",
    "https://book-club-web-blue.vercel.app",
    "https://book-club-fe.vercel.app",
]
_BAD_ORIGINS = [
    "https://book-club-evil-x.vercel.app",
    "http://book-club-planer.vercel.app",
    "https://book-club-planer.vercel.app.evil.com",
    "https://xbook-club-planer.vercel.app",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", _GOOD_ORIGINS)
async def test_origin_allowlist_accepts_known_frontends(no_redirect_client, origin):
    oauth = await no_redirect_client.get("/api/v1/auth/oauth/google", params={"origin": origin})
    assert _fe_origin_cookie(oauth) == origin

    preflight = await no_redirect_client.options(
        "/api/v1/auth/login", headers={"Origin": origin, "Access-Control-Request-Method": "POST"}
    )
    assert preflight.headers.get("access-control-allow-origin") == origin

    csrf = await no_redirect_client.post("/api/v1/auth/logout", headers={"Origin": origin, "Cookie": "access_token=x"})
    assert csrf.status_code != 403


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", _BAD_ORIGINS)
async def test_origin_allowlist_rejects_lookalikes(no_redirect_client, origin):
    oauth = await no_redirect_client.get("/api/v1/auth/oauth/google", params={"origin": origin})
    assert _fe_origin_cookie(oauth) is None

    preflight = await no_redirect_client.options(
        "/api/v1/auth/login", headers={"Origin": origin, "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in preflight.headers

    csrf = await no_redirect_client.post("/api/v1/auth/logout", headers={"Origin": origin, "Cookie": "access_token=x"})
    assert csrf.status_code == 403
    assert csrf.json()["detail"]["code"] == "CSRF_ORIGIN_MISMATCH"


def _pkce(verifier: str) -> str:
    import base64
    import hashlib

    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


_VERIFIER = "v" * 43


def _exchange_redis(challenge: str | None):
    import json
    from unittest.mock import AsyncMock

    from app.dependencies import get_redis

    payload = json.dumps({"accessToken": "a", "refreshToken": "r", "userId": "u", "challenge": challenge})
    mock_redis = AsyncMock()
    mock_redis.getdel = AsyncMock(return_value=payload)
    app.dependency_overrides[get_redis] = lambda: mock_redis
    return mock_redis


@pytest.mark.asyncio
async def test_oauth_google_challenge_sets_cookie(no_redirect_client):
    resp = await no_redirect_client.get("/api/v1/auth/oauth/google", params={"challenge": _pkce(_VERIFIER)})
    header = _set_cookie_header(resp, "oauth_challenge")
    assert header is not None
    assert _pkce(_VERIFIER) in header
    assert "httponly" in header.lower()
    assert "samesite=lax" in header.lower()
    assert "path=/api/v1/auth" in header.lower()


@pytest.mark.asyncio
async def test_oauth_google_rejects_malformed_challenge(no_redirect_client):
    resp = await no_redirect_client.get("/api/v1/auth/oauth/google", params={"challenge": "short"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_oauth_google_without_challenge_sets_no_cookie(no_redirect_client):
    resp = await no_redirect_client.get("/api/v1/auth/oauth/google")
    header = _set_cookie_header(resp, "oauth_challenge")
    assert header is None or 'oauth_challenge=""' in header


@pytest.mark.asyncio
async def test_oauth_callback_stores_challenge_from_cookie(no_redirect_client):
    import json
    from unittest.mock import AsyncMock

    from app.dependencies import get_redis

    mock_redis = AsyncMock()
    app.dependency_overrides[get_redis] = lambda: mock_redis
    resp = await no_redirect_client.get(
        "/api/v1/auth/callback", params={"code": "good-code"}, headers={"Cookie": f"oauth_challenge={_pkce(_VERIFIER)}"}
    )
    assert resp.status_code == 302
    stored = json.loads(mock_redis.set.await_args.args[1])
    assert stored["challenge"] == _pkce(_VERIFIER)
    assert 'oauth_challenge=""' in "".join(resp.headers.get_list("set-cookie"))


def _enforce_pkce():
    from app.config import Settings, get_settings
    from app.dependencies import get_settings_dep

    base = get_settings()
    app.dependency_overrides[get_settings_dep] = lambda: Settings(**{**base.model_dump(), "OAUTH_REQUIRE_PKCE": True})


def _callback_redis():
    from unittest.mock import AsyncMock

    from app.dependencies import get_redis

    mock_redis = AsyncMock()
    app.dependency_overrides[get_redis] = lambda: mock_redis
    return mock_redis


def _challenge_cleared(resp) -> bool:
    return 'oauth_challenge=""' in "".join(resp.headers.get_list("set-cookie"))


@pytest.mark.asyncio
async def test_oauth_callback_requires_challenge_when_enforced(no_redirect_client):
    _enforce_pkce()
    mock_redis = _callback_redis()
    resp = await no_redirect_client.get("/api/v1/auth/callback", params={"code": "good-code"})
    assert resp.headers["location"].endswith("/login?oauth=failed")
    mock_redis.set.assert_not_awaited()
    assert _challenge_cleared(resp)


@pytest.mark.asyncio
async def test_oauth_callback_enforced_with_valid_cookie_succeeds(no_redirect_client):
    _enforce_pkce()
    mock_redis = _callback_redis()
    resp = await no_redirect_client.get(
        "/api/v1/auth/callback", params={"code": "good-code"}, headers={"Cookie": f"oauth_challenge={_pkce(_VERIFIER)}"}
    )
    assert "/auth/callback?code=" in resp.headers["location"]
    mock_redis.set.assert_awaited_once()
    assert _challenge_cleared(resp)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cookie", [b"oauth_challenge=short", b"oauth_challenge=" + "é".encode() * 43, b"oauth_challenge=" + b"a" * 44]
)
@pytest.mark.parametrize("enforced", [False, True])
async def test_oauth_callback_malformed_cookie_treated_as_absent(no_redirect_client, cookie, enforced):
    if enforced:
        _enforce_pkce()
    mock_redis = _callback_redis()
    resp = await no_redirect_client.get(
        "/api/v1/auth/callback", params={"code": "good-code"}, headers={"Cookie": cookie}
    )
    assert _challenge_cleared(resp)
    if enforced:
        assert resp.headers["location"].endswith("/login?oauth=failed")
        mock_redis.set.assert_not_awaited()
    else:
        assert "/auth/callback?code=" in resp.headers["location"]
        import json

        assert json.loads(mock_redis.set.await_args.args[1])["challenge"] is None


@pytest.mark.asyncio
async def test_oauth_callback_clears_cookie_on_invalid_code(no_redirect_client):
    resp = await no_redirect_client.get(
        "/api/v1/auth/callback", params={"code": "bad"}, headers={"Cookie": f"oauth_challenge={_pkce(_VERIFIER)}"}
    )
    assert resp.headers["location"].endswith("/login?oauth=failed")
    assert _challenge_cleared(resp)


@pytest.mark.asyncio
async def test_oauth_callback_clears_cookie_on_unexpected_error(no_redirect_client):
    from unittest.mock import AsyncMock, patch

    with patch("app.routers.auth.get_supabase_client", AsyncMock(side_effect=RuntimeError("boom"))):
        resp = await no_redirect_client.get(
            "/api/v1/auth/callback",
            params={"code": "good-code"},
            headers={"Cookie": f"oauth_challenge={_pkce(_VERIFIER)}"},
        )
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/login?oauth=failed")
    assert _challenge_cleared(resp)


@pytest.mark.asyncio
async def test_oauth_google_without_challenge_deletes_stale_cookie(no_redirect_client):
    resp = await no_redirect_client.get(
        "/api/v1/auth/oauth/google", headers={"Cookie": f"oauth_challenge={_pkce(_VERIFIER)}"}
    )
    header = _set_cookie_header(resp, "oauth_challenge")
    assert header is not None
    assert 'oauth_challenge=""' in header
    assert "path=/api/v1/auth" in header.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("verifier", ["é" * 43, "v" * 42, "v" * 129, "v" * 42 + "!"])
async def test_oauth_exchange_malformed_verifier_is_422(async_client, verifier):
    _exchange_redis(_pkce(_VERIFIER))
    resp = await async_client.post("/api/v1/auth/oauth/exchange", json={"code": "c", "code_verifier": verifier})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_oauth_exchange_with_matching_verifier(async_client):
    _exchange_redis(_pkce(_VERIFIER))
    resp = await async_client.post("/api/v1/auth/oauth/exchange", json={"code": "c", "code_verifier": _VERIFIER})
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("body_extra", [{}, {"code_verifier": "w" * 43}])
async def test_oauth_exchange_bound_code_rejects_missing_or_wrong_verifier(async_client, body_extra):
    mock_redis = _exchange_redis(_pkce(_VERIFIER))
    resp = await async_client.post("/api/v1/auth/oauth/exchange", json={"code": "c", **body_extra})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_OAUTH_CODE"
    mock_redis.getdel.assert_awaited_once()
    assert _set_cookie_header(resp, "access_token") is None


@pytest.mark.asyncio
async def test_oauth_exchange_unbound_code_still_works_when_not_enforced(async_client):
    _exchange_redis(None)
    resp = await async_client.post("/api/v1/auth/oauth/exchange", json={"code": "c"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_oauth_exchange_unbound_code_rejected_when_enforced(async_client):
    from app.config import Settings, get_settings
    from app.dependencies import get_settings_dep

    base = get_settings()
    app.dependency_overrides[get_settings_dep] = lambda: Settings(**{**base.model_dump(), "OAUTH_REQUIRE_PKCE": True})
    _exchange_redis(None)
    resp = await async_client.post("/api/v1/auth/oauth/exchange", json={"code": "c"})
    assert resp.status_code == 400


# --- SCRUM-167: per-account rate limit (credential stuffing from many IPs) ---


def _client_from_ip(ip: str):
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 1234)), base_url="http://test")


@pytest.mark.asyncio
async def test_login_per_account_limit_across_ips(override_get_db):
    body = {"email": "Victim@Example.com", "password": "wrong-password"}
    for i in range(10):
        async with _client_from_ip(f"10.0.0.{i + 1}") as c:
            resp = await c.post("/api/v1/auth/login", json=body)
        assert resp.status_code != 429, i
    async with _client_from_ip("10.0.1.1") as c:
        resp = await c.post("/api/v1/auth/login", json={**body, "email": "victim@example.com "})
        other = await c.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "x"})
    assert resp.status_code == 429
    assert "detail" in resp.json()
    assert other.status_code != 429


@pytest.mark.asyncio
async def test_register_per_account_limit_across_ips(override_get_db):
    def body(email):
        return {"email": email, "password": "password123", "displayName": "Name"}

    for i in range(5):
        async with _client_from_ip(f"10.0.2.{i + 1}") as c:
            resp = await c.post("/api/v1/auth/register", json=body("dup@example.com"))
        assert resp.status_code != 429, i
    async with _client_from_ip("10.0.3.1") as c:
        blocked = await c.post("/api/v1/auth/register", json=body("DUP@example.com"))
        free = await c.post("/api/v1/auth/register", json=body("fresh@example.com"))
    assert blocked.status_code == 429
    assert free.status_code != 429


def test_account_hash_shape_and_normalization():
    from app.config import get_settings
    from app.routers.auth import _account_hash

    st = get_settings()
    h = _account_hash(" A@B.com ", st)
    assert len(h) == 32
    assert h == _account_hash("a@b.com", st)
    assert h != _account_hash("c@b.com", st)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["login", "register"])
@pytest.mark.parametrize(
    "body",
    [
        {"password": "password123", "displayName": "N"},
        {"email": "not-an-email", "password": "password123", "displayName": "N"},
    ],
)
async def test_email_validation_error_reported_once(override_get_db, route, body):
    async with _client_from_ip("10.0.4.1") as c:
        resp = await c.post(f"/api/v1/auth/{route}", json=body)
    assert resp.status_code == 422
    email_errors = [e for e in resp.json()["detail"] if e["loc"] == ["body", "email"]]
    assert len(email_errors) == 1


@pytest.mark.asyncio
async def test_account_429_has_same_headers_as_ip_429(override_get_db):
    body = {"email": "hdr@example.com", "password": "wrong-password"}
    for i in range(10):
        async with _client_from_ip(f"10.0.5.{i + 1}") as c:
            await c.post("/api/v1/auth/login", json=body)
    async with _client_from_ip("10.0.6.1") as c:
        acct = await c.post("/api/v1/auth/login", json=body)
    assert acct.status_code == 429
    # per-IP 429 (11th request from one IP, distinct emails)
    async with _client_from_ip("10.0.7.1") as c:
        for i in range(10):
            await c.post("/api/v1/auth/login", json={"email": f"u{i}@example.com", "password": "x"})
        ip = await c.post("/api/v1/auth/login", json={"email": "u99@example.com", "password": "x"})
    assert ip.status_code == 429
    assert ("retry-after" in acct.headers) == ("retry-after" in ip.headers)
    assert acct.json().keys() == ip.json().keys() == {"detail"}


# ── SCRUM-81: WEB_TOKENS_IN_BODY ─────────────────────────────────────────────

TRUSTED = {"Origin": "http://localhost:4200"}
UNTRUSTED = {"Origin": "https://evil.com"}


@pytest.fixture
def tokens_out_of_body():
    from app.dependencies import get_settings_dep
    from tests.conftest import _make_test_settings

    original = app.dependency_overrides[get_settings_dep]
    settings = _make_test_settings().model_copy(update={"WEB_TOKENS_IN_BODY": False})
    app.dependency_overrides[get_settings_dep] = lambda: settings
    yield
    app.dependency_overrides[get_settings_dep] = original


@pytest.mark.asyncio
async def test_default_keeps_tokens_in_the_body_for_web_origins(async_client):
    resp = await async_client.post(
        "/api/v1/auth/register",
        headers=TRUSTED,
        json={"email": "wt1@example.com", "password": "password123", "displayName": "W", "role": "user"},
    )
    assert resp.status_code == 201
    assert resp.json()["accessToken"] and resp.json()["refreshToken"]


@pytest.mark.asyncio
async def test_register_and_login_omit_tokens_for_a_trusted_origin_but_keep_the_user(async_client, tokens_out_of_body):
    reg = await async_client.post(
        "/api/v1/auth/register",
        headers=TRUSTED,
        json={"email": "wt2@example.com", "password": "password123", "displayName": "W", "role": "user"},
    )
    assert reg.status_code == 201
    assert "accessToken" not in reg.json() and "refreshToken" not in reg.json()
    assert reg.json()["user"]["email"] == "wt2@example.com"
    assert "avatarUrl" in reg.json()["user"]  # null fields of the user are not dropped
    assert _set_cookie_header(reg, "access_token") and _set_cookie_header(reg, "refresh_token")

    login = await async_client.post(
        "/api/v1/auth/login", headers=TRUSTED, json={"email": "wt2@example.com", "password": "password123"}
    )
    assert login.status_code == 200
    assert "accessToken" not in login.json() and "refreshToken" not in login.json()
    assert login.json()["user"].keys() == reg.json()["user"].keys()
    assert _set_cookie_header(login, "access_token")


@pytest.mark.asyncio
async def test_refresh_omits_tokens_for_a_trusted_origin_and_still_sets_cookies(async_client, tokens_out_of_body):
    resp = await async_client.post("/api/v1/auth/refresh", headers=TRUSTED, json={"refreshToken": "fake-refresh-token"})
    assert resp.status_code == 200
    assert resp.json() == {}
    assert _set_cookie_header(resp, "access_token") and _set_cookie_header(resp, "refresh_token")


@pytest.mark.asyncio
async def test_clients_without_a_trusted_origin_still_get_tokens(async_client, tokens_out_of_body):
    resp = await async_client.post(
        "/api/v1/auth/refresh", headers=UNTRUSTED, json={"refreshToken": "fake-refresh-token"}
    )
    assert resp.status_code == 200
    assert resp.json()["accessToken"] and resp.json()["refreshToken"] == "fake-refresh-token"


@pytest.mark.asyncio
async def test_oauth_exchange_omits_tokens_for_a_trusted_origin(async_client, tokens_out_of_body):
    import json
    from unittest.mock import AsyncMock

    from app.dependencies import get_redis

    mock_redis = AsyncMock()
    mock_redis.getdel = AsyncMock(
        return_value=json.dumps({"accessToken": "access-x", "refreshToken": "refresh-x", "userId": "uid"})
    )
    app.dependency_overrides[get_redis] = lambda: mock_redis

    resp = await async_client.post("/api/v1/auth/oauth/exchange", headers=TRUSTED, json={"code": "handoff"})
    assert resp.status_code == 200
    assert resp.json() == {}
    assert "access-x" in (_set_cookie_header(resp, "access_token") or "")


@pytest.mark.asyncio
async def test_logout_revokes_session_for_bearer_token(async_client, register_user, auth_headers, override_get_db):
    await register_user()
    headers = await auth_headers()
    resp = await async_client.post("/api/v1/auth/logout", headers=headers)
    assert resp.status_code == 204
    override_get_db.admin.sign_out.assert_awaited_once_with(headers["Authorization"].split(" ", 1)[1], "local")


@pytest.mark.asyncio
async def test_logout_revokes_session_for_cookie_token(async_client, register_user, override_get_db):
    token = (await register_user()).json()["accessToken"]
    async_client.cookies.clear()
    async_client.cookies.set("access_token", token)
    resp = await async_client.post("/api/v1/auth/logout")
    assert resp.status_code == 204
    override_get_db.admin.sign_out.assert_awaited_once_with(token, "local")


@pytest.mark.asyncio
async def test_logout_still_succeeds_and_clears_cookies_when_revocation_fails(
    async_client, register_user, auth_headers, override_get_db
):
    await register_user()
    headers = await auth_headers()
    override_get_db.admin.sign_out.side_effect = RuntimeError("supabase down")
    resp = await async_client.post("/api/v1/auth/logout", headers=headers)
    assert resp.status_code == 204
    for name in ("refresh_token", "access_token"):
        assert _set_cookie_header(resp, name) is not None


def _spy_refresh(fake_auth):
    fake_auth.refresh_session = AsyncMock(side_effect=fake_auth.refresh_session)
    return fake_auth.refresh_session


def _assert_cookies_cleared(resp):
    for name in ("refresh_token", "access_token"):
        assert _set_cookie_header(resp, name) is not None


@pytest.mark.asyncio
async def test_logout_without_any_token_is_idempotent_and_makes_no_outbound_call(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    resp = await async_client.post("/api/v1/auth/logout")
    assert resp.status_code == 204
    _assert_cookies_cleared(resp)
    override_get_db.admin.sign_out.assert_not_awaited()
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_bearer_token_wins_over_access_cookie(async_client, override_get_db):
    async_client.cookies.set("access_token", "cookie-token")
    resp = await async_client.post("/api/v1/auth/logout", headers={"Authorization": "Bearer bearer-token", **TRUSTED})
    assert resp.status_code == 204
    override_get_db.admin.sign_out.assert_awaited_once_with("bearer-token", "local")


@pytest.mark.asyncio
async def test_logout_revocation_is_bounded_by_the_timeout(async_client, override_get_db, monkeypatch):
    async def hang(*_args):
        await asyncio.sleep(10)

    monkeypatch.setattr("app.services.auth_service._REVOKE_TIMEOUT_SECONDS", 0.05)
    override_get_db.admin.sign_out.side_effect = hang
    resp = await async_client.post("/api/v1/auth/logout", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 204
    _assert_cookies_cleared(resp)


@pytest.mark.asyncio
async def test_logout_with_only_refresh_cookie_refreshes_then_revokes(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    async_client.cookies.set("refresh_token", "fake-refresh-token")
    resp = await async_client.post("/api/v1/auth/logout", headers=TRUSTED)
    assert resp.status_code == 204
    _assert_cookies_cleared(resp)
    refresh.assert_awaited_once_with("fake-refresh-token")
    override_get_db.admin.sign_out.assert_awaited_once()
    fresh_token, scope = override_get_db.admin.sign_out.await_args.args
    assert fresh_token and scope == "local"


@pytest.mark.asyncio
async def test_logout_with_expired_access_token_falls_back_to_refresh(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    override_get_db.admin.sign_out.side_effect = [AuthApiError("jwt expired", 401, "bad_jwt"), None]
    async_client.cookies.set("access_token", "expired-token")
    async_client.cookies.set("refresh_token", "fake-refresh-token")
    resp = await async_client.post("/api/v1/auth/logout", headers=TRUSTED)
    assert resp.status_code == 204
    _assert_cookies_cleared(resp)
    refresh.assert_awaited_once_with("fake-refresh-token")
    calls = override_get_db.admin.sign_out.await_args_list
    assert calls[0].args[0] == "expired-token"
    assert calls[1].args[0] != "expired-token" and calls[1].args[1] == "local"


@pytest.mark.asyncio
async def test_logout_with_rejected_refresh_token_still_succeeds(async_client, override_get_db):
    async_client.cookies.set("refresh_token", "stale-refresh-token")
    resp = await async_client.post("/api/v1/auth/logout", headers=TRUSTED)
    assert resp.status_code == 204
    _assert_cookies_cleared(resp)
    override_get_db.admin.sign_out.assert_not_awaited()


@pytest.mark.asyncio
async def test_csrf_blocks_cross_origin_logout_with_only_a_refresh_cookie(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    async_client.cookies.set("refresh_token", "fake-refresh-token")
    resp = await async_client.post("/api/v1/auth/logout", headers=UNTRUSTED)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "CSRF_ORIGIN_MISMATCH"
    refresh.assert_not_awaited()
    override_get_db.admin.sign_out.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_failure_log_does_not_contain_the_token(async_client, override_get_db):
    override_get_db.admin.sign_out.side_effect = RuntimeError("secret-bearer-xyz rejected")
    with capture_logs() as logs:
        resp = await async_client.post("/api/v1/auth/logout", headers={"Authorization": "Bearer secret-bearer-xyz"})
    assert resp.status_code == 204
    assert any(e["event"] == "supabase_session_revoke_failed" for e in logs)
    assert "secret-bearer-xyz" not in repr(logs)


@pytest.mark.asyncio
async def test_logout_is_rate_limited_per_ip(async_client, override_get_db):
    for i in range(20):
        assert (await async_client.post("/api/v1/auth/logout")).status_code == 204, i
    assert (await async_client.post("/api/v1/auth/logout")).status_code == 429


LOGOUT = "/api/v1/auth/logout"


@pytest.mark.asyncio
async def test_logout_body_refresh_token_refreshes_then_revokes_without_cookies(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    resp = await async_client.post(LOGOUT, json={"refreshToken": "fake-refresh-token"})
    assert resp.status_code == 204
    _assert_cookies_cleared(resp)
    refresh.assert_awaited_once_with("fake-refresh-token")
    override_get_db.admin.sign_out.assert_awaited_once()
    fresh_token, scope = override_get_db.admin.sign_out.await_args.args
    assert fresh_token and scope == "local"


@pytest.mark.asyncio
async def test_logout_body_refresh_token_after_expired_bearer_falls_back(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    override_get_db.admin.sign_out.side_effect = [AuthApiError("jwt expired", 401, "bad_jwt"), None]
    resp = await async_client.post(
        LOGOUT, headers={"Authorization": "Bearer expired-token"}, json={"refreshToken": "fake-refresh-token"}
    )
    assert resp.status_code == 204
    refresh.assert_awaited_once_with("fake-refresh-token")
    calls = override_get_db.admin.sign_out.await_args_list
    assert calls[0].args[0] == "expired-token"
    assert calls[1].args[0] != "expired-token" and calls[1].args[1] == "local"


@pytest.mark.asyncio
async def test_logout_refresh_cookie_wins_over_body_token(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    async_client.cookies.set("refresh_token", "fake-refresh-token")
    resp = await async_client.post(LOGOUT, headers=TRUSTED, json={"refreshToken": "other-refresh-token"})
    assert resp.status_code == 204
    refresh.assert_awaited_once_with("fake-refresh-token")


@pytest.mark.asyncio
async def test_logout_without_body_is_unchanged(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    resp = await async_client.post(LOGOUT)
    assert resp.status_code == 204
    _assert_cookies_cleared(resp)
    refresh.assert_not_awaited()
    override_get_db.admin.sign_out.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs"),
    [
        {"content": b"", "headers": {"Content-Type": "application/json"}},
        {"content": b"{not json", "headers": {"Content-Type": "application/json"}},
        {"content": b"refreshToken=abc", "headers": {"Content-Type": "text/plain"}},
        {"content": b"refreshToken=abc", "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
        {"json": {"refreshToken": ""}},
        {"json": {"refreshToken": "   "}},
        {"json": {"refreshToken": None}},
        {"json": {"refreshToken": 123}},
        {"json": {}},
        {"json": ["refreshToken"]},
        {"json": None},
    ],
)
async def test_logout_blank_or_malformed_body_is_204_without_revocation(async_client, override_get_db, kwargs):
    refresh = _spy_refresh(override_get_db)
    resp = await async_client.post(LOGOUT, **kwargs)
    assert resp.status_code == 204
    _assert_cookies_cleared(resp)
    refresh.assert_not_awaited()
    override_get_db.admin.sign_out.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_origin_cookieless_body_token_only_revokes_its_own_session(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    resp = await async_client.post(LOGOUT, headers=UNTRUSTED, json={"refreshToken": "fake-refresh-token"})
    assert resp.status_code == 204
    refresh.assert_awaited_once_with("fake-refresh-token")


@pytest.mark.asyncio
async def test_logout_body_refresh_token_never_appears_in_logs(async_client, override_get_db):
    override_get_db.admin.sign_out.side_effect = RuntimeError("secret-body-refresh rejected")
    override_get_db.refresh_session = AsyncMock(side_effect=RuntimeError("secret-body-refresh rejected"))
    with capture_logs() as logs:
        resp = await async_client.post(LOGOUT, json={"refreshToken": "secret-body-refresh"})
    assert resp.status_code == 204
    assert "secret-body-refresh" not in repr(logs)


JSON_CT = {"Content-Type": "application/json"}


@pytest.mark.asyncio
async def test_logout_deeply_nested_body_is_204_and_clears_cookies(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    resp = await async_client.post(LOGOUT, content="[" * 200000, headers=JSON_CT)
    assert resp.status_code == 204
    _assert_cookies_cleared(resp)
    refresh.assert_not_awaited()
    override_get_db.admin.sign_out.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_oversized_body_is_ignored_but_still_clears_cookies(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    body = json.dumps({"pad": "x" * 4096, "refreshToken": "fake-refresh-token"})
    resp = await async_client.post(LOGOUT, content=body, headers=JSON_CT)
    assert resp.status_code == 204
    _assert_cookies_cleared(resp)
    refresh.assert_not_awaited()
    override_get_db.admin.sign_out.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_oversized_chunked_body_is_ignored(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)

    async def chunks():
        yield b'{"pad": "' + b"x" * 4096
        yield b'", "refreshToken": "fake-refresh-token"}'

    resp = await async_client.post(LOGOUT, content=chunks(), headers=JSON_CT)
    assert resp.status_code == 204
    _assert_cookies_cleared(resp)
    refresh.assert_not_awaited()
    override_get_db.admin.sign_out.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_body_at_the_cap_still_works(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    prefix, suffix = '{"refreshToken": "fake-refresh-token", "pad": "', '"}'
    body = prefix + "x" * (2048 - len(prefix) - len(suffix)) + suffix
    assert len(body) == 2048
    resp = await async_client.post(LOGOUT, content=body, headers=JSON_CT)
    assert resp.status_code == 204
    refresh.assert_awaited_once_with("fake-refresh-token")


@pytest.mark.asyncio
async def test_logout_cookie_bearer_and_body_tokens_together_use_the_cookie(async_client, override_get_db):
    refresh = _spy_refresh(override_get_db)
    override_get_db.admin.sign_out.side_effect = [AuthApiError("jwt expired", 401, "bad_jwt"), None]
    async_client.cookies.set("refresh_token", "cookie-refresh-token")
    resp = await async_client.post(
        LOGOUT,
        headers={**TRUSTED, "Authorization": "Bearer expired-token"},
        json={"refreshToken": "body-refresh-token"},
    )
    assert resp.status_code == 204
    refresh.assert_awaited_once_with("cookie-refresh-token")


@pytest.mark.asyncio
async def test_openapi_documents_the_optional_logout_body(async_client):
    spec = (await async_client.get("/openapi.json")).json()
    body = spec["paths"][LOGOUT]["post"]["requestBody"]
    assert body["required"] is False
    assert "refreshToken" in body["content"]["application/json"]["schema"]["properties"]
