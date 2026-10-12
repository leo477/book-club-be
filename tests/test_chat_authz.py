import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect

from app.main import app
from app.repositories import ChatRepository
from app.schemas.chat import normalize_message_text
from app.services.chat_connections import ConnectionManager, close_rooms_best_effort
from app.services.chat_connections import manager as chat_manager


async def _organizer_club_room(async_client, register_user, auth_headers):
    await register_user(email="authz_org@example.com")
    headers = await auth_headers(email="authz_org@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_id = (
        await async_client.post(
            "/api/v1/clubs", headers=headers, json={"name": "Authz Club", "description": "d", "city": "Kyiv"}
        )
    ).json()["id"]
    rooms = (await async_client.get(f"/api/v1/clubs/{club_id}/chat/rooms", headers=headers)).json()
    return headers, club_id, rooms[0]["id"]


async def _outsider(register_user, auth_headers, email="authz_out@example.com"):
    await register_user(email=email)
    return await auth_headers(email=email)


@pytest.mark.asyncio
async def test_non_member_gets_404_on_every_rest_endpoint(async_client, register_user, auth_headers):
    org, club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    msg_id = (
        await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=org, json={"text": "hi"})
    ).json()["id"]
    out = await _outsider(register_user, auth_headers)
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=out)  # pending request only

    calls = [
        ("get", f"/api/v1/clubs/{club_id}/chat/rooms", None),
        ("get", f"/api/v1/chat/rooms/{room_id}/messages", None),
        ("post", f"/api/v1/chat/rooms/{room_id}/messages", {"text": "intrude"}),
        ("get", f"/api/v1/chat/rooms/{room_id}/unread-count", None),
        ("post", f"/api/v1/chat/rooms/{room_id}/read", {"last_read_message_id": msg_id}),
    ]
    for method, url, body in calls:
        resp = await getattr(async_client, method)(url, headers=out, **({"json": body} if body else {}))
        assert resp.status_code == 404, (method, url, resp.status_code)

    unknown = await async_client.get(f"/api/v1/chat/rooms/{uuid.uuid4()}/messages", headers=out)
    assert unknown.status_code == 404
    assert unknown.json() == (await async_client.get(f"/api/v1/chat/rooms/{room_id}/messages", headers=out)).json()

    after = await async_client.get(f"/api/v1/chat/rooms/{room_id}/messages", headers=org)
    assert [m["text"] for m in after.json()] == ["hi"]


@pytest.mark.asyncio
async def test_member_keeps_access(async_client, register_user, auth_headers, make_member):
    org, club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    member = await _outsider(register_user, auth_headers)
    await make_member(club_id, member)
    assert (await async_client.get(f"/api/v1/clubs/{club_id}/chat/rooms", headers=member)).status_code == 200
    sent = await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=member, json={"text": "yo"})
    assert sent.status_code == 201
    assert (await async_client.get(f"/api/v1/chat/rooms/{room_id}/messages", headers=member)).status_code == 200
    assert (await async_client.get(f"/api/v1/chat/rooms/{room_id}/unread-count", headers=member)).status_code == 200
    read = await async_client.post(
        f"/api/v1/chat/rooms/{room_id}/read", headers=member, json={"last_read_message_id": sent.json()["id"]}
    )
    assert read.status_code == 204


@pytest.mark.asyncio
async def test_member_removed_loses_access(async_client, register_user, auth_headers, make_member):
    org, club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    member = await _outsider(register_user, auth_headers)
    await make_member(club_id, member)
    assert (await async_client.delete(f"/api/v1/clubs/{club_id}/leave", headers=member)).status_code == 204
    assert (await async_client.get(f"/api/v1/chat/rooms/{room_id}/messages", headers=member)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   \n\t", "x" * 2001])
async def test_send_message_rejects_invalid_text(async_client, register_user, auth_headers, text):
    org, _club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    resp = await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=org, json={"text": text})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_send_message_accepts_max_length(async_client, register_user, auth_headers):
    org, _club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    resp = await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=org, json={"text": "x" * 2000})
    assert resp.status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 101])
