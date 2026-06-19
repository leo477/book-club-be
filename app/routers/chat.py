import asyncio
import time as _time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, TypedDict

import structlog
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import get_current_user, get_db_dep, get_settings_dep
from app.models.chat import ChatMessage, ChatRoom
from app.models.user import User
from app.repositories import ChatRepository
from app.schemas.chat import (
    BanFromRoomRequest,
    ChatMessageResponse,
    ChatRoomResponse,
    CreateChatRoomRequest,
    MarkReadRequest,
    SendMessageRequest,
    UnreadCountResponse,
)
from app.services.auth_service import decode_access_token
from app.services.chat_service import (
    ban_from_room_service,
    create_chat_room_service,
    create_event_chat_room_service,
    delete_chat_room_service,
    delete_message_service,
    get_event_chat_room_service,
    get_unread_count_service,
    list_chat_rooms_service,
    list_messages_service,
    mark_room_as_read_service,
    send_message_service,
)

router = APIRouter(prefix="/api/v1", tags=["chat"])


class MessagePayload(TypedDict):
    id: str
    senderId: str
    senderName: str
    text: str
    timestamp: str


class BroadcastMessage(TypedDict):
    type: str
    payload: MessagePayload


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[str, list[WebSocket]] = defaultdict(list)
        # room_id → set of user_id strings currently connected (Feature 4: presence)
        self.room_presence: dict[str, set[str]] = defaultdict(set)

    def connect(self, room_id: str, websocket: WebSocket, user_id: str) -> None:
        websocket._user_id = user_id  # type: ignore[attr-defined]
        self.active_connections[room_id].append(websocket)
        self.room_presence[room_id].add(user_id)

    def disconnect(self, room_id: str, websocket: WebSocket) -> None:
        if websocket in self.active_connections[room_id]:
            self.active_connections[room_id].remove(websocket)
        # Only remove from presence if this user has no remaining connections in the room.
        uid: str | None = getattr(websocket, "_user_id", None)
        if uid:
            still_connected = any(getattr(ws, "_user_id", None) == uid for ws in self.active_connections[room_id])
            if not still_connected:
                self.room_presence[room_id].discard(uid)
        # Drop empty room keys so they don't accumulate after everyone leaves.
        if not self.active_connections[room_id]:
            del self.active_connections[room_id]
        if not self.room_presence[room_id]:
            del self.room_presence[room_id]

    def get_presence_snapshot(self, room_id: str) -> list[dict[str, str]]:
        """Return current online users for the room as a list of presence payloads."""
        return [{"userId": uid, "status": "online"} for uid in self.room_presence.get(room_id, set())]

    async def broadcast(self, room_id: str, message: dict[str, Any]) -> None:
        for connection in self.active_connections.get(room_id, []).copy():
            try:
                await connection.send_json(message)
            except WebSocketDisconnect:
                self.disconnect(room_id, connection)
                logger.debug("WebSocket disconnected during broadcast")
            except RuntimeError:
                self.disconnect(room_id, connection)
                logger.warning("Runtime error while broadcasting to a room")

    async def broadcast_presence(self, room_id: str, user_id: str, status: str) -> None:
        """Broadcast an online/offline event to everyone in the room."""
        await self.broadcast(room_id, {"type": "presence", "payload": {"userId": user_id, "status": status}})


logger = structlog.get_logger(__name__)
manager = ConnectionManager()


