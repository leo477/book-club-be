import asyncio
from unittest.mock import patch

import pytest
from sqlalchemy.exc import SQLAlchemyError


async def _organizer_with_club(async_client, register_user, auth_headers, email, club_name):
    await register_user(email=email, role="user")
    headers = await auth_headers(email=email)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": club_name, "description": "Desc", "city": "Kyiv"}
    )
    return headers, club_resp.json()["id"]


@pytest.mark.asyncio
async def test_join_club_timeout_returns_503(async_client, register_user, auth_headers):
    _, club_id = await _organizer_with_club(
        async_client, register_user, auth_headers, email="jto_org@example.com", club_name="JTOClub"
    )
    await register_user(email="jto_user@example.com")
    user_headers = await auth_headers(email="jto_user@example.com")

    async def _hang(*_args, **_kwargs):
        raise TimeoutError

    with patch("app.routers.clubs._do_join_club", side_effect=_hang):
        resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)

    assert resp.status_code == 503
    body = resp.json()
    assert body.get("code") == "JOIN_TIMEOUT" or body.get("detail", {}).get("code") == "JOIN_TIMEOUT"


@pytest.mark.asyncio
async def test_join_club_db_error_returns_503(async_client, register_user, auth_headers):
    _, club_id = await _organizer_with_club(
        async_client, register_user, auth_headers, email="jdb_org@example.com", club_name="JDBClub"
    )
    await register_user(email="jdb_user@example.com")
    user_headers = await auth_headers(email="jdb_user@example.com")

    async def _boom(*_args, **_kwargs):
        raise SQLAlchemyError("boom")

    with patch("app.routers.clubs._do_join_club", side_effect=_boom):
        resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)

    assert resp.status_code == 503
    body = resp.json()
    assert body.get("code") == "JOIN_DB_ERROR" or body.get("detail", {}).get("code") == "JOIN_DB_ERROR"


@pytest.mark.asyncio
async def test_join_club_real_timeout_path(async_client, register_user, auth_headers, monkeypatch):
    _, club_id = await _organizer_with_club(
        async_client, register_user, auth_headers, email="rto_org@example.com", club_name="RTOClub"
    )
    await register_user(email="rto_user@example.com")
    user_headers = await auth_headers(email="rto_user@example.com")

    async def _slow(*_args, **_kwargs):
        await asyncio.sleep(5)
        return 0

    monkeypatch.setattr("app.routers.clubs._JOIN_DB_TIMEOUT_SECONDS", 0.05)
    with patch("app.routers.clubs._do_join_club", side_effect=_slow):
        resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)

    assert resp.status_code == 503
