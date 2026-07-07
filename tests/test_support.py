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


@pytest.mark.asyncio
async def test_like_complaint_returns_201_and_increments_count(async_client, auth_headers):
    author_headers = await auth_headers(email="like-author@example.com")
    create = await async_client.post(
        "/api/v1/support",
        headers=author_headers,
        json={"type": "complaint", "title": "Bad thing", "body": "Something went wrong"},
    )
    assert create.status_code == 201, create.text
    submission_id = create.json()["id"]
    assert create.json()["likeCount"] == 0
    assert create.json()["likedByMe"] is False

    liker_headers = await auth_headers(email="liker@example.com")
    resp = await async_client.post(f"/api/v1/support/{submission_id}/like", headers=liker_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["likeCount"] == 1
    assert body["likedByMe"] is True


@pytest.mark.asyncio
async def test_like_again_returns_409(async_client, auth_headers):
    author_headers = await auth_headers(email="like-author2@example.com")
    create = await async_client.post(
        "/api/v1/support",
        headers=author_headers,
        json={"type": "comment", "title": "Nice", "body": "Great app"},
    )
    assert create.status_code == 201, create.text
    submission_id = create.json()["id"]

    liker_headers = await auth_headers(email="liker2@example.com")
    first = await async_client.post(f"/api/v1/support/{submission_id}/like", headers=liker_headers)
    assert first.status_code == 201, first.text

    second = await async_client.post(f"/api/v1/support/{submission_id}/like", headers=liker_headers)
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "ALREADY_LIKED"


@pytest.mark.asyncio
async def test_unlike_removes_like_and_is_idempotent(async_client, auth_headers):
    author_headers = await auth_headers(email="like-author3@example.com")
    create = await async_client.post(
        "/api/v1/support",
        headers=author_headers,
        json={"type": "comment", "title": "Nice", "body": "Great app"},
    )
    assert create.status_code == 201, create.text
    submission_id = create.json()["id"]

    liker_headers = await auth_headers(email="liker3@example.com")
    like_resp = await async_client.post(f"/api/v1/support/{submission_id}/like", headers=liker_headers)
    assert like_resp.status_code == 201, like_resp.text

    unlike_resp = await async_client.delete(f"/api/v1/support/{submission_id}/like", headers=liker_headers)
    assert unlike_resp.status_code == 204, unlike_resp.text

    # Idempotent: unliking again should still return 204, not error.
    unlike_again_resp = await async_client.delete(f"/api/v1/support/{submission_id}/like", headers=liker_headers)
    assert unlike_again_resp.status_code == 204, unlike_again_resp.text


@pytest.mark.asyncio
async def test_like_suggestion_returns_400(async_client, auth_headers):
    author_headers = await auth_headers(email="suggestion-author@example.com")
    create = await async_client.post(
        "/api/v1/support",
        headers=author_headers,
        json={"type": "suggestion", "title": "Add dark mode", "body": "Please add dark mode"},
    )
    assert create.status_code == 201, create.text
    submission_id = create.json()["id"]

    liker_headers = await auth_headers(email="liker4@example.com")
    resp = await async_client.post(f"/api/v1/support/{submission_id}/like", headers=liker_headers)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "CANNOT_LIKE_SUGGESTION"


@pytest.mark.asyncio
async def test_like_without_auth_returns_401(async_client, auth_headers):
    author_headers = await auth_headers(email="like-author4@example.com")
    create = await async_client.post(
        "/api/v1/support",
        headers=author_headers,
        json={"type": "comment", "title": "Nice", "body": "Great app"},
    )
    assert create.status_code == 201, create.text
    submission_id = create.json()["id"]

    # Clear cookies: the author login above left an access_token cookie on this shared
    # client, which would otherwise authenticate this "anonymous" request.
    async_client.cookies.clear()
    resp = await async_client.post(f"/api/v1/support/{submission_id}/like")
    assert resp.status_code == 401, resp.text
