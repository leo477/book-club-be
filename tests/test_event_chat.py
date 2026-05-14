import pytest


async def _setup(async_client, register_user, auth_headers, email):
    await register_user(email=email)
    headers = await auth_headers(email=email)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "Test Club", "description": "D", "city": "Kyiv"},
    )
    club_id = club.json()["id"]
    event = await async_client.post(
        f"/api/v1/clubs/{club_id}/events",
        headers=headers,
        json={"title": "Test Event", "date": "2030-01-01T18:00:00", "city": "Kyiv"},
    )
    return headers, club_id, event.json()["id"]


@pytest.mark.asyncio
async def test_create_event_chat_room(async_client, register_user, auth_headers):
    """Organizer creates an event chat room → 201 with id, name, eventId."""
    headers, club_id, event_id = await _setup(
        async_client, register_user, auth_headers, "chat_create@example.com"
    )

    resp = await async_client.post(f"/api/v1/events/{event_id}/chat/room", headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert "name" in data
    assert data["eventId"] == event_id


@pytest.mark.asyncio
async def test_create_event_chat_room_duplicate_returns_409(async_client, register_user, auth_headers):
    """Creating a chat room twice for the same event returns 409."""
    headers, club_id, event_id = await _setup(
        async_client, register_user, auth_headers, "chat_duplicate@example.com"
    )

    first = await async_client.post(f"/api/v1/events/{event_id}/chat/room", headers=headers)
    assert first.status_code == 201

    second = await async_client.post(f"/api/v1/events/{event_id}/chat/room", headers=headers)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_get_event_chat_room_as_organizer(async_client, register_user, auth_headers):
    """Organizer creates then GETs the event chat room → 200."""
    headers, club_id, event_id = await _setup(
        async_client, register_user, auth_headers, "chat_get@example.com"
    )

    await async_client.post(f"/api/v1/events/{event_id}/chat/room", headers=headers)

    resp = await async_client.get(f"/api/v1/events/{event_id}/chat/room", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["eventId"] == event_id


@pytest.mark.asyncio
async def test_get_event_chat_room_not_found(async_client, register_user, auth_headers):
    """GET before creating the room returns 404."""
    headers, club_id, event_id = await _setup(
        async_client, register_user, auth_headers, "chat_notfound@example.com"
    )

    resp = await async_client.get(f"/api/v1/events/{event_id}/chat/room", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_event_chat_room_non_organizer(async_client, register_user, auth_headers):
    """A non-organizer club member cannot create a chat room → 403."""
    headers, club_id, event_id = await _setup(
        async_client, register_user, auth_headers, "chat_org_nonorg@example.com"
    )

    # Register a second user and have them join the club
    await register_user(email="chat_member_nonorg@example.com")
    member_headers = await auth_headers(email="chat_member_nonorg@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=member_headers)

    resp = await async_client.post(f"/api/v1/events/{event_id}/chat/room", headers=member_headers)
    assert resp.status_code == 403
