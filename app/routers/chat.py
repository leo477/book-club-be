import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.dependencies import get_current_user, get_db_dep, is_club_organizer, require_club_organizer
from app.exceptions import AppError
from app.models.chat import ChatMessage, ChatRoom, ChatRoomBan
from app.models.event import Event, EventAttendee
from app.models.user import User
from app.schemas.chat import (
    BanFromRoomRequest,
    ChatMessageResponse,
    ChatRoomResponse,
    CreateChatRoomRequest,
    SendMessageRequest,
)
from app.services.auth_service import decode_access_token

router = APIRouter(prefix="/api/v1", tags=["chat"])

_ROOM_NOT_FOUND = "Room not found"


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, room_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections[room_id].append(websocket)

    def disconnect(self, room_id: str, websocket: WebSocket) -> None:
        if websocket in self.active_connections[room_id]:
            self.active_connections[room_id].remove(websocket)

    async def broadcast(self, room_id: str, message: dict[str, object]) -> None:
        for connection in self.active_connections[room_id].copy():
            try:
                await connection.send_json(message)
            except WebSocketDisconnect:
                self.disconnect(room_id, connection)
                logger.debug("WebSocket disconnected during broadcast")
            except RuntimeError:
                self.disconnect(room_id, connection)
                logger.warning("Runtime error while broadcasting to a room")


logger = logging.getLogger(__name__)
manager = ConnectionManager()


@router.get("/clubs/{club_id}/chat/rooms", status_code=status.HTTP_200_OK)
async def get_chat_rooms(
    club_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> list[ChatRoomResponse]:
    result = await db.execute(select(ChatRoom).where(ChatRoom.club_id == club_id))
    rooms = result.scalars().all()
    return [ChatRoomResponse(id=str(r.id), name=r.name, eventId=str(r.event_id) if r.event_id else None) for r in rooms]


@router.post("/clubs/{club_id}/chat/rooms", status_code=status.HTTP_201_CREATED)
async def create_chat_room(
    club_id: uuid.UUID,
    body: CreateChatRoomRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatRoomResponse:
    await require_club_organizer(club_id, current_user, db)

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
    # MN-5: verify room exists
    room_result = await db.execute(select(ChatRoom).where(ChatRoom.id == room_id))
    if room_result.scalar_one_or_none() is None:
        raise AppError(404, _ROOM_NOT_FOUND, "ROOM_NOT_FOUND")

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
    # MN-5: verify room exists
    room_result = await db.execute(select(ChatRoom).where(ChatRoom.id == room_id))
    room = room_result.scalar_one_or_none()
    if room is None:
        raise AppError(404, _ROOM_NOT_FOUND, "ROOM_NOT_FOUND")

    # MN-4: check if user is banned from this room
    now = datetime.now(UTC)
    ban_result = await db.execute(
        select(ChatRoomBan).where(
            ChatRoomBan.room_id == room_id,
            ChatRoomBan.user_id == current_user.id,
            (ChatRoomBan.banned_until.is_(None)) | (ChatRoomBan.banned_until > now),
        )
    )
    if ban_result.scalar_one_or_none() is not None:
        raise AppError(403, "You are banned from this room", "ROOM_BANNED")

    msg = ChatMessage(room_id=room_id, sender_id=current_user.id, text=body.text)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Message not found", "code": "MESSAGE_NOT_FOUND"},
        )

    if msg.sender_id != current_user.id:
        room_result = await db.execute(select(ChatRoom).where(ChatRoom.id == room_id))
        room = room_result.scalar_one_or_none()
        if room is None or not await is_club_organizer(room.club_id, current_user.id, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "Not authorized to delete this message", "code": "FORBIDDEN"},
            )

    await db.delete(msg)
    await db.commit()


@router.post("/chat/rooms/{room_id}/ban", status_code=status.HTTP_204_NO_CONTENT)
async def ban_from_room(
    room_id: uuid.UUID,
    body: BanFromRoomRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    room_result = await db.execute(select(ChatRoom).where(ChatRoom.id == room_id))
    room = room_result.scalar_one_or_none()
    if room is None:
        raise AppError(404, _ROOM_NOT_FOUND, "ROOM_NOT_FOUND")

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


@router.websocket("/chat/rooms/{room_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    room_id: str,
    token: str,  # query parameter: ws://...?token=<jwt>
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> None:
    settings = get_settings()
    try:
        token_data = decode_access_token(token, settings)
    except HTTPException:
        await websocket.close(code=1008)
        return

    user_id = token_data.get("sub")
    user_result = await db.execute(select(User).where(User.id == uuid.UUID(str(user_id))))
    user = user_result.scalar_one_or_none()
    if not user:
        await websocket.close(code=1008)
        return

    await manager.connect(room_id, websocket)

    _ban_cache_ttl = 30  # seconds
    ban_cache_result: bool | None = None
    ban_cache_expires: datetime = datetime.now(UTC)

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
        manager.disconnect(room_id, websocket)


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
