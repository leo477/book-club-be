import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep
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
    normalize_message_text,
)
from app.services.chat_connections import WS_POLICY_VIOLATION, MessagePayload, manager
from app.services.chat_flood import flood_limiter
from app.services.chat_service import (
    ban_from_room_service,
    can_access_room,
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


logger = structlog.get_logger(__name__)


@router.get("/clubs/{club_id}/chat/rooms", status_code=status.HTTP_200_OK)
async def get_chat_rooms(
    club_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[ChatRoomResponse]:
    return await list_chat_rooms_service(club_id, current_user, db)


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
    current_user: Annotated[User, Depends(get_current_user)],
    before_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ChatMessageResponse]:
    return await list_messages_service(room_id, current_user, db, before_id=before_id, limit=limit)


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
        "isSystem": False,
    }
    await manager.broadcast(str(room_id), {"type": "message", "payload": payload})

    return ChatMessageResponse(
        id=str(msg.id),
        senderId=str(msg.sender_id),
        senderName=current_user.display_name,
        text=msg.text,
        isSystem=False,
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


_WS_TICKET_PREFIX = "ws:ticket:"
_WS_LEGACY_TOKEN_REASON = "token auth frame removed; use POST /auth/ws-ticket"  # noqa: S105


async def _ws_authenticate(
    websocket: WebSocket,
    db: AsyncSession,
    room_id: str,
) -> tuple[User, ChatRoom] | None:
    # noinspection PyBroadException
    try:
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
    except Exception:  # intentional: any handshake failure closes the socket
        await websocket.close(code=WS_POLICY_VIOLATION)
        return None
    if not isinstance(raw, dict) or raw.get("type") != "auth":
        await websocket.close(code=WS_POLICY_VIOLATION)
        return None
    if "token" in raw and not raw.get("ticket"):
        # Legacy access-token auth frame was removed (SCRUM-169): clients must use POST /auth/ws-ticket.
        await websocket.close(code=WS_POLICY_VIOLATION, reason=_WS_LEGACY_TOKEN_REASON)
        return None
    if not isinstance(raw.get("ticket"), str) or not raw["ticket"]:
        await websocket.close(code=WS_POLICY_VIOLATION)
        return None

    # Expire any stale transaction snapshot that may have been inherited from the
    # connection pool (e.g. via pool_pre_ping).  Rolling back here guarantees the
    # next statement opens a brand-new READ-COMMITTED snapshot and therefore sees
    # any ClubMember row that was committed by a preceding POST /join request —
    # this is the root-cause fix for the WS-403-after-join bug.
    await db.rollback()
    repo = ChatRepository(db)

    try:
        room_uuid = uuid.UUID(room_id)
    except ValueError:
        await websocket.close(code=WS_POLICY_VIOLATION)
        return None

    redis = aioredis.Redis(connection_pool=websocket.app.state.redis_pool)
    raw_user_id = await redis.getdel(f"{_WS_TICKET_PREFIX}{raw['ticket']}")
    if not raw_user_id:
        await websocket.close(code=WS_POLICY_VIOLATION)
        return None
    user_id = raw_user_id.decode() if isinstance(raw_user_id, bytes) else raw_user_id
    try:
        user = await repo.get_user_by_id(uuid.UUID(user_id))
    except ValueError:
        await websocket.close(code=WS_POLICY_VIOLATION)
        return None

    if not user:
        await websocket.close(code=WS_POLICY_VIOLATION)
        return None
    room = await repo.get_room(room_uuid)
    if not room:
        await websocket.close(code=WS_POLICY_VIOLATION)
        return None
    if not await can_access_room(room, user.id, db):
        await websocket.close(code=WS_POLICY_VIOLATION)
        return None
    if await repo.get_active_ban(room_uuid, user.id, datetime.now(UTC)) is not None:
        await websocket.close(code=WS_POLICY_VIOLATION)
        return None
    return user, room


@router.websocket("/chat/rooms/{room_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    room_id: str,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> None:
    await websocket.accept()
    result = await _ws_authenticate(websocket, db, room_id)
    if result is None:
        return
    user, room = result
    # Canonicalise: uuid.UUID() accepts many spellings of one id; manager, flood limiter and
    # ban cache must all see a single form.
    room_id = str(room.id)

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
    flood_limiter.acquire(room_id, str(user.id))
    try:
        flood_redis: aioredis.Redis | None = aioredis.Redis(connection_pool=websocket.app.state.redis_pool)
    except Exception:  # intentional: no Redis -> in-process limiting only
        flood_redis = None

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

            if await flood_limiter.is_limited(room_id, str(user.id), flood_redis):
                await websocket.send_json(
                    {"type": "error", "payload": {"code": "RATE_LIMITED", "message": "Too many messages"}}
                )
                continue

            if not isinstance(data, dict):
                continue
            text = normalize_message_text(data.get("text"))
            if text is None:
                await websocket.send_json(
                    {"type": "error", "payload": {"code": "INVALID_MESSAGE", "message": "Invalid message text"}}
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
                        "isSystem": False,
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
        flood_limiter.release(room_id, str(user.id))
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
