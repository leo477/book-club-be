import uuid

import pytest


async def create_organizer_with_club(async_client, register_user, auth_headers, email="chat_mgmt@example.com"):
    await register_user(email=email)
    headers = await auth_headers(email=email)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": "Chat Mgmt Club", "description": "Desc", "city": "Kyiv"},
    )
    club_id = club_resp.json()["id"]
    return headers, club_id


async def create_room(async_client, headers, club_id, name="General"):
    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/chat/rooms",
        headers=headers,
        json={"name": name},
    )
    return resp.json()["id"]


# --- POST /clubs/{club_id}/chat/rooms ---


@pytest.mark.asyncio
async def test_create_chat_room(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/chat/rooms",
        headers=headers,
        json={"name": "General"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "General"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_chat_room_not_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="cr_org@example.com"
    )
    await register_user(email="cr_member@example.com")
    headers2 = await auth_headers(email="cr_member@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/chat/rooms",
        headers=headers2,
        json={"name": "Hack Room"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_chat_room_appears_in_list(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="cr_list_org@example.com"
    )
    room_id = await create_room(async_client, headers, club_id, name="News")
    list_resp = await async_client.get(f"/api/v1/clubs/{club_id}/chat/rooms", headers=headers)
    assert any(r["id"] == room_id for r in list_resp.json())


# --- DELETE /chat/rooms/{room_id}/messages/{message_id} ---


@pytest.mark.asyncio
async def test_delete_own_message(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="dm_org@example.com"
    )
    room_id = await create_room(async_client, headers, club_id)
    send_resp = await async_client.post(
        f"/api/v1/chat/rooms/{room_id}/messages",
        headers=headers,
        json={"text": "Delete me"},
    )
    msg_id = send_resp.json()["id"]
    resp = await async_client.delete(f"/api/v1/chat/rooms/{room_id}/messages/{msg_id}", headers=headers)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_message_not_found(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="dm_nf_org@example.com"
    )
    room_id = await create_room(async_client, headers, club_id)
    resp = await async_client.delete(f"/api/v1/chat/rooms/{room_id}/messages/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_organizer_can_delete_member_message(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="dm_org2@example.com"
    )
    room_id = await create_room(async_client, headers, club_id)
    await register_user(email="dm_member@example.com")
    headers2 = await auth_headers(email="dm_member@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    send_resp = await async_client.post(
        f"/api/v1/chat/rooms/{room_id}/messages",
        headers=headers2,
        json={"text": "Member msg"},
    )
    msg_id = send_resp.json()["id"]
    # Organizer deletes member's message
    resp = await async_client.delete(f"/api/v1/chat/rooms/{room_id}/messages/{msg_id}", headers=headers)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_member_cannot_delete_other_member_message(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="dm_org3@example.com"
    )
    room_id = await create_room(async_client, headers, club_id)
    await register_user(email="dm_member1@example.com")
    headers1 = await auth_headers(email="dm_member1@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers1)
    send_resp = await async_client.post(
        f"/api/v1/chat/rooms/{room_id}/messages",
        headers=headers1,
        json={"text": "Member1 msg"},
    )
    msg_id = send_resp.json()["id"]
    await register_user(email="dm_member2@example.com")
    headers2 = await auth_headers(email="dm_member2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.delete(f"/api/v1/chat/rooms/{room_id}/messages/{msg_id}", headers=headers2)
    assert resp.status_code == 403


# --- POST /chat/rooms/{room_id}/ban ---


@pytest.mark.asyncio
async def test_ban_user_from_room(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="ban_org@example.com"
    )
    room_id = await create_room(async_client, headers, club_id)
    await register_user(email="ban_target@example.com")
    target_headers = await auth_headers(email="ban_target@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=target_headers)
    # Get target user id from /users/me
    me_resp = await async_client.get("/api/v1/users/me", headers=target_headers)
    target_user_id = me_resp.json()["id"]
    resp = await async_client.post(
        f"/api/v1/chat/rooms/{room_id}/ban",
        headers=headers,
        json={"user_id": target_user_id, "duration_seconds": 3600},
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_ban_not_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="ban_org2@example.com"
    )
    room_id = await create_room(async_client, headers, club_id)
    await register_user(email="ban_member@example.com")
    headers2 = await auth_headers(email="ban_member@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    await register_user(email="ban_target2@example.com")
    target_headers = await auth_headers(email="ban_target2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=target_headers)
    me_resp = await async_client.get("/api/v1/users/me", headers=target_headers)
    target_user_id = me_resp.json()["id"]
    resp = await async_client.post(
        f"/api/v1/chat/rooms/{room_id}/ban",
        headers=headers2,
        json={"user_id": target_user_id, "duration_seconds": 60},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ban_room_not_found(async_client, register_user, auth_headers):
    await register_user(email="ban_nf@example.com")
    headers = await auth_headers(email="ban_nf@example.com")
    resp = await async_client.post(
        f"/api/v1/chat/rooms/{uuid.uuid4()}/ban",
        headers=headers,
        json={"user_id": str(uuid.uuid4()), "duration_seconds": 60},
    )
    assert resp.status_code == 404


# --- Name validation & deduplication ---


@pytest.mark.asyncio
async def test_create_chat_room_duplicate_name_returns_409(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="dup_name_org@example.com"
    )
    await async_client.post(
        f"/api/v1/clubs/{club_id}/chat/rooms",
        headers=headers,
        json={"name": "General"},
    )
    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/chat/rooms",
        headers=headers,
        json={"name": "General"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ROOM_NAME_DUPLICATE"


@pytest.mark.asyncio
async def test_create_chat_room_name_too_short_returns_422(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="short_name_org@example.com"
    )
    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/chat/rooms",
        headers=headers,
        json={"name": "ab"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_chat_room_name_too_long_returns_422(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="long_name_org@example.com"
    )
    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/chat/rooms",
        headers=headers,
        json={"name": "A" * 41},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ban_permanent(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="ban_perm_org@example.com"
    )
    room_id = await create_room(async_client, headers, club_id)
    await register_user(email="ban_perm_target@example.com")
    target_headers = await auth_headers(email="ban_perm_target@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=target_headers)
    me_resp = await async_client.get("/api/v1/users/me", headers=target_headers)
    target_user_id = me_resp.json()["id"]
    # duration_seconds=0 means permanent
    resp = await async_client.post(
        f"/api/v1/chat/rooms/{room_id}/ban",
        headers=headers,
        json={"user_id": target_user_id, "duration_seconds": 0},
    )
    assert resp.status_code == 204
