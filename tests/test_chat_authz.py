import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.main import app
from app.services.chat_connections import ConnectionManager


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
        self.task.cancel()
        try:
            await asyncio.wait_for(self.task, timeout=2)
        except (TimeoutError, asyncio.CancelledError):
            pass


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
