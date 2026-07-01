"""Privacy: complaint authorId is masked for non-admins on GET /api/v1/support.

Complaints must hide the author identity (authorId == None) from non-admin
requesters, while admins still see it. Comments and suggestions keep authorId
for everyone.
"""

import uuid as _uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import User


async def _promote_to_admin(test_engine, async_client, headers) -> None:
    """Registration only allows user/organizer, so set role=admin directly."""
    me = await async_client.get("/api/v1/users/me", headers=headers)
    user_id = _uuid.UUID(me.json()["id"])
    SessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        user.role = "admin"
        await session.commit()


@pytest.mark.asyncio
async def test_complaint_author_hidden_for_non_admin(async_client, auth_headers):
    author_headers = await auth_headers(email="complainer@example.com")
    create = await async_client.post(
        "/api/v1/support",
        headers=author_headers,
        json={"type": "complaint", "title": "Bad thing", "body": "Something went wrong"},
    )
    assert create.status_code == 201, create.text

    viewer_headers = await auth_headers(email="viewer@example.com")
    resp = await async_client.get("/api/v1/support", headers=viewer_headers)
    assert resp.status_code == 200, resp.text
    items = [i for i in resp.json() if i["type"] == "complaint"]
    assert items, "complaint should still be listed"
    assert all(i["authorId"] is None for i in items)


@pytest.mark.asyncio
async def test_complaint_author_visible_for_admin(async_client, auth_headers, test_engine):
    author_headers = await auth_headers(email="complainer2@example.com")
    create = await async_client.post(
        "/api/v1/support",
        headers=author_headers,
        json={"type": "complaint", "title": "Bad thing", "body": "Something went wrong"},
    )
    assert create.status_code == 201, create.text

    admin_headers = await auth_headers(email="admin@example.com")
    await _promote_to_admin(test_engine, async_client, admin_headers)

    resp = await async_client.get("/api/v1/support", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    items = [i for i in resp.json() if i["type"] == "complaint"]
    assert items
    assert all(i["authorId"] is not None for i in items)


@pytest.mark.asyncio
async def test_comment_author_visible_for_non_admin(async_client, auth_headers):
    author_headers = await auth_headers(email="commenter@example.com")
    create = await async_client.post(
        "/api/v1/support",
        headers=author_headers,
        json={"type": "comment", "title": "Nice", "body": "Great app"},
    )
    assert create.status_code == 201, create.text

    viewer_headers = await auth_headers(email="viewer2@example.com")
    resp = await async_client.get("/api/v1/support", headers=viewer_headers)
    assert resp.status_code == 200, resp.text
    items = [i for i in resp.json() if i["type"] == "comment"]
    assert items
    assert all(i["authorId"] is not None for i in items)
