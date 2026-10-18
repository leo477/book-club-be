import uuid

import pytest

from app.limiter import limiter
from app.services.chat_connections import manager as chat_manager
from tests.test_chat_authz import _fake_ws, _WsDriver


async def _user(register_user, auth_headers, email):
    limiter._limiter.storage.reset()  # registration is capped at 5/min; these tests need 7 users
    return await auth_headers(email=email)


async def _event_room(async_client, register_user, auth_headers, make_member, tag):
    org = await _user(register_user, auth_headers, f"er_org_{tag}@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org, json={"role": "organizer"})
    club_id = (
        await async_client.post(
            "/api/v1/clubs", headers=org, json={"name": f"ER {tag}", "description": "d", "city": "Kyiv"}
        )
    ).json()["id"]
    event_id = (
        await async_client.post(
            f"/api/v1/clubs/{club_id}/events",
            headers=org,
            json={"title": "Ev", "date": "2030-01-01T18:00:00", "city": "Kyiv"},
        )
    ).json()["id"]
    room_id = (await async_client.get(f"/api/v1/events/{event_id}/chat/room", headers=org)).json()["id"]
    msg_id = (
        await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=org, json={"text": "seed"})
    ).json()["id"]

    attendee = await _user(register_user, auth_headers, f"er_att_{tag}@example.com")
    attendee_id = await make_member(club_id, attendee)
    resp = await async_client.post(f"/api/v1/events/{event_id}/attend", headers=attendee)
    assert resp.status_code == 201
    non_attendee = await _user(register_user, auth_headers, f"er_non_{tag}@example.com")
    non_attendee_id = await make_member(club_id, non_attendee)
    outsider = await _user(register_user, auth_headers, f"er_out_{tag}@example.com")
    other_org = await _user(register_user, auth_headers, f"er_oorg_{tag}@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=other_org, json={"role": "organizer"})
    other_club = (
        await async_client.post(
            "/api/v1/clubs", headers=other_org, json={"name": f"ER other {tag}", "description": "d", "city": "Kyiv"}
        )
    ).json()["id"]
    other_member = await _user(register_user, auth_headers, f"er_om_{tag}@example.com")
    await make_member(other_club, other_member)
    return {
        "room": room_id,
        "msg": msg_id,
        "event": event_id,
        "club": club_id,
        "org": org,
        "attendee": attendee,
        "attendee_id": attendee_id,
        "non_attendee": non_attendee,
        "non_attendee_id": non_attendee_id,
        "outsider": outsider,
        "other_member": other_member,
    }


async def _calls(client, ctx, who, target_id):
    room, msg = ctx["room"], ctx["msg"]
    h = ctx[who]
    base = f"/api/v1/chat/rooms/{room}"
    return {
        "list": await client.get(f"{base}/messages", headers=h),
        "send": await client.post(f"{base}/messages", headers=h, json={"text": "x"}),
        "unread": await client.get(f"{base}/unread-count", headers=h),
        "read": await client.post(f"{base}/read", headers=h, json={"last_read_message_id": msg}),
        "delete_msg": await client.delete(f"{base}/messages/{msg}", headers=h),
        "ban": await client.post(f"{base}/ban", headers=h, json={"user_id": target_id, "duration_seconds": 60}),
        "delete_room": await client.delete(base, headers=h),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["non_attendee", "outsider", "other_member"])
async def test_event_room_denied_uniformly(async_client, register_user, auth_headers, make_member, who, ws_ticket):
    ctx = await _event_room(async_client, register_user, auth_headers, make_member, f"d{who}")
    unknown = await async_client.get(f"/api/v1/chat/rooms/{uuid.uuid4()}/messages", headers=ctx[who])
    results = await _calls(async_client, ctx, who, ctx["attendee_id"])
    for name, resp in results.items():
        assert resp.status_code == 404, name
        assert resp.json() == unknown.json(), name
    after = await async_client.get(f"/api/v1/chat/rooms/{ctx['room']}/messages", headers=ctx["org"])
    assert [m["text"] for m in after.json()] == ["seed"]


@pytest.mark.asyncio
async def test_event_room_attendee_allowed(async_client, register_user, auth_headers, make_member):
    ctx = await _event_room(async_client, register_user, auth_headers, make_member, "att")
    h = ctx["attendee"]
    base = f"/api/v1/chat/rooms/{ctx['room']}"
    resp = await async_client.get(f"{base}/messages", headers=h)
    assert resp.status_code == 200
    sent = await async_client.post(f"{base}/messages", headers=h, json={"text": "hi"})
    assert sent.status_code == 201
    resp = await async_client.get(f"{base}/unread-count", headers=h)
    assert resp.status_code == 200
    read = await async_client.post(f"{base}/read", headers=h, json={"last_read_message_id": sent.json()["id"]})
    assert read.status_code == 204
    resp = await async_client.delete(f"{base}/messages/{sent.json()['id']}", headers=h)
    assert resp.status_code == 204
    # non-organizer attendee reaches the organizer check, not the room check
    ban = await async_client.post(
        f"{base}/ban", headers=h, json={"user_id": ctx["non_attendee_id"], "duration_seconds": 60}
    )
    assert ban.status_code == 403


@pytest.mark.asyncio
async def test_event_room_organizer_without_attendance_allowed(async_client, register_user, auth_headers, make_member):
    ctx = await _event_room(async_client, register_user, auth_headers, make_member, "orgok")
    org = ctx["org"]
    base = f"/api/v1/chat/rooms/{ctx['room']}"
    # the organizer is auto-registered as attendee at event creation; drop that row
    resp = await async_client.delete(f"/api/v1/events/{ctx['event']}/attend", headers=org)
    assert resp.status_code == 204
    resp = await async_client.get(f"{base}/messages", headers=org)
    assert resp.status_code == 200
    resp = await async_client.post(f"{base}/messages", headers=org, json={"text": "o"})
    assert resp.status_code == 201
    resp = await async_client.get(f"{base}/unread-count", headers=org)
    assert resp.status_code == 200
    read = await async_client.post(f"{base}/read", headers=org, json={"last_read_message_id": ctx["msg"]})
    assert read.status_code == 204
    ban = await async_client.post(
        f"{base}/ban", headers=org, json={"user_id": ctx["attendee_id"], "duration_seconds": 60}
    )
    assert ban.status_code == 204
    resp = await async_client.delete(f"{base}/messages/{ctx['msg']}", headers=org)
    assert resp.status_code == 204
    resp = await async_client.delete(base, headers=org)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_event_room_banned_attendee_cannot_send(async_client, register_user, auth_headers, make_member):
    ctx = await _event_room(async_client, register_user, auth_headers, make_member, "ban")
    base = f"/api/v1/chat/rooms/{ctx['room']}"
    await async_client.post(
        f"{base}/ban", headers=ctx["org"], json={"user_id": ctx["attendee_id"], "duration_seconds": 60}
    )
    resp = await async_client.post(f"{base}/messages", headers=ctx["attendee"], json={"text": "x"})
    assert resp.status_code == 403
    assert "ROOM_BANNED" in resp.text


@pytest.mark.asyncio
async def test_unattend_loses_access_and_kicks_socket(async_client, register_user, auth_headers, make_member):
    ctx = await _event_room(async_client, register_user, auth_headers, make_member, "un")
    base = f"/api/v1/chat/rooms/{ctx['room']}"
    sock = _fake_ws()
    chat_manager.connect(ctx["room"], sock, ctx["attendee_id"])
    try:
        resp = await async_client.delete(f"/api/v1/events/{ctx['event']}/attend", headers=ctx["attendee"])
        assert resp.status_code == 204
        sock.close.assert_awaited_once_with(code=1008)
        resp = await async_client.get(f"{base}/messages", headers=ctx["attendee"])
        assert resp.status_code == 404
    finally:
        chat_manager.active_connections.pop(ctx["room"], None)
        chat_manager.room_presence.pop(ctx["room"], None)


@pytest.mark.asyncio
async def test_organizer_unattend_keeps_socket(async_client, register_user, auth_headers, make_member):
    ctx = await _event_room(async_client, register_user, auth_headers, make_member, "orgun")
    org_id = (await async_client.get("/api/v1/users/me", headers=ctx["org"])).json()["id"]
    sock = _fake_ws()
    chat_manager.connect(ctx["room"], sock, org_id)
    try:
        await async_client.delete(f"/api/v1/events/{ctx['event']}/attend", headers=ctx["org"])
        sock.close.assert_not_awaited()
        resp = await async_client.get(f"/api/v1/chat/rooms/{ctx['room']}/messages", headers=ctx["org"])
        assert resp.status_code == 200
    finally:
        chat_manager.active_connections.pop(ctx["room"], None)
        chat_manager.room_presence.pop(ctx["room"], None)


async def _handshake(ctx, who, ws_ticket):
    ws = _WsDriver(ctx["room"])
    await ws.start()
    await ws.send({"type": "auth", "ticket": await ws_ticket(ctx[who])})
    return ws


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["non_attendee", "outsider", "other_member"])
async def test_ws_event_room_denied(async_client, register_user, auth_headers, make_member, who, ws_ticket):
    ctx = await _event_room(async_client, register_user, auth_headers, make_member, f"w{who}")
    ws = await _handshake(ctx, who, ws_ticket)
    frame = await ws.recv()
    assert frame["type"] == "websocket.close"
    assert frame["code"] == 1008
    await ws.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["attendee", "org"])