@router.get("/clubs/{club_id}/chat/rooms", status_code=status.HTTP_200_OK)
async def get_chat_rooms(
    club_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> list[ChatRoomResponse]:
    return await list_chat_rooms_service(club_id, db)


@router.post("/clubs/{club_id}/chat/rooms", status_code=status.HTTP_201_CREATED)
async def create_chat_room(
    club_id: uuid.UUID,
    body: CreateChatRoomRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatRoomResponse:
    return await create_chat_room_service(club_id, body, current_user, db)


@router.get("/chat/rooms/{room_id}/messages", status_code=status.HTTP_200_OK)
async def get_messages(
    room_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
    before_id: str | None = None,
    limit: int = 50,
) -> list[ChatMessageResponse]:
    return await list_messages_service(room_id, db, before_id=before_id, limit=limit)


@router.post("/chat/rooms/{room_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(
    room_id: uuid.UUID,
    body: SendMessageRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatMessageResponse:
    msg = await send_message_service(room_id, body.text, current_user, db)

    payload: MessagePayload = {
        "id": str(msg.id),
        "senderId": str(msg.sender_id),
        "senderName": current_user.display_name,
        "text": msg.text,
        "timestamp": msg.timestamp.isoformat(),
    }
    await manager.broadcast(str(room_id), {"type": "message", "payload": payload})

    return ChatMessageResponse(
        id=str(msg.id),
        senderId=str(msg.sender_id),
        senderName=current_user.display_name,
        text=msg.text,
        timestamp=msg.timestamp.isoformat(),
    )


@router.delete("/chat/rooms/{room_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    room_id: uuid.UUID,
    message_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    await delete_message_service(room_id, message_id, current_user, db)


@router.post("/chat/rooms/{room_id}/ban", status_code=status.HTTP_204_NO_CONTENT)
async def ban_from_room(
    room_id: uuid.UUID,
    body: BanFromRoomRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    await ban_from_room_service(room_id, body, current_user, db)


# ── Feature 5: read/unread tracking ──────────────────────────────────────────


@router.post("/chat/rooms/{room_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_room_as_read(
    room_id: uuid.UUID,
    body: MarkReadRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Mark messages in a room as read up to `last_read_message_id`."""
    await mark_room_as_read_service(room_id, body, current_user, db)


@router.get("/chat/rooms/{room_id}/unread-count", status_code=status.HTTP_200_OK)
async def get_unread_count(
    room_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> UnreadCountResponse:
    """Return the number of unread messages and the last-read message id."""
    return await get_unread_count_service(room_id, current_user, db)


async def _ws_authenticate(
    websocket: WebSocket,
    db: AsyncSession,
    settings: Settings,
    room_id: str,
) -> tuple[User, ChatRoom] | None:
    # noinspection PyBroadException
    try:
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
    except Exception:  # intentional: any handshake failure closes the socket
        await websocket.close(code=1008)
        return None
    if not isinstance(raw, dict) or raw.get("type") != "auth" or not raw.get("token"):
        await websocket.close(code=1008)
        return None
    try:
        token_data = decode_access_token(raw["token"], settings)
    except HTTPException:
        await websocket.close(code=1008)
        return None
    # Expire any stale transaction snapshot that may have been inherited from the
    # connection pool (e.g. via pool_pre_ping).  Rolling back here guarantees the
    # next statement opens a brand-new READ-COMMITTED snapshot and therefore sees
    # any ClubMember row that was committed by a preceding POST /join request —
    # this is the root-cause fix for the WS-403-after-join bug.
    await db.rollback()
    repo = ChatRepository(db)
    user_id = token_data.get("sub")
    user = await repo.get_user_by_supabase_id(uuid.UUID(str(user_id)))
    if not user:
        await websocket.close(code=1008)
        return None
    room = await repo.get_room(uuid.UUID(room_id))
    if not room:
        await websocket.close(code=1008)
        return None
    if await repo.get_membership(room.club_id, user.id) is None:
        await websocket.close(code=1008)
        return None
    return user, room


@router.websocket("/chat/rooms/{room_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    room_id: str,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> None:
    await websocket.accept()
    result = await _ws_authenticate(websocket, db, settings, room_id)
    if result is None:
        return
    user, _room = result

    manager.connect(room_id, websocket, str(user.id))

    # Feature 4: send current presence snapshot to the newly connected user.
    await websocket.send_json(
        {
            "type": "presence_snapshot",
            "payload": manager.get_presence_snapshot(room_id),
        }
    )
    # Broadcast that this user is now online to everyone else in the room.
    await manager.broadcast_presence(room_id, str(user.id), "online")

    _ban_cache_ttl = 5  # seconds
    ban_cache_result: bool | None = None
    ban_cache_expires: datetime = datetime.now(UTC)
    _msg_timestamps: deque[float] = deque(maxlen=10)

    ban_repo = ChatRepository(db)

    async def check_ban_cached() -> bool:
        nonlocal ban_cache_result, ban_cache_expires
        now_ = datetime.now(UTC)
        if ban_cache_result is not None and now_ < ban_cache_expires:
            return ban_cache_result
        ban = await ban_repo.get_active_ban(uuid.UUID(room_id), user.id, now_)
        ban_cache_result = ban is not None
        ban_cache_expires = now_ + timedelta(seconds=_ban_cache_ttl)
        return ban_cache_result

    # noinspection PyBroadException
    try:
        while True:
            data = await websocket.receive_json()
            text = data.get("text", "")
            if not text:
                continue

            _now = _time.monotonic()
            _msg_timestamps.append(_now)
            if len(_msg_timestamps) == 10 and (_now - _msg_timestamps[0]) < 10.0:
                await websocket.send_json(
                    {"type": "error", "payload": {"code": "RATE_LIMITED", "message": "Too many messages"}}
                )
                continue

            if await check_ban_cached():
                await websocket.send_json(
                    {"type": "error", "payload": {"code": "ROOM_BANNED", "message": "You are banned from this room"}}
                )
                continue

            msg = ChatMessage(room_id=uuid.UUID(room_id), sender_id=user.id, text=text)
            db.add(msg)
            await db.commit()
            await db.refresh(msg)

            await manager.broadcast(
                room_id,
                {
                    "type": "message",
                    "payload": {
                        "id": str(msg.id),
                        "senderId": str(msg.sender_id),
                        "senderName": user.display_name,
                        "text": msg.text,
                        "timestamp": msg.timestamp.isoformat(),
                    },
                },
            )
    except WebSocketDisconnect:
        pass  # Normal client disconnect; the finally block below handles cleanup
    except Exception as exc:  # intentional: keep the socket handler alive and log any unexpected error
        logger.exception("Unexpected WebSocket error", exc_info=exc)
    finally:
        # Feature 4: broadcast offline status before removing from connection list.
        will_go_offline = str(user.id) not in {
            getattr(ws, "_user_id", None) for ws in manager.active_connections[room_id] if ws is not websocket
        }
        manager.disconnect(room_id, websocket)
        if will_go_offline:
            await manager.broadcast_presence(room_id, str(user.id), "offline")


@router.delete("/chat/rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_room(
    room_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete a chat room. Only the club organizer may do this."""
    await delete_chat_room_service(room_id, current_user, db)


@router.post("/events/{event_id}/chat/room", status_code=status.HTTP_201_CREATED)
async def create_event_chat_room(
    event_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatRoomResponse:
    return await create_event_chat_room_service(event_id, current_user, db)


@router.get("/events/{event_id}/chat/room", status_code=status.HTTP_200_OK)
async def get_event_chat_room(
    event_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatRoomResponse:
    return await get_event_chat_room_service(event_id, current_user, db)
