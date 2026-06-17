"""Unit tests for event_service helpers — exercises branch logic without HTTP/DB."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import AppError
from app.services.event_service import _resolve_join_request_status

_JOIN_SERVICE = "app.services.club_service.request_join_club_service"


def _ctx() -> tuple[SimpleNamespace, SimpleNamespace]:
    event = SimpleNamespace(club_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())
    return event, user


@pytest.mark.asyncio
async def test_resolve_join_status_member_when_already_member():
    repo = SimpleNamespace(get_membership_id=AsyncMock(return_value=uuid.uuid4()))
    event, user = _ctx()

    result = await _resolve_join_request_status(repo, event, user, db=AsyncMock())

    assert result == "member"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("join_result", "expected"),
    [
        ("pending", "pending"),
        ("already_requested", "pending"),
        ("member", "none"),
    ],
)
async def test_resolve_join_status_maps_request_result(join_result: str, expected: str):
    repo = SimpleNamespace(get_membership_id=AsyncMock(return_value=None))
    event, user = _ctx()

    with patch(_JOIN_SERVICE, AsyncMock(return_value=join_result)):
        result = await _resolve_join_request_status(repo, event, user, db=AsyncMock())

    assert result == expected


@pytest.mark.asyncio
async def test_resolve_join_status_banned_returns_none():
    repo = SimpleNamespace(get_membership_id=AsyncMock(return_value=None))
    event, user = _ctx()
    banned = AsyncMock(side_effect=AppError(403, "Banned from club", "CLUB_BANNED"))

    with patch(_JOIN_SERVICE, banned):
        result = await _resolve_join_request_status(repo, event, user, db=AsyncMock())

    assert result == "none"


@pytest.mark.asyncio
async def test_resolve_join_status_reraises_other_apperror():
    repo = SimpleNamespace(get_membership_id=AsyncMock(return_value=None))
    event, user = _ctx()
    other = AsyncMock(side_effect=AppError(404, "Club not found", "CLUB_NOT_FOUND"))

    with patch(_JOIN_SERVICE, other), pytest.raises(AppError) as exc_info:
        await _resolve_join_request_status(repo, event, user, db=AsyncMock())

    assert exc_info.value.detail["code"] == "CLUB_NOT_FOUND"
