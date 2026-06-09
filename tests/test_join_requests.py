from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


async def _organizer_with_club(async_client, register_user, auth_headers, email, club_name):
    await register_user(email=email, role="user")
    headers = await auth_headers(email=email)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": club_name, "description": "Desc", "city": "Kyiv"}
    )
    return headers, club_resp.json()["id"]


async def _member_user(async_client, register_user, auth_headers, email):
    await register_user(email=email)
    headers = await auth_headers(email=email)
    me = await async_client.get("/api/v1/users/me", headers=headers)
    return headers, me.json()["id"]


@pytest.mark.asyncio
async def test_manual_join_creates_pending_request(async_client, register_user, auth_headers):
    org_headers, club_id = await _organizer_with_club(
        async_client, register_user, auth_headers, "jr_org1@example.com", "JRClub1"
    )
    user_headers, _ = await _member_user(async_client, register_user, auth_headers, "jr_user1@example.com")

    resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    # Not a member yet
    members = await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=org_headers)
    roles = [m["role"] for m in members.json()]
    assert roles == ["organizer"]

    # my-membership reflects the pending request
    mine = await async_client.get(f"/api/v1/clubs/{club_id}/my-membership", headers=user_headers)
    assert mine.status_code == 200
    data = mine.json()
    assert data["isMember"] is False
    assert data["joinRequestStatus"] == "pending"


@pytest.mark.asyncio
async def test_duplicate_join_returns_already_requested(async_client, register_user, auth_headers):
    _, club_id = await _organizer_with_club(async_client, register_user, auth_headers, "jr_org2@example.com", "JRClub2")
    user_headers, _ = await _member_user(async_client, register_user, auth_headers, "jr_user2@example.com")

    first = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)
    assert first.json()["status"] == "pending"

    second = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)
    assert second.status_code == 200
    assert second.json()["status"] == "already_requested"


@pytest.mark.asyncio
async def test_join_as_member_returns_409(async_client, register_user, auth_headers, make_member):
    _, club_id = await _organizer_with_club(async_client, register_user, auth_headers, "jr_org3@example.com", "JRClub3")
    user_headers, _ = await _member_user(async_client, register_user, auth_headers, "jr_user3@example.com")
    await make_member(club_id, user_headers)

    resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ALREADY_MEMBER"


@pytest.mark.asyncio
async def test_join_when_banned_returns_403(async_client, register_user, auth_headers):
    org_headers, club_id = await _organizer_with_club(
        async_client, register_user, auth_headers, "jr_org4@example.com", "JRClub4"
    )
    user_headers, user_id = await _member_user(async_client, register_user, auth_headers, "jr_user4@example.com")

    ban = await async_client.post(
        f"/api/v1/clubs/{club_id}/members/{user_id}/ban",
        headers=org_headers,
        json={"duration": "permanent"},
    )
    assert ban.status_code == 201

    resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "CLUB_BANNED"


@pytest.mark.asyncio
async def test_organizer_lists_join_requests(async_client, register_user, auth_headers):
    org_headers, club_id = await _organizer_with_club(
        async_client, register_user, auth_headers, "jr_org5@example.com", "JRClub5"
    )
    user_headers, user_id = await _member_user(async_client, register_user, auth_headers, "jr_user5@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/join-requests", headers=org_headers)
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "1"
    requests = resp.json()
    assert len(requests) == 1
    assert requests[0]["userId"] == user_id
    assert requests[0]["status"] == "pending"
    assert requests[0]["source"] == "manual"