async def test_get_messages_limit_bounds(async_client, register_user, auth_headers, limit):
    org, _club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    resp = await async_client.get(f"/api/v1/chat/rooms/{room_id}/messages?limit={limit}", headers=org)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ban_target_must_be_member(async_client, register_user, auth_headers):
    org, _club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    resp = await async_client.post(
        f"/api/v1/chat/rooms/{room_id}/ban", headers=org, json={"user_id": str(uuid.uuid4()), "duration_seconds": 60}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"user_id": "not-a-uuid", "duration_seconds": 60},
        {"user_id": str(uuid.uuid4()), "duration_seconds": -1},
        {"user_id": str(uuid.uuid4()), "duration_seconds": 10**10},
    ],
)
async def test_ban_validation(async_client, register_user, auth_headers, body):
    org, _club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    resp = await async_client.post(f"/api/v1/chat/rooms/{room_id}/ban", headers=org, json=body)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ws_ticket_rate_limited_per_user(async_client, register_user):
    from app.dependencies import get_redis

    token = (await register_user()).json()["accessToken"]
    app.dependency_overrides[get_redis] = lambda: AsyncMock()
    codes = [
        (await async_client.post("/api/v1/auth/ws-ticket", headers={"Authorization": f"Bearer {token}"})).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [200] * 10
    assert codes[10] == 429


@pytest.mark.asyncio
async def test_manager_kick_closes_only_target_user_in_room():
    mgr = ConnectionManager()
    target, other, other_room = (MagicMock(close=AsyncMock()) for _ in range(3))
    mgr.connect("r1", target, "u1")
    mgr.connect("r1", other, "u2")
    mgr.connect("r2", other_room, "u1")
    await mgr.kick("u1", "r1")
    target.close.assert_awaited_once_with(code=1008)
    other.close.assert_not_awaited()
    other_room.close.assert_not_awaited()
    assert "u1" not in mgr.room_presence["r1"]


class _WsDriver:
    def __init__(self, room_id: str) -> None:
        self.to_app: asyncio.Queue = asyncio.Queue()
        self.from_app: asyncio.Queue = asyncio.Queue()
        path = f"/api/v1/chat/rooms/{room_id}"
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
            "subprotocols": [],
            "extensions": {"websocket.http.response": {}},
            "state": {},
        }
        self.task = asyncio.create_task(app(scope, self.to_app.get, self.from_app.put))

    async def start(self) -> None:
        await self.to_app.put({"type": "websocket.connect"})
        assert (await self.recv())["type"] == "websocket.accept"

    async def send(self, payload) -> None:
        await self.to_app.put({"type": "websocket.receive", "text": json.dumps(payload), "bytes": None})

    async def recv(self) -> dict:
        return await asyncio.wait_for(self.from_app.get(), timeout=5)

    async def recv_json(self) -> dict:
        frame = await self.recv()
        assert frame["type"] == "websocket.send", frame
        return json.loads(frame["text"])

    async def stop(self) -> None:
        await self.to_app.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout=2)
        except TimeoutError:
            self.task.cancel()
            pytest.fail("websocket handler did not finish after disconnect")


@pytest.mark.asyncio
async def test_ws_invalid_room_id_closes_1008(async_client, register_user, auth_headers):
    headers = await _outsider(register_user, auth_headers)
    token = headers["Authorization"].split(" ", 1)[1]
    ws = _WsDriver("not-a-uuid")
    await ws.start()
    await ws.send({"type": "auth", "token": token})
    close = await ws.recv()
    assert close == {"type": "websocket.close", "code": 1008, "reason": ""}
    await ws.stop()


@pytest.mark.asyncio
async def test_ws_garbage_ticket_user_id_closes_1008(async_client, register_user, auth_headers):
    _org, _club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    mock_redis = AsyncMock()
    mock_redis.getdel = AsyncMock(return_value=b"garbage")
    app.state.redis_pool = None
    with patch("app.routers.chat.aioredis.Redis", return_value=mock_redis):
        ws = _WsDriver(room_id)
        await ws.start()
        await ws.send({"type": "auth", "ticket": "t"})
        close = await ws.recv()
        assert close["type"] == "websocket.close"
        assert close["code"] == 1008
        await ws.stop()


