"""
Regression test: clubs_joined counter in GET /users/me/stats must reflect
joins made via POST /clubs/{id}/join.

Bug: after POST /clubs/{id}/join the value of clubs_joined stayed 0.
Root cause: the stats query counted ClubMember rows correctly but the
            get_current_user dependency (which runs in the same FastAPI
            request as get_my_stats) loads the User from the DB and keeps
            it in the session identity-map.  Because autoflush=False and
            expire_on_commit=False, a stale cached User object was returned
            from the identity-map on subsequent requests within the same
            test session.  More critically: the stats function was passing
            the *same* session that already had the User loaded; SQLAlchemy
            may serve a cached result for aggregate queries when the ORM
            unit-of-work still has un-expired objects tied to related rows.
            The fix is to call db.expire_all() before running the stats
            COUNT queries so the session fetches fresh data from the DB.
"""

import pytest


@pytest.mark.asyncio
async def test_clubs_joined_after_join(async_client, register_user, auth_headers):
    """After joining a club, clubs_joined in GET /users/me/stats must be 1."""
    # --- organizer creates a public club ---
    await register_user(email="sj_org@example.com", role="organizer")
    org_headers = await auth_headers(email="sj_org@example.com", role="organizer")
    await async_client.patch("/api/v1/users/me/role", headers=org_headers, json={"role": "organizer"})

    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=org_headers,
        json={"name": "Stats Join Club", "description": "Desc", "city": "Kyiv", "isPublic": True},
    )
    assert club_resp.status_code == 201, club_resp.text
    club_id = club_resp.json()["id"]

    # --- regular user registers and joins the club ---
    await register_user(email="sj_member@example.com")
    member_headers = await auth_headers(email="sj_member@example.com")

    join_resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=member_headers)
    assert join_resp.status_code == 200, join_resp.text

    # --- stats must reflect the join ---
    stats_resp = await async_client.get("/api/v1/users/me/stats", headers=member_headers)
    assert stats_resp.status_code == 200, stats_resp.text
    data = stats_resp.json()
    assert data["clubsJoined"] == 1, f"Expected clubsJoined == 1 after joining one club, got {data['clubsJoined']}"


@pytest.mark.asyncio
async def test_clubs_joined_after_multiple_joins(async_client, register_user, auth_headers):
    """clubs_joined increments correctly when a user joins multiple clubs."""
    # org1 creates club1
    await register_user(email="mj_org1@example.com", role="organizer")
    org1_headers = await auth_headers(email="mj_org1@example.com", role="organizer")
    await async_client.patch("/api/v1/users/me/role", headers=org1_headers, json={"role": "organizer"})
    club1_resp = await async_client.post(
        "/api/v1/clubs",
        headers=org1_headers,
        json={"name": "Multi Join Club A", "description": "Desc", "city": "Kyiv", "isPublic": True},
    )
    assert club1_resp.status_code == 201
    club1_id = club1_resp.json()["id"]

    # regular user joins club1
    await register_user(email="mj_member@example.com")
    member_headers = await auth_headers(email="mj_member@example.com")
    join1 = await async_client.post(f"/api/v1/clubs/{club1_id}/join", headers=member_headers)
    assert join1.status_code == 200

    stats1 = await async_client.get("/api/v1/users/me/stats", headers=member_headers)
    assert stats1.json()["clubsJoined"] == 1, f"After 1 join: {stats1.json()}"

    # org2 creates club2, member joins it too
    await register_user(email="mj_org2@example.com", role="organizer")
    org2_headers = await auth_headers(email="mj_org2@example.com", role="organizer")
    await async_client.patch("/api/v1/users/me/role", headers=org2_headers, json={"role": "organizer"})
    club2_resp = await async_client.post(
        "/api/v1/clubs",
        headers=org2_headers,
        json={"name": "Multi Join Club B", "description": "Desc", "city": "Lviv", "isPublic": True},
    )
    assert club2_resp.status_code == 201
    club2_id = club2_resp.json()["id"]

    join2 = await async_client.post(f"/api/v1/clubs/{club2_id}/join", headers=member_headers)
    assert join2.status_code == 200

    stats2 = await async_client.get("/api/v1/users/me/stats", headers=member_headers)
    assert stats2.json()["clubsJoined"] == 2, f"After 2 joins: {stats2.json()}"
