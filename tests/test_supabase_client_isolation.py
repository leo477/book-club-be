from unittest.mock import AsyncMock

import httpx
import pytest
from supabase_auth.types import AuthResponse, Session, User

from app.services import auth_service
from app.services.auth_service import get_supabase_client, supabase_refresh, supabase_revoke_session
from tests.conftest import _make_test_settings

USER_TOKEN = "user-access-token"
ANON = "Bearer " + _make_test_settings().SUPABASE_ANON_KEY


def _auth_response(token: str = USER_TOKEN) -> AuthResponse:
    user = User(
        id="00000000-0000-0000-0000-000000000001",
        app_metadata={},
        user_metadata={},
        aud="authenticated",
        created_at="2026-01-01T00:00:00Z",
    )
    session = Session(access_token=token, refresh_token="r", expires_in=3600, token_type="bearer", user=user)  # noqa: S106
    return AuthResponse(user=user, session=session)


@pytest.fixture
async def shared_client():
    auth_service._supabase_clients.clear()
    client = await get_supabase_client(_make_test_settings())
    yield client
    auth_service._supabase_clients.clear()


def _assert_anon(client) -> None:
    assert client.options.headers["Authorization"] == ANON
    assert client.auth._headers["Authorization"] == ANON
    assert client.auth.admin._headers["Authorization"] == ANON
    assert client.storage.session.headers["Authorization"] == ANON


async def test_created_client_is_anon(shared_client):
    _assert_anon(shared_client)


@pytest.mark.parametrize("event", ["SIGNED_IN", "TOKEN_REFRESHED", "SIGNED_OUT"])
async def test_auth_events_do_not_touch_shared_headers(shared_client, event):
    shared_client.auth._notify_all_subscribers(event, _auth_response().session)
    _assert_anon(shared_client)


async def test_refresh_flow_keeps_client_anon(shared_client, monkeypatch):
    monkeypatch.setattr(shared_client.auth, "_refresh_access_token", AsyncMock(return_value=_auth_response()))
    result = await supabase_refresh(shared_client, "old-refresh")
    assert result.session.access_token == USER_TOKEN
    _assert_anon(shared_client)


async def test_login_flow_keeps_client_anon(shared_client, monkeypatch):
    body = _auth_response().session.model_dump(mode="json")
    monkeypatch.setattr(shared_client.auth, "_request", AsyncMock(return_value=httpx.Response(200, json=body)))
    await shared_client.auth.sign_in_with_password({"email": "a@b.co", "password": "x"})
    _assert_anon(shared_client)


async def test_logout_refresh_fallback_keeps_client_anon(shared_client, monkeypatch):
    monkeypatch.setattr(shared_client.auth, "_refresh_access_token", AsyncMock(return_value=_auth_response()))
    sign_out = AsyncMock()
    monkeypatch.setattr(shared_client.auth.admin, "sign_out", sign_out)
    await supabase_revoke_session(_make_test_settings(), None, "old-refresh")
    sign_out.assert_awaited_once_with(USER_TOKEN, "local")
    _assert_anon(shared_client)