@pytest.mark.asyncio
async def test_ws_bad_frames_do_not_crash_and_ban_closes_socket(async_client, register_user, auth_headers, make_member):
    org, club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    member = await _outsider(register_user, auth_headers)
    member_id = await make_member(club_id, member)
    token = member["Authorization"].split(" ", 1)[1]

    ws = _WsDriver(room_id)
    await ws.start()
    await ws.send({"type": "auth", "token": token})
    assert (await ws.recv_json())["type"] == "presence_snapshot"
    assert (await ws.recv_json())["type"] == "presence"

    for bad in (["list"], "string", 5, None):
        await ws.send(bad)
    for bad_text in ({"text": 123}, {"text": "   "}, {"text": "x" * 2001}):
        await ws.send(bad_text)
        err = await ws.recv_json()
        assert err["type"] == "error"
        assert err["payload"]["code"] == "INVALID_MESSAGE"

    await ws.send({"text": "still alive"})
    assert (await ws.recv_json())["payload"]["text"] == "still alive"

    ban = await async_client.post(
        f"/api/v1/chat/rooms/{room_id}/ban", headers=org, json={"user_id": member_id, "duration_seconds": 0}
    )
    assert ban.status_code == 204
    frame = await ws.recv()
    while frame["type"] == "websocket.send":
        frame = await ws.recv()
    assert frame["type"] == "websocket.close"
    assert frame["code"] == 1008
    await ws.stop()


def _fake_ws(close_exc=None):
    return MagicMock(close=AsyncMock(side_effect=close_exc))


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [WebSocketDisconnect(), OSError("broken pipe"), RuntimeError("closed")])
async def test_kick_continues_after_failing_close(exc):
    mgr = ConnectionManager()
    dead, alive = _fake_ws(exc), _fake_ws()
    mgr.connect("r1", dead, "u1")
    mgr.connect("r1", alive, "u1")
    await mgr.kick("u1", "r1")
    alive.close.assert_awaited_once_with(code=1008)


@pytest.mark.asyncio
async def test_close_room_closes_everyone_even_when_one_fails():
    mgr = ConnectionManager()
    dead, alive = _fake_ws(OSError()), _fake_ws()
    mgr.connect("r1", dead, "u1")
    mgr.connect("r1", alive, "u2")
    await mgr.close_room("r1")
    alive.close.assert_awaited_once_with(code=1008)


@pytest.mark.asyncio
async def test_leave_succeeds_and_kicks_remaining_sockets_when_one_close_fails(
    async_client, register_user, auth_headers, make_member
):
    org, club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    member = await _outsider(register_user, auth_headers)
    member_id = await make_member(club_id, member)
    dead, alive = _fake_ws(OSError("half dead")), _fake_ws()
    chat_manager.connect(room_id, dead, member_id)
    chat_manager.connect(room_id, alive, member_id)
    try:
        assert (await async_client.delete(f"/api/v1/clubs/{club_id}/leave", headers=member)).status_code == 204
        alive.close.assert_awaited_once_with(code=1008)
    finally:
        chat_manager.active_connections.pop(room_id, None)
        chat_manager.room_presence.pop(room_id, None)


@pytest.mark.asyncio
async def test_ban_and_remove_member_succeed_when_close_fails(async_client, register_user, auth_headers, make_member):
    org, club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    member = await _outsider(register_user, auth_headers)
    member_id = await make_member(club_id, member)
    chat_manager.connect(room_id, _fake_ws(WebSocketDisconnect()), member_id)
    try:
        ban = await async_client.post(
            f"/api/v1/chat/rooms/{room_id}/ban", headers=org, json={"user_id": member_id, "duration_seconds": 0}
        )
        assert ban.status_code == 204
        chat_manager.connect(room_id, _fake_ws(OSError()), member_id)
        removed = await async_client.delete(f"/api/v1/clubs/{club_id}/members/{member_id}", headers=org)
        assert removed.status_code == 204
    finally:
        chat_manager.active_connections.pop(room_id, None)
        chat_manager.room_presence.pop(room_id, None)


