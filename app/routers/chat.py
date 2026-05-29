import asyncio
import time as _time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, TypedDict

import structlog
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import get_current_user, get_db_dep, get_settings_dep, is_club_organizer, require_club_organizer
from app.exceptions import AppError
from app.models.chat import ChatMessage, ChatRoom, ChatRoomBan, MessageRead
from app.models.club_member import ClubMember
from app.models.event import Event, EventAttendee
from app.models.user import User
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
from app.services.chat_service import check_user_ban, get_room_or_404

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

    async def connect(self, room_id: str, websocket: WebSocket, user_id: str) -> None:
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

    def get_presence_snapshot(self, room_id: str) -> list[dict[str, str]]:
        """Return current online users for the room as a list of presence payloads."""
        return [{"userId": uid, "status": "online"} for uid in self.room_presence[room_id]]

    async def broadcast(self, room_id: str, message: dict[str, Any]) -> None:
        for connection in self.active_connections[room_id].copy():
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
    result = await db.execute(select(ChatRoom).where(ChatRoom.club_id == club_id).distinct())
    rooms = result.scalars().unique().all()
    return [ChatRoomResponse(id=str(r.id), name=r.name, eventId=str(r.event_id) if r.event_id else None) for r in rooms]


@router.post("/clubs/{club_id}/chat/rooms", status_code=status.HTTP_201_CREATED)
async def create_chat_room(
    club_id: uuid.UUID,
    body: CreateChatRoomRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatRoomResponse:
    await require_club_organizer(club_id, current_user, db)

    existing = await db.execute(select(ChatRoom).where(ChatRoom.club_id == club_id, ChatRoom.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise AppError(409, "A room with this name already exists in the club", "ROOM_NAME_DUPLICATE")

    room = ChatRoom(id=uuid.uuid4(), club_id=club_id, name=body.name)
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return ChatRoomResponse(id=str(room.id), name=room.name, eventId=None)


@router.get("/chat/rooms/{room_id}/messages", status_code=status.HTTP_200_OK)
async def get_messages(
    room_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
    before_id: str | None = None,
    limit: int = 50,
) -> list[ChatMessageResponse]:
    await get_room_or_404(room_id, db)

    query = (
        select(ChatMessage, User.display_name)
        .join(User, ChatMessage.sender_id == User.id)
        .where(ChatMessage.room_id == room_id)
    )

    # MN-6: ID-based cursor pagination — no timestamp collision issues
    if before_id:
        try:
            cursor_uuid = uuid.UUID(before_id)
        except ValueError as exc:
            raise AppError(422, "Invalid cursor", "INVALID_CURSOR") from exc
        cursor_ts_subq = select(ChatMessage.timestamp).where(ChatMessage.id == cursor_uuid).scalar_subquery()
        query = query.where(
            (ChatMessage.timestamp < cursor_ts_subq)
            | ((ChatMessage.timestamp == cursor_ts_subq) & (ChatMessage.id < cursor_uuid))
        )

    query = query.order_by(ChatMessage.timestamp.desc(), ChatMessage.id.desc()).limit(limit)
    rows = (await db.execute(query)).all()

    messages = [
        ChatMessageResponse(
            id=str(msg.id),
            senderId=str(msg.sender_id),
            senderName=display_name,
            text=msg.text,
            timestamp=msg.timestamp.isoformat(),
        )
        for msg, display_name in rows
    ]
    messages.reverse()
    return messages


@router.post("/chat/rooms/{room_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(
    room_id: uuid.UUID,
    body: SendMessageRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatMessageResponse:
    await get_room_or_404(room_id, db)

    # MN-4: check if user is banned from this room
    if await check_user_ban(room_id, current_user.id, db):
        raise AppError(403, "You are banned from this room", "ROOM_BANNED")

    msg = ChatMessage(room_id=room_id, sender_id=current_user.id, text=body.text)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

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
    msg_result = await db.execute(
        select(ChatMessage).where(ChatMessage.id == message_id, ChatMessage.room_id == room_id)
    )
    msg = msg_result.scalar_one_or_none()
    if msg is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "Message not found", "MESSAGE_NOT_FOUND")

    if msg.sender_id != current_user.id:
        room = await get_room_or_404(room_id, db)
        if not await is_club_organizer(room.club_id, current_user.id, db):
            raise AppError(status.HTTP_403_FORBIDDEN, "Not authorized to delete this message", "FORBIDDEN")

    await db.delete(msg)
    await db.commit()


@router.post("/chat/rooms/{room_id}/ban", status_code=status.HTTP_204_NO_CONTENT)
async def ban_from_room(
    room_id: uuid.UUID,
    body: BanFromRoomRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    room = await get_room_or_404(room_id, db)
    await require_club_organizer(room.club_id, current_user, db)

    banned_until = None
    if body.duration_seconds > 0:
        banned_until = datetime.now(UTC) + timedelta(seconds=body.duration_seconds)

    ban = ChatRoomBan(
        id=uuid.uuid4(),
        room_id=room_id,
        user_id=uuid.UUID(body.user_id),
        banned_by=current_user.id,
        banned_until=banned_until,
    )
    db.add(ban)
    await db.commit()


# ── Feature 5: read/unread tracking ──────────────────────────────────────────


@router.post("/chat/rooms/{room_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_room_as_read(
    room_id: uuid.UUID,
    body: MarkReadRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Mark messages in a room as read up to `last_read_message_id`."""
    # Validate the message exists in this room.
    msg_result = await db.execute(
        select(ChatMessage).where(
            ChatMessage.id == uuid.UUID(body.last_read_message_id),
            ChatMessage.room_id == room_id,
        )
    )
    if msg_result.scalar_one_or_none() is None:
        raise AppError(404, "Message not found", "MESSAGE_NOT_FOUND")

    # Upsert: insert or update the read pointer.
    existing = await db.execute(
        select(MessageRead).where(
            MessageRead.user_id == current_user.id,
            MessageRead.room_id == room_id,
        )
    )
    read_row = existing.scalar_one_or_none()
    if read_row is None:
        read_row = MessageRead(
            user_id=current_user.id,
            room_id=room_id,
            last_read_message_id=uuid.UUID(body.last_read_message_id),
        )
        db.add(read_row)
    else:
        read_row.last_read_message_id = uuid.UUID(body.last_read_message_id)
        from sqlalchemy.sql import func as sqlfunc

        read_row.updated_at = sqlfunc.now()
    await db.commit()


@router.get("/chat/rooms/{room_id}/unread-count", status_code=status.HTTP_200_OK)
async def get_unread_count(
    room_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> UnreadCountResponse:
    """Return the number of unread messages and the last-read message id."""
    from sqlalchemy import func as sqlfunc

    # Load the user's read pointer for this room.
    read_result = await db.execute(
        select(MessageRead).where(
            MessageRead.user_id == current_user.id,
            MessageRead.room_id == room_id,
        )
    )
    read_row = read_result.scalar_one_or_none()

    if read_row is None or read_row.last_read_message_id is None:
        # No read pointer → all messages are unread.
        total = await db.scalar(select(sqlfunc.count()).where(ChatMessage.room_id == room_id))
        return UnreadCountResponse(
            room_id=str(room_id),
            unread_count=int(total or 0),
            last_read_message_id=None,
        )

    # Count messages with a timestamp strictly after the last-read message.
    last_ts_subq = (
        select(ChatMessage.timestamp).where(ChatMessage.id == read_row.last_read_message_id).scalar_subquery()
    )
    count = await db.scalar(
        select(sqlfunc.count()).where(
            ChatMessage.room_id == room_id,
            ChatMessage.timestamp > last_ts_subq,
        )
    )
    return UnreadCountResponse(
        room_id=str(room_id),
        unread_count=int(count or 0),
        last_read_message_id=str(read_row.last_read_message_id),
    )


@router.websocket("/chat/rooms/{room_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    room_id: str,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> None:
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
    except (TimeoutError, Exception):
        await websocket.close(code=1008)
        return
    if not isinstance(raw, dict) or raw.get("type") != "auth" or not raw.get("token"):
        await websocket.close(code=1008)
        return
    token = raw["token"]
    try:
        token_data = decode_access_token(token, settings)
    except HTTPException:
        await websocket.close(code=1008)
        return

    # Expire any stale transaction snapshot that may have been inherited from the
    # connection pool (e.g. via pool_pre_ping).  Rolling back here guarantees the
    # next statement opens a brand-new READ-COMMITTED snapshot and therefore sees
    # any ClubMember row that was committed by a preceding POST /join request —
    # this is the root-cause fix for the WS-403-after-join bug.
    await db.rollback()

    user_id = token_data.get("sub")
    user_result = await db.execute(select(User).where(User.supabase_user_id == uuid.UUID(str(user_id))))
    user = user_result.scalar_one_or_none()
    if not user:
        await websocket.close(code=1008)
        return

    room_result = await db.execute(select(ChatRoom).where(ChatRoom.id == uuid.UUID(room_id)))
    room = room_result.scalar_one_or_none()
    if not room:
        await websocket.close(code=1008)
        return

    member_result = await db.execute(
        select(ClubMember).where(
            ClubMember.club_id == room.club_id,
            ClubMember.user_id == user.id,
        )
    )
    if member_result.scalar_one_or_none() is None:
        await websocket.close(code=1008)
        return

    await manager.connect(room_id, websocket, str(user.id))

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

    async def check_ban_cached() -> bool:
        nonlocal ban_cache_result, ban_cache_expires
        now_ = datetime.now(UTC)
        if ban_cache_result is not None and now_ < ban_cache_expires:
            return ban_cache_result
        ban_q = await db.execute(
            select(ChatRoomBan).where(
                ChatRoomBan.room_id == uuid.UUID(room_id),
                ChatRoomBan.user_id == user.id,
                (ChatRoomBan.banned_until.is_(None)) | (ChatRoomBan.banned_until > now_),
            )
        )
        ban_cache_result = ban_q.scalar_one_or_none() is not None
        ban_cache_expires = now_ + timedelta(seconds=_ban_cache_ttl)
        return ban_cache_result

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
    except Exception as exc:
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
    from sqlalchemy import delete as sa_delete

    room = await get_room_or_404(room_id, db)
    await require_club_organizer(room.club_id, current_user, db)

    # Delete child records that may not have CASCADE FK constraints.
    # MessageRead and ChatRoomBan reference chat_rooms.id; ChatMessage also does.
    # We delete them explicitly to be safe regardless of migration history.
    await db.execute(sa_delete(MessageRead).where(MessageRead.room_id == room_id))
    await db.execute(sa_delete(ChatRoomBan).where(ChatRoomBan.room_id == room_id))
    await db.execute(sa_delete(ChatMessage).where(ChatMessage.room_id == room_id))
    await db.delete(room)
    await db.commit()


@router.post("/events/{event_id}/chat/room", status_code=status.HTTP_201_CREATED)
async def create_event_chat_room(
    event_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatRoomResponse:
    event_result = await db.execute(select(Event).where(Event.id == event_id))
    event = event_result.scalar_one_or_none()
    if event is None:
        raise AppError(404, "Event not found", "EVENT_NOT_FOUND")

    await require_club_organizer(event.club_id, current_user, db)

    existing_result = await db.execute(select(ChatRoom).where(ChatRoom.event_id == event_id))
    if existing_result.scalar_one_or_none() is not None:
        raise AppError(409, "Event chat room already exists", "EVENT_CHAT_ALREADY_EXISTS")

    room = ChatRoom(club_id=event.club_id, event_id=event_id, name=event.title + " · Chat")
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return ChatRoomResponse(id=str(room.id), name=room.name, eventId=str(room.event_id) if room.event_id else None)


@router.get("/events/{event_id}/chat/room", status_code=status.HTTP_200_OK)
async def get_event_chat_room(
    event_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatRoomResponse:
    event_result = await db.execute(select(Event).where(Event.id == event_id))
    event = event_result.scalar_one_or_none()
    if event is None:
        raise AppError(404, "Event not found", "EVENT_NOT_FOUND")

    organizer = await is_club_organizer(event.club_id, current_user.id, db)
    if not organizer:
        attendee_result = await db.execute(
            select(EventAttendee).where(
                EventAttendee.event_id == event_id,
                EventAttendee.user_id == current_user.id,
            )
        )
        if attendee_result.scalar_one_or_none() is None:
            raise AppError(403, "Access denied", "FORBIDDEN")

    room_result = await db.execute(select(ChatRoom).where(ChatRoom.event_id == event_id))
    room = room_result.scalar_one_or_none()
    if room is None:
        raise AppError(404, "Event chat not found", "EVENT_CHAT_NOT_FOUND")

    return ChatRoomResponse(id=str(room.id), name=room.name, eventId=str(room.event_id) if room.event_id else None)