async def test_ws_event_room_allowed(async_client, register_user, auth_headers, make_member, who, ws_ticket):
    ctx = await _event_room(async_client, register_user, auth_headers, make_member, f"wa{who}")
    ws = await _handshake(ctx, who, ws_ticket)
    try:
        frame = await ws.recv_json()
        assert frame["type"] == "presence_snapshot"
    finally:
        await ws.stop()
        chat_manager.active_connections.pop(ctx["room"], None)
        chat_manager.room_presence.pop(ctx["room"], None)


@pytest.mark.asyncio
async def test_ws_closed_when_membership_removed_after_handshake(
    async_client, register_user, auth_headers, make_member, ws_ticket
):
    """SCRUM-83: membership is enforced after the handshake via the kick on removal."""
    ctx = await _event_room(async_client, register_user, auth_headers, make_member, "s83")
    ws = await _handshake(ctx, "attendee", ws_ticket)
    try:
        frame = await ws.recv_json()
        assert frame["type"] == "presence_snapshot"
        resp = await async_client.delete(
            f"/api/v1/clubs/{ctx['club']}/members/{ctx['attendee_id']}", headers=ctx["org"]
        )
        assert resp.status_code == 204
        frame = await ws.recv()
        while frame["type"] != "websocket.close":
            frame = await ws.recv()
        assert frame["code"] == 1008
    finally:
        await ws.stop()
        chat_manager.active_connections.pop(ctx["room"], None)
        chat_manager.room_presence.pop(ctx["room"], None)