@pytest.mark.asyncio
async def test_ws_handshake_rejects_banned_user(async_client, register_user, auth_headers, make_member):
    org, club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    member = await _outsider(register_user, auth_headers)
    member_id = await make_member(club_id, member)
    ban = await async_client.post(
        f"/api/v1/chat/rooms/{room_id}/ban", headers=org, json={"user_id": member_id, "duration_seconds": 3600}
    )
    assert ban.status_code == 204
    ws = _WsDriver(room_id)
    await ws.start()
    await ws.send({"type": "auth", "token": member["Authorization"].split(" ", 1)[1]})
    frame = await ws.recv()
    assert frame["type"] == "websocket.close"
    assert frame["code"] == 1008
    await ws.stop()


@pytest.mark.asyncio
async def test_delete_message_requires_membership(async_client, register_user, auth_headers, make_member):
    org, club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    member = await _outsider(register_user, auth_headers)
    await make_member(club_id, member)
    msg_id = (
        await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=member, json={"text": "mine"})
    ).json()["id"]
    outsider = await _outsider(register_user, auth_headers, "authz_out2@example.com")
    url = f"/api/v1/chat/rooms/{room_id}/messages/{msg_id}"
    assert (await async_client.delete(url, headers=outsider)).status_code == 404

    await async_client.delete(f"/api/v1/clubs/{club_id}/leave", headers=member)
    assert (await async_client.delete(url, headers=member)).status_code == 404
    assert (await async_client.delete(url, headers=org)).status_code == 204


@pytest.mark.asyncio
async def test_event_chat_room_requires_current_membership(async_client, register_user, auth_headers, make_member):
    org, club_id, _room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    event_id = (
        await async_client.post(
            f"/api/v1/clubs/{club_id}/events",
            headers=org,
            json={"title": "Ev", "date": "2030-01-01T18:00:00", "city": "Kyiv"},
        )
    ).json()["id"]
    member = await _outsider(register_user, auth_headers)
    await make_member(club_id, member)
    assert (await async_client.post(f"/api/v1/events/{event_id}/attend", headers=member)).status_code == 201
    url = f"/api/v1/events/{event_id}/chat/room"
    assert (await async_client.get(url, headers=member)).status_code == 200

    await async_client.delete(f"/api/v1/clubs/{club_id}/leave", headers=member)
    assert (await async_client.get(url, headers=member)).status_code == 403
    assert (await async_client.get(f"/api/v1/events/{uuid.uuid4()}/chat/room", headers=member)).status_code == 404


@pytest.mark.asyncio
async def test_ban_and_delete_room_uniform_404_for_non_members(async_client, register_user, auth_headers, make_member):
    org, club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    outsider = await _outsider(register_user, auth_headers)
    ban_body = {"user_id": str(uuid.uuid4()), "duration_seconds": 60}
    real = await async_client.post(f"/api/v1/chat/rooms/{room_id}/ban", headers=outsider, json=ban_body)
    fake = await async_client.post(f"/api/v1/chat/rooms/{uuid.uuid4()}/ban", headers=outsider, json=ban_body)
    assert real.status_code == fake.status_code == 404
    assert real.json() == fake.json()
    real = await async_client.delete(f"/api/v1/chat/rooms/{room_id}", headers=outsider)
    fake = await async_client.delete(f"/api/v1/chat/rooms/{uuid.uuid4()}", headers=outsider)
    assert real.status_code == fake.status_code == 404
    assert real.json() == fake.json()

    await make_member(club_id, outsider)
    assert (await async_client.delete(f"/api/v1/chat/rooms/{room_id}", headers=outsider)).status_code == 403
    assert (
        await async_client.post(f"/api/v1/chat/rooms/{room_id}/ban", headers=outsider, json=ban_body)
    ).status_code == 403


