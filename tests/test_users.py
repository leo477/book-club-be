import pytest


@pytest.mark.asyncio
async def test_get_stats_empty(async_client, register_user, auth_headers):
    await register_user(email="stats_fresh@example.com")
    headers = await auth_headers(email="stats_fresh@example.com")
    resp = await async_client.get("/api/v1/users/me/stats", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["clubsJoined"] == 0


@pytest.mark.asyncio
async def test_update_display_name(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    resp = await async_client.patch("/api/v1/users/me", headers=headers, json={"displayName": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["displayName"] == "New Name"


@pytest.mark.asyncio
async def test_update_role_to_organizer(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    resp = await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "organizer"


@pytest.mark.asyncio
async def test_update_socials(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    resp = await async_client.patch("/api/v1/users/me/socials", headers=headers, json={"telegram": "@user"})
    assert resp.status_code == 200
    assert resp.json()["socials"]["telegram"] == "@user"


@pytest.mark.asyncio
async def test_update_socials_visibility(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    resp = await async_client.patch(
        "/api/v1/users/me/socials-visibility", headers=headers, json={"socialsPublic": False}
    )
    assert resp.status_code == 200
    assert resp.json()["socialsPublic"] is False


@pytest.mark.asyncio
async def test_unauthenticated_access(async_client):
    # All PATCH endpoints should return 401 without token
    resp1 = await async_client.patch("/api/v1/users/me", json={"displayName": "X"})
    resp2 = await async_client.patch("/api/v1/users/me/role", json={"role": "organizer"})
    resp3 = await async_client.patch("/api/v1/users/me/socials", json={"telegram": "@user"})
    resp4 = await async_client.patch("/api/v1/users/me/socials-visibility", json={"socialsPublic": False})
    assert resp1.status_code == 401
    assert resp2.status_code == 401
    assert resp3.status_code == 401
    assert resp4.status_code == 401