@pytest.mark.asyncio
async def test_non_organizer_cannot_list_join_requests(async_client, register_user, auth_headers):
    _, club_id = await _organizer_with_club(async_client, register_user, auth_headers, "jr_org6@example.com", "JRClub6")
    user_headers, _ = await _member_user(async_client, register_user, auth_headers, "jr_user6@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/join-requests", headers=user_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_approve_join_request(async_client, register_user, auth_headers):
    org_headers, club_id = await _organizer_with_club(
        async_client, register_user, auth_headers, "jr_org7@example.com", "JRClub7"
    )
    user_headers, user_id = await _member_user(async_client, register_user, auth_headers, "jr_user7@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)

    approve = await async_client.post(f"/api/v1/clubs/{club_id}/join-requests/{user_id}/approve", headers=org_headers)
    assert approve.status_code == 200
    assert approve.json()["memberCount"] == 2

    # User is now a member
    mine = await async_client.get(f"/api/v1/clubs/{club_id}/my-membership", headers=user_headers)
    data = mine.json()
    assert data["isMember"] is True
    assert data["role"] == "member"

    # No longer pending; second approve → 404
    second = await async_client.post(f"/api/v1/clubs/{club_id}/join-requests/{user_id}/approve", headers=org_headers)
    assert second.status_code == 404
    assert second.json()["detail"]["code"] == "JOIN_REQUEST_NOT_FOUND"


@pytest.mark.asyncio
async def test_reject_join_request_then_rejoin(async_client, register_user, auth_headers):
    org_headers, club_id = await _organizer_with_club(
        async_client, register_user, auth_headers, "jr_org8@example.com", "JRClub8"
    )
    user_headers, user_id = await _member_user(async_client, register_user, auth_headers, "jr_user8@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)

    reject = await async_client.post(f"/api/v1/clubs/{club_id}/join-requests/{user_id}/reject", headers=org_headers)
    assert reject.status_code == 204

    mine = await async_client.get(f"/api/v1/clubs/{club_id}/my-membership", headers=user_headers)
    data = mine.json()
    assert data["isMember"] is False
    assert data["joinRequestStatus"] == "rejected"

    # Rejecting again → 404 (no pending request)
    reject_again = await async_client.post(
        f"/api/v1/clubs/{club_id}/join-requests/{user_id}/reject", headers=org_headers
    )
    assert reject_again.status_code == 404

    # Re-join flips the rejected request back to pending
    rejoin = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)
    assert rejoin.status_code == 200
    assert rejoin.json()["status"] == "pending"

    mine_again = await async_client.get(f"/api/v1/clubs/{club_id}/my-membership", headers=user_headers)
    assert mine_again.json()["joinRequestStatus"] == "pending"


@pytest.mark.asyncio
async def test_attend_event_as_non_member_creates_pending_request(async_client, register_user, auth_headers):
    org_headers, club_id = await _organizer_with_club(
        async_client, register_user, auth_headers, "jr_org9@example.com", "JRClub9"
    )
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    event_resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/events",
        headers=org_headers,
        json={"title": "JR Event", "date": future, "city": "Kyiv", "description": "Read"},
    )
    assert event_resp.status_code == 201
    event_id = event_resp.json()["id"]

    user_headers, _ = await _member_user(async_client, register_user, auth_headers, "jr_user9@example.com")

    attend = await async_client.post(f"/api/v1/events/{event_id}/attend", headers=user_headers)
    assert attend.status_code == 201
    data = attend.json()
    assert data["attendeeCount"] >= 1
    assert data["joinRequestStatus"] == "pending"

    # A pending (source=event) request now exists, visible to the organizer
    requests = await async_client.get(f"/api/v1/clubs/{club_id}/join-requests", headers=org_headers)
    assert requests.headers["X-Total-Count"] == "1"
    assert requests.json()[0]["source"] == "event"


@pytest.mark.asyncio
async def test_attend_event_as_member_returns_member(async_client, register_user, auth_headers, make_member):
    org_headers, club_id = await _organizer_with_club(
        async_client, register_user, auth_headers, "jr_org10@example.com", "JRClub10"
    )
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    event_resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/events",
        headers=org_headers,
        json={"title": "JR Event 2", "date": future, "city": "Kyiv", "description": "Read"},
    )
    assert event_resp.status_code == 201
    event_id = event_resp.json()["id"]

    user_headers, _ = await _member_user(async_client, register_user, auth_headers, "jr_user10@example.com")
    await make_member(club_id, user_headers)

    attend = await async_client.post(f"/api/v1/events/{event_id}/attend", headers=user_headers)
    assert attend.status_code == 201
    assert attend.json()["joinRequestStatus"] == "member"

    # No join request was created for an existing member
    requests = await async_client.get(f"/api/v1/clubs/{club_id}/join-requests", headers=org_headers)
    assert requests.headers["X-Total-Count"] == "0"
