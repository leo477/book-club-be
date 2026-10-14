import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from app.exceptions import AppError
from app.routers.clubs import join_club
from app.services.club_service import request_join_club_service


async def _organizer_with_club(async_client, register_user, auth_headers, email, club_name):
    await register_user(email=email, role="user")
    headers = await auth_headers(email=email)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": club_name, "description": "Desc", "city": "Kyiv"}
    )
    return headers, club_resp.json()["id"]


@pytest.mark.asyncio
async def test_join_club_statement_timeout_returns_503(async_client, register_user, auth_headers):
    """C1: PostgreSQL statement_timeout fires as OperationalError → 503 JOIN_DB_ERROR."""
    _, club_id = await _organizer_with_club(
        async_client, register_user, auth_headers, email="jto_org@example.com", club_name="JTOClub"
    )
    await register_user(email="jto_user@example.com")
    user_headers = await auth_headers(email="jto_user@example.com")

    async def _timeout(*_args, **_kwargs):
        raise OperationalError("statement", {}, Exception("canceling statement due to statement timeout"))

    with patch("app.routers.clubs.request_join_club_service", side_effect=_timeout):
        resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)

    assert resp.status_code == 503
    body = resp.json()
    assert body.get("detail", {}).get("code") == "JOIN_DB_ERROR"


@pytest.mark.asyncio
async def test_join_club_db_error_returns_503(async_client, register_user, auth_headers):
    _, club_id = await _organizer_with_club(
        async_client, register_user, auth_headers, email="jdb_org@example.com", club_name="JDBClub"
    )
    await register_user(email="jdb_user@example.com")
    user_headers = await auth_headers(email="jdb_user@example.com")

    async def _boom(*_args, **_kwargs):
        raise SQLAlchemyError("boom")

    with patch("app.routers.clubs.request_join_club_service", side_effect=_boom):
        resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)

    assert resp.status_code == 503
    body = resp.json()
    assert body.get("detail", {}).get("code") == "JOIN_DB_ERROR"


@pytest.mark.asyncio
async def test_request_join_integrity_error_maps_to_already_requested():
    """TOCTOU race: SELECT shows no pending request, but INSERT collides on the unique constraint."""
    club_id = uuid.uuid4()
    user = MagicMock()
    user.id = uuid.uuid4()

    db = MagicMock()
    fake_conn = MagicMock()
    fake_conn.dialect.name = "postgresql"
    db.connection = AsyncMock(return_value=fake_conn)
    # execute calls: SET LOCAL statement_timeout, ban check, member check, existing-request check
    set_timeout_result = MagicMock()
    ban_result = MagicMock()
    ban_result.scalar_one_or_none = MagicMock(return_value=None)
    member_result = MagicMock()
    member_result.scalar_one_or_none = MagicMock(return_value=None)
    request_result = MagicMock()
    request_result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(side_effect=[set_timeout_result, ban_result, member_result, request_result])
    db.add = MagicMock()
    db.commit = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))
    db.rollback = AsyncMock()

    with patch("app.services.club_service.get_club_or_404", new=AsyncMock(return_value=MagicMock())):
        result = await request_join_club_service(club_id, user, db)

    assert result == "already_requested"
    db.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_join_club_db_error_rollback_failure_still_returns_503():
    """Rollback after DB error itself raises — handler must still return 503."""
    club_id = uuid.uuid4()
    user = MagicMock()
    user.id = uuid.uuid4()
    db = MagicMock()
    db.rollback = AsyncMock(side_effect=SQLAlchemyError("rollback failed"))

    async def _boom(*_args, **_kwargs):
        raise SQLAlchemyError("boom")

    with patch("app.routers.clubs.request_join_club_service", side_effect=_boom):
        with pytest.raises(AppError) as exc_info:
            await join_club(club_id, user, db)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "JOIN_DB_ERROR"
    db.rollback.assert_awaited()