BAD_TEXTS = ["\x85", "a\x85b", "\x9f", "\x00", "a\x00b", "a\x07", "​", "⠀", "ㅤ", "﻿", " ​⠀ ", "\x1b[31mred"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", BAD_TEXTS)
async def test_rest_and_ws_reject_the_same_texts(async_client, register_user, auth_headers, text):
    org, _club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    assert normalize_message_text(text) is None
    assert (
        await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=org, json={"text": text})
    ).status_code == 422

    ws = _WsDriver(room_id)
    await ws.start()
    await ws.send({"type": "auth", "token": org["Authorization"].split(" ", 1)[1]})
    await ws.recv_json()
    await ws.recv_json()
    await ws.send({"text": text})
    assert (await ws.recv_json())["payload"]["code"] == "INVALID_MESSAGE"
    await ws.stop()


@pytest.mark.asyncio
async def test_rest_and_ws_accept_and_strip_the_same_text(async_client, register_user, auth_headers):
    org, _club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    raw = "  line1\nline2\ttab  "
    assert normalize_message_text(raw) == "line1\nline2\ttab"
    rest = await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=org, json={"text": raw})
    assert rest.json()["text"] == "line1\nline2\ttab"

    ws = _WsDriver(room_id)
    await ws.start()
    await ws.send({"type": "auth", "token": org["Authorization"].split(" ", 1)[1]})
    await ws.recv_json()
    await ws.recv_json()
    await ws.send({"text": raw})
    assert (await ws.recv_json())["payload"]["text"] == "line1\nline2\ttab"
    await ws.stop()


@pytest.mark.asyncio
async def test_ws_invalid_frames_count_toward_flood_limit(async_client, register_user, auth_headers):
    org, _club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    ws = _WsDriver(room_id)
    await ws.start()
    await ws.send({"type": "auth", "token": org["Authorization"].split(" ", 1)[1]})
    await ws.recv_json()
    await ws.recv_json()
    codes = []
    for _ in range(10):
        await ws.send({"text": "\x00"})
        codes.append((await ws.recv_json())["payload"]["code"])
    assert codes == ["INVALID_MESSAGE"] * 9 + ["RATE_LIMITED"]
    await ws.stop()


@pytest.mark.asyncio
async def test_delete_room_closes_its_sockets(async_client, register_user, auth_headers):
    org, _club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    sock = _fake_ws()
    chat_manager.connect(room_id, sock, "someone")
    try:
        assert (await async_client.delete(f"/api/v1/chat/rooms/{room_id}", headers=org)).status_code == 204
        sock.close.assert_awaited_once_with(code=1008)
    finally:
        chat_manager.active_connections.pop(room_id, None)
        chat_manager.room_presence.pop(room_id, None)


@pytest.mark.asyncio
async def test_delete_club_closes_room_sockets(async_client, register_user, auth_headers):
    org, club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    sock = _fake_ws(OSError())
    other = _fake_ws()
    chat_manager.connect(room_id, sock, "a")
    chat_manager.connect(room_id, other, "b")
    try:
        assert (await async_client.delete(f"/api/v1/clubs/{club_id}", headers=org)).status_code in (200, 204)
        other.close.assert_awaited_once_with(code=1008)
    finally:
        chat_manager.active_connections.pop(room_id, None)
        chat_manager.room_presence.pop(room_id, None)


def _cleanup_rooms(*room_ids: str) -> None:
    for rid in room_ids:
        chat_manager.active_connections.pop(rid, None)
        chat_manager.room_presence.pop(rid, None)


async def _member_with_sockets(async_client, register_user, auth_headers, make_member):
    org, club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    second = (
        await async_client.post(f"/api/v1/clubs/{club_id}/chat/rooms", headers=org, json={"name": "Second"})
    ).json()["id"]
    member = await _outsider(register_user, auth_headers)
    member_id = await make_member(club_id, member)
    socks = {room_id: _fake_ws(), second: _fake_ws()}
    for rid, sock in socks.items():
        chat_manager.connect(rid, sock, member_id)
    return org, club_id, member, member_id, socks


def _action(kind: str, club_id: str, member_id: str):
    if kind == "leave":
        return "delete", f"/api/v1/clubs/{club_id}/leave", {}, "member"
    if kind == "remove":
        return "delete", f"/api/v1/clubs/{club_id}/members/{member_id}", {}, "org"
    return "post", f"/api/v1/clubs/{club_id}/members/{member_id}/ban", {"json": {"duration": "permanent"}}, "org"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["leave", "remove", "club_ban"])
