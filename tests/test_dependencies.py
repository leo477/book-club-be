"""Tests for dependency edge-cases not hit by normal API flows."""

from __future__ import annotations

import time
import uuid

import jwt as pyjwt
import pytest

_JWT_SECRET = "test-supabase-jwt-secret-32characters!!"


def _token(**payload_overrides) -> str:
    payload = {"sub": str(uuid.uuid4()), "exp": int(time.time()) + 3600}
    payload.update(payload_overrides)
    return pyjwt.encode(payload, _JWT_SECRET, algorithm="HS256")


@pytest.mark.asyncio
async def test_get_current_user_token_missing_sub(async_client):
    """JWT with empty 'sub' → 401 INVALID_TOKEN (dependencies.py line 44)."""
    token = _token(sub="")
    resp = await async_client.get("/api/v1/clubs/my", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_user_not_found(async_client):
    """JWT with valid UUID sub but no matching user in DB → 401 (dependencies.py line 49)."""
    token = _token(sub=str(uuid.uuid4()))
    resp = await async_client.get("/api/v1/clubs/my", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
