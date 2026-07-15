import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.push_token import PushToken


async def _get_token_row(test_engine, token: str) -> PushToken | None:
    TestSessionLocal = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    async with TestSessionLocal() as session:
        result = await session.execute(select(PushToken).where(PushToken.token == token))
        return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_register_token_inserts_row(async_client, register_user, auth_headers, test_engine):
    headers = await auth_headers()
    resp = await async_client.post(
        "/api/v1/notifications/register-token",
        headers=headers,
        json={"token": "ExponentPushToken[aaa]", "platform": "ios"},
    )
    assert resp.status_code == 204

    row = await _get_token_row(test_engine, "ExponentPushToken[aaa]")
    assert row is not None
    assert row.platform == "ios"


@pytest.mark.asyncio
async def test_register_token_reassigns_to_new_user(async_client, register_user, auth_headers, test_engine):
    headers_a = await auth_headers(email="user-a@example.com")
    resp = await async_client.post(
        "/api/v1/notifications/register-token",
        headers=headers_a,
        json={"token": "ExponentPushToken[shared]", "platform": "ios"},
    )
    assert resp.status_code == 204

    me_a = await async_client.get("/api/v1/users/me", headers=headers_a)
    user_a_id = me_a.json()["id"]

    row = await _get_token_row(test_engine, "ExponentPushToken[shared]")
    assert row is not None
    assert str(row.user_id) == user_a_id

    headers_b = await auth_headers(email="user-b@example.com")
    resp = await async_client.post(
        "/api/v1/notifications/register-token",
        headers=headers_b,
        json={"token": "ExponentPushToken[shared]", "platform": "android"},
    )
    assert resp.status_code == 204

    me_b = await async_client.get("/api/v1/users/me", headers=headers_b)
    user_b_id = me_b.json()["id"]

    row = await _get_token_row(test_engine, "ExponentPushToken[shared]")
    assert row is not None
    assert str(row.user_id) == user_b_id
    assert row.platform == "android"
    assert user_a_id != user_b_id


@pytest.mark.asyncio
async def test_unregister_token_deletes_row(async_client, register_user, auth_headers, test_engine):
    headers = await auth_headers()
    await async_client.post(
        "/api/v1/notifications/register-token",
        headers=headers,
        json={"token": "ExponentPushToken[bbb]", "platform": "ios"},
    )

    resp = await async_client.request(
        "DELETE",
        "/api/v1/notifications/register-token",
        headers=headers,
        json={"token": "ExponentPushToken[bbb]"},
    )
    assert resp.status_code == 204

    row = await _get_token_row(test_engine, "ExponentPushToken[bbb]")
    assert row is None


@pytest.mark.asyncio
async def test_unregister_nonexistent_token_is_idempotent(async_client, register_user, auth_headers):
    headers = await auth_headers()
    resp = await async_client.request(
        "DELETE",
        "/api/v1/notifications/register-token",
        headers=headers,
        json={"token": "ExponentPushToken[does-not-exist]"},
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_register_token_requires_auth(async_client):
    resp = await async_client.post(
        "/api/v1/notifications/register-token",
        json={"token": "ExponentPushToken[ccc]", "platform": "ios"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unregister_token_requires_auth(async_client):
    resp = await async_client.request(
        "DELETE",
        "/api/v1/notifications/register-token",
        json={"token": "ExponentPushToken[ccc]"},
    )
    assert resp.status_code == 401
