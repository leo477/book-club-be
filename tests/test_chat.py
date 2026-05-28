import asyncio
import json

import pytest

from app.main import app


async def create_organizer_with_club(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "Chat Club", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]
    return headers, club_id


@pytest.mark.asyncio
async def test_list_chat_rooms_has_general(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/chat/rooms", headers=headers)
    assert resp.status_code == 200
    rooms = resp.json()
    assert len(rooms) == 1
    assert rooms[0]["name"].endswith("· General")


@pytest.mark.asyncio
async def test_send_and_get_messages(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    # Create chat room (assume at least one exists after club creation or create if needed)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/chat/rooms", headers=headers)
    assert resp.status_code == 200
    rooms = resp.json()
    assert rooms, "Expected at least one room (General) after club creation"
    room_id = rooms[0]["id"]
    msg_payload = {"text": "Hello world!"}
    send_resp = await async_client.post(f"/api/v1/chat/rooms/{room_id}/messages", headers=headers, json=msg_payload)
    assert send_resp.status_code == 201
    get_resp = await async_client.get(f"/api/v1/chat/rooms/{room_id}/messages", headers=headers)
    assert get_resp.status_code == 200
    msgs = get_resp.json()
    assert any(m["text"] == "Hello world!" for m in msgs)


@pytest.mark.asyncio
async def test_ws_room_membership_after_join(async_client, register_user, auth_headers):
    """Regression: POST /clubs/{id}/join then WS connect must get 101, not 403.

    Root cause: the WS session's db connection could inherit a stale transaction
    snapshot from the asyncpg pool (taken before the join committed), causing the
    ClubMember SELECT to return no rows even though the row exists in the DB.
    Fix: db.rollback() at the top of websocket_endpoint forces a fresh snapshot.

    Uses a direct async ASGI WebSocket driver to stay in the same event loop as
    the test_engine/aiosqlite session — avoids the cross-loop issues of TestClient.
    """
    # --- Arrange: create organizer and club ---
    org_email = "ws_org@example.com"
    await register_user(email=org_email)
    org_headers = await auth_headers(email=org_email)
    await async_client.patch("/api/v1/users/me/role", headers=org_headers, json={"role": "organizer"})

    club_resp = await async_client.post(
        "/api/v1/clubs",
        headers=org_headers,
        json={"name": "WS Test Club", "description": "WS desc", "city": "Kyiv"},
    )
    assert club_resp.status_code == 201
    club_id = club_resp.json()["id"]

    rooms_resp = await async_client.get(f"/api/v1/clubs/{club_id}/chat/rooms", headers=org_headers)
    assert rooms_resp.status_code == 200
    room_id = rooms_resp.json()[0]["id"]

    # --- Arrange: register a regular member and join the club ---
    member_email = "ws_member@example.com"
    await register_user(email=member_email)
    member_headers = await auth_headers(email=member_email)
    member_token = member_headers["Authorization"].split(" ", 1)[1]

    join_resp = await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=member_headers)
    assert join_resp.status_code == 200, f"Join failed: {join_resp.text}"

    # --- Act: drive the WS ASGI endpoint directly inside the current event loop ---
    # We wire up two asyncio.Queue pairs to simulate the ASGI receive/send channels,
    # then run the app coroutine concurrently with our test interaction.
    to_app: asyncio.Queue = asyncio.Queue()  # test → handler
    from_app: asyncio.Queue = asyncio.Queue()  # handler → test

    ws_scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "ws",
        "path": f"/api/v1/chat/rooms/{room_id}",
        "raw_path": f"/api/v1/chat/rooms/{room_id}".encode(),
        "root_path": "",
        "query_string": f"token={member_token}".encode(),
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "subprotocols": [],
        "extensions": {"websocket.http.response": {}},
        "state": {},
    }

    async def receive():
        return await to_app.get()

    async def send(message):
        await from_app.put(message)

    # Kick off the handler in the background
    handler_task = asyncio.create_task(app(ws_scope, receive, send))

    # Simulate the WS upgrade handshake
    await to_app.put({"type": "websocket.connect"})

    # The handler may reject (close without accept) or accept
    first_msg = await asyncio.wait_for(from_app.get(), timeout=5)

    # --- Assert: handler accepted the connection (not rejected it) ---
    assert first_msg["type"] == "websocket.accept", (
        f"Expected websocket.accept but got {first_msg['type']!r} — "
        "membership check after join is still failing (403 bug not fixed)"
    )

    # After accept, the server sends a presence_snapshot and an online presence
    # broadcast before waiting for messages from the client.  Drain those first.
    async def drain_non_message_frames(n: int = 3) -> None:
        """Consume up to `n` non-'message' WS frames sent right after connect."""
        for _ in range(n):
            try:
                frame = await asyncio.wait_for(from_app.get(), timeout=1)
                if frame.get("type") != "websocket.send":
                    break
                envelope = json.loads(frame["text"])
                if envelope.get("type") == "message":
                    # Put it back — shouldn't happen here but be safe
                    await from_app.put(frame)  # type: ignore[attr-defined]
                    break
            except TimeoutError:
                break

    await drain_non_message_frames()

    # Send a chat message and expect it broadcast back
    await to_app.put({"type": "websocket.receive", "text": json.dumps({"text": "hello from member"}), "bytes": None})
    broadcast = await asyncio.wait_for(from_app.get(), timeout=5)
    assert broadcast["type"] == "websocket.send"
    payload = json.loads(broadcast["text"])
    assert payload["type"] == "message"
    assert payload["payload"]["text"] == "hello from member"

    # Close the connection cleanly
    await to_app.put({"type": "websocket.disconnect", "code": 1000})
    handler_task.cancel()
    try:
        await asyncio.wait_for(handler_task, timeout=2)
    except (TimeoutError, asyncio.CancelledError):
        # Expected during test teardown after cancelling the websocket handler task.
        # Intentionally ignored to avoid masking the functional assertions above.
        pass