async def test_membership_removal_closes_sockets_in_all_club_rooms(
    async_client, register_user, auth_headers, make_member, kind
):
    org, club_id, member, member_id, socks = await _member_with_sockets(
        async_client, register_user, auth_headers, make_member
    )
    try:
        method, url, kwargs, who = _action(kind, club_id, member_id)
        resp = await getattr(async_client, method)(url, headers=org if who == "org" else member, **kwargs)
        assert resp.status_code in (201, 204)
        for sock in socks.values():
            sock.close.assert_awaited_once_with(code=1008)
    finally:
        _cleanup_rooms(*socks)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["leave", "remove", "club_ban"])
@pytest.mark.parametrize("failure", ["kick", "list_rooms"])
async def test_membership_removal_survives_kick_failure_and_is_committed(
    async_client, register_user, auth_headers, make_member, kind, failure
):
    org, club_id, member, member_id, socks = await _member_with_sockets(
        async_client, register_user, auth_headers, make_member
    )
    room_id = next(iter(socks))
    target = (chat_manager, "kick") if failure == "kick" else (ChatRepository, "list_rooms")
    try:
        method, url, kwargs, who = _action(kind, club_id, member_id)
        with patch.object(*target, side_effect=RuntimeError("boom")):
            resp = await getattr(async_client, method)(url, headers=org if who == "org" else member, **kwargs)
        assert resp.status_code in (201, 204)
        assert (await async_client.get(f"/api/v1/chat/rooms/{room_id}/messages", headers=member)).status_code == 404
    finally:
        _cleanup_rooms(*socks)


@pytest.mark.asyncio
async def test_room_ban_survives_kick_failure(async_client, register_user, auth_headers, make_member):
    org, club_id, member, member_id, socks = await _member_with_sockets(
        async_client, register_user, auth_headers, make_member
    )
    room_id = next(iter(socks))
    try:
        with patch.object(chat_manager, "kick", side_effect=RuntimeError("boom")):
            resp = await async_client.post(
                f"/api/v1/chat/rooms/{room_id}/ban", headers=org, json={"user_id": member_id, "duration_seconds": 0}
            )
        assert resp.status_code == 204
        banned = await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=member, json={"text": "x"})
        assert banned.status_code == 403
    finally:
        _cleanup_rooms(*socks)


@pytest.mark.asyncio
async def test_close_rooms_best_effort_continues_past_failing_room():
    closed = []

    async def fake_close(room_id):
        if room_id == "a":
            raise OSError("boom")
        closed.append(room_id)

    with patch.object(chat_manager, "close_room", side_effect=fake_close):
        await close_rooms_best_effort(["a", "b"])  # type: ignore[list-item]
    assert closed == ["b"]


@pytest.mark.asyncio
async def test_room_delete_returns_204_when_closing_sockets_fails(async_client, register_user, auth_headers):
    org, _club_id, room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    with patch.object(chat_manager, "close_room", side_effect=RuntimeError("boom")):
        assert (await async_client.delete(f"/api/v1/chat/rooms/{room_id}", headers=org)).status_code == 204


@pytest.mark.asyncio
async def test_create_event_chat_room_non_member_gets_uniform_404(async_client, register_user, auth_headers):
    org, club_id, _room_id = await _organizer_club_room(async_client, register_user, auth_headers)
    event_id = (
        await async_client.post(
            f"/api/v1/clubs/{club_id}/events",
            headers=org,
            json={"title": "Ev", "date": "2030-01-01T18:00:00", "city": "Kyiv"},
        )
    ).json()["id"]
    outsider = await _outsider(register_user, auth_headers)
    real = await async_client.post(f"/api/v1/events/{event_id}/chat/room", headers=outsider)
    fake = await async_client.post(f"/api/v1/events/{uuid.uuid4()}/chat/room", headers=outsider)
    assert real.status_code == fake.status_code == 404
    assert real.json() == fake.json()


def test_nel_is_rejected_consistently():
    assert normalize_message_text("\x85") is None
    assert normalize_message_text("a\x85") is None