@pytest.mark.asyncio
async def test_club_room_open_to_any_member_regardless_of_attendance(
    async_client, register_user, auth_headers, make_member
):
    ctx = await _event_room(async_client, register_user, auth_headers, make_member, "club")
    rid = (
        await async_client.post(f"/api/v1/clubs/{ctx['club']}/chat/rooms", headers=ctx["org"], json={"name": "General"})
    ).json()["id"]
    for who in ("attendee", "non_attendee"):
        resp = await async_client.get(f"/api/v1/chat/rooms/{rid}/messages", headers=ctx[who])
        assert resp.status_code == 200, who
    resp = await async_client.get(f"/api/v1/chat/rooms/{rid}/messages", headers=ctx["outsider"])
    assert resp.status_code == 404


async def _insert_ban(db_session, room_id, user_id, banned_by, until):
    from app.models.chat import ChatRoomBan

    db_session.add(
        ChatRoomBan(
            id=uuid.uuid4(),
            room_id=uuid.UUID(room_id),
            user_id=uuid.UUID(user_id),
            banned_by=uuid.UUID(banned_by),
            banned_until=until,
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("room_kind", ["club", "event"])
async def test_history_banned_member_denied_expired_ban_allowed(
    async_client, register_user, auth_headers, make_member, db_session, room_kind
):
    from datetime import UTC, datetime, timedelta

    ctx = await _event_room(async_client, register_user, auth_headers, make_member, f"hist{room_kind}")
    if room_kind == "club":
        room = (
            await async_client.post(
                f"/api/v1/clubs/{ctx['club']}/chat/rooms", headers=ctx["org"], json={"name": "Hist"}
            )
        ).json()["id"]
    else:
        room = ctx["room"]
    url = f"/api/v1/chat/rooms/{room}/messages"
    org_id = (await async_client.get("/api/v1/users/me", headers=ctx["org"])).json()["id"]

    assert (await async_client.get(url, headers=ctx["attendee"])).status_code == 200  # member
    assert (await async_client.get(url, headers=ctx["org"])).status_code == 200  # organizer

    # expired ban must not block
    past = datetime.now(UTC) - timedelta(minutes=5)
    await _insert_ban(db_session, room, ctx["attendee_id"], org_id, past)
    assert (await async_client.get(url, headers=ctx["attendee"])).status_code == 200

    # active timed ban
    resp = await async_client.post(
        f"/api/v1/chat/rooms/{room}/ban",
        headers=ctx["org"],
        json={"user_id": ctx["attendee_id"], "duration_seconds": 60},
    )
    assert resp.status_code == 204
    resp = await async_client.get(url, headers=ctx["attendee"])
    assert resp.status_code == 403
    assert "ROOM_BANNED" in resp.text
    # outsider (non-member) still denied; organizer unaffected
    assert (await async_client.get(url, headers=ctx["outsider"])).status_code == 404
    assert (await async_client.get(url, headers=ctx["org"])).status_code == 200


@pytest.mark.asyncio
async def test_history_permanent_ban_denied(async_client, register_user, auth_headers, make_member):
    ctx = await _event_room(async_client, register_user, auth_headers, make_member, "histperm")
    base = f"/api/v1/chat/rooms/{ctx['room']}"
    resp = await async_client.post(
        f"{base}/ban", headers=ctx["org"], json={"user_id": ctx["attendee_id"], "duration_seconds": 0}
    )
    assert resp.status_code == 204
    resp = await async_client.get(f"{base}/messages", headers=ctx["attendee"])
    assert resp.status_code == 403
