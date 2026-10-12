import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func as sqlfunc
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import SYSTEM_USER_ID
from app.dependencies import is_club_organizer, require_club_organizer
from app.exceptions import AppError
from app.models.chat import ChatMessage, ChatRoom, ChatRoomBan, MessageRead
from app.models.event import Event, EventAttendee
from app.models.user import User
from app.repositories import ChatRepository
from app.schemas.chat import (
    BanFromRoomRequest,
    ChatMessageResponse,
    ChatRoomResponse,
    CreateChatRoomRequest,
    MarkReadRequest,
    UnreadCountResponse,
)
from app.services.chat_connections import manager

logger = structlog.get_logger(__name__)


async def get_room_or_404(room_id: uuid.UUID, db: AsyncSession) -> ChatRoom:
    repo = ChatRepository(db)
    room = await repo.get_room(room_id)
    if not room:
        raise AppError(404, "Chat room not found", "ROOM_NOT_FOUND")
    return room


async def require_club_member(club_id: uuid.UUID, user: User, db: AsyncSession) -> None:
    if await ChatRepository(db).get_membership(club_id, user.id) is None:
        raise AppError(404, "Chat room not found", "ROOM_NOT_FOUND")


async def require_room_member(room_id: uuid.UUID, user: User, db: AsyncSession) -> ChatRoom:
    room = await ChatRepository(db).get_room(room_id)
    if room is None or await ChatRepository(db).get_membership(room.club_id, user.id) is None:
        raise AppError(404, "Chat room not found", "ROOM_NOT_FOUND")
    return room


async def kick_user_from_club_rooms(club_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> None:
    """Best-effort: the DB change is already committed, so socket failures must not fail the request."""
    try:
        for room in await ChatRepository(db).list_rooms(club_id):
            await manager.kick(str(user_id), str(room.id))
    except Exception:  # intentional: see docstring
        logger.warning("Failed to kick user sockets", club_id=str(club_id), user_id=str(user_id), exc_info=True)


async def close_rooms_best_effort(room_ids: list[uuid.UUID]) -> None:
    for room_id in room_ids:
        try:
            await manager.close_room(str(room_id))
        except Exception:  # intentional: deletion is already committed
            logger.warning("Failed to close room sockets", room_id=str(room_id), exc_info=True)


async def check_user_ban(room_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> bool:
    now = datetime.now(UTC)
    ban = await db.scalar(
        select(ChatRoomBan).where(
            ChatRoomBan.room_id == room_id,
            ChatRoomBan.user_id == user_id,
            (ChatRoomBan.banned_until.is_(None)) | (ChatRoomBan.banned_until > now),
        )
    )
    return ban is not None


async def list_chat_rooms_service(club_id: uuid.UUID, current_user: User, db: AsyncSession) -> list[ChatRoomResponse]:
    await require_club_member(club_id, current_user, db)
    repo = ChatRepository(db)
    rooms = await repo.list_rooms(club_id)
    return [ChatRoomResponse(id=str(r.id), name=r.name, eventId=str(r.event_id) if r.event_id else None) for r in rooms]


async def create_chat_room_service(
    club_id: uuid.UUID,
    body: CreateChatRoomRequest,
    current_user: User,
    db: AsyncSession,
) -> ChatRoomResponse:
    await require_club_organizer(club_id, current_user, db)

    repo = ChatRepository(db)
    if await repo.get_room_by_name(club_id, body.name) is not None:
        raise AppError(409, "A room with this name already exists in the club", "ROOM_NAME_DUPLICATE")

    room = ChatRoom(id=uuid.uuid4(), club_id=club_id, name=body.name)
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return ChatRoomResponse(id=str(room.id), name=room.name, eventId=None)


async def list_messages_service(
    room_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
    before_id: str | None = None,
    limit: int = 50,
) -> list[ChatMessageResponse]:
    await require_room_member(room_id, current_user, db)

    cursor_uuid: uuid.UUID | None = None
    if before_id:
        try:
            cursor_uuid = uuid.UUID(before_id)
        except ValueError as exc:
            raise AppError(422, "Invalid cursor", "INVALID_CURSOR") from exc

    repo = ChatRepository(db)
    rows = await repo.list_messages_paginated(room_id, before_id=cursor_uuid, limit=limit)

    messages = [
        ChatMessageResponse(
            id=str(msg.id),
            senderId=str(msg.sender_id),
            senderName=display_name,
            text=msg.text,
            timestamp=msg.timestamp.isoformat(),
            isSystem=msg.sender_id == SYSTEM_USER_ID,
        )
        for msg, display_name in rows
    ]
    messages.reverse()
    return messages


async def send_message_service(
    room_id: uuid.UUID,
    text: str,
    current_user: User,
    db: AsyncSession,
) -> ChatMessage:
    await require_room_member(room_id, current_user, db)

    if await check_user_ban(room_id, current_user.id, db):
        raise AppError(403, "You are banned from this room", "ROOM_BANNED")

    msg = ChatMessage(room_id=room_id, sender_id=current_user.id, text=text)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def delete_message_service(
    room_id: uuid.UUID,
    message_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> None:
    room = await require_room_member(room_id, current_user, db)
    repo = ChatRepository(db)
    msg = await repo.get_message(message_id, room_id)
    if msg is None:
        raise AppError(404, "Message not found", "MESSAGE_NOT_FOUND")

    if msg.sender_id != current_user.id:
        if not await is_club_organizer(room.club_id, current_user.id, db):
            raise AppError(403, "Not authorized to delete this message", "FORBIDDEN")

    await db.delete(msg)
    await db.commit()


async def ban_from_room_service(
    room_id: uuid.UUID,
    body: BanFromRoomRequest,
    current_user: User,
    db: AsyncSession,
) -> None:
    room = await require_room_member(room_id, current_user, db)
    await require_club_organizer(room.club_id, current_user, db)

    if await ChatRepository(db).get_membership(room.club_id, body.user_id) is None:
        raise AppError(404, "User is not a member of this club", "MEMBER_NOT_FOUND")

    banned_until = None
    if body.duration_seconds > 0:
        banned_until = datetime.now(UTC) + timedelta(seconds=body.duration_seconds)

    ban = ChatRoomBan(
        id=uuid.uuid4(),
        room_id=room_id,
        user_id=body.user_id,
        banned_by=current_user.id,
        banned_until=banned_until,
    )
    db.add(ban)
    await db.commit()
    await manager.kick(str(body.user_id), str(room_id))


async def mark_room_as_read_service(
    room_id: uuid.UUID,
    body: MarkReadRequest,
    current_user: User,
    db: AsyncSession,
) -> None:
    await require_room_member(room_id, current_user, db)
    repo = ChatRepository(db)
    try:
        last_read_id = uuid.UUID(body.last_read_message_id)
    except ValueError as exc:
        raise AppError(400, "Invalid message id", "INVALID_MESSAGE_ID") from exc
    if await repo.get_message(last_read_id, room_id) is None:
        raise AppError(404, "Message not found", "MESSAGE_NOT_FOUND")

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
            last_read_message_id=last_read_id,
        )
        db.add(read_row)
    else:
        read_row.last_read_message_id = last_read_id
        read_row.updated_at = sqlfunc.now()
    await db.commit()


async def get_unread_count_service(
    room_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> UnreadCountResponse:
    await require_room_member(room_id, current_user, db)
    read_result = await db.execute(
        select(MessageRead).where(
            MessageRead.user_id == current_user.id,
            MessageRead.room_id == room_id,
        )
    )
    read_row = read_result.scalar_one_or_none()

    if read_row is None or read_row.last_read_message_id is None:
        total = await db.scalar(select(sqlfunc.count()).where(ChatMessage.room_id == room_id))
        return UnreadCountResponse(
            room_id=str(room_id),
            unread_count=int(total or 0),
            last_read_message_id=None,
        )

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


async def delete_chat_room_service(
    room_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> None:
    from sqlalchemy import delete as sa_delete

    room = await require_room_member(room_id, current_user, db)
    await require_club_organizer(room.club_id, current_user, db)

    await db.execute(sa_delete(MessageRead).where(MessageRead.room_id == room_id))
    await db.execute(sa_delete(ChatRoomBan).where(ChatRoomBan.room_id == room_id))
    await db.execute(sa_delete(ChatMessage).where(ChatMessage.room_id == room_id))
    await db.delete(room)
    await db.commit()
    await close_rooms_best_effort([room_id])


async def get_or_create_event_chat_room(event: Event, db: AsyncSession) -> ChatRoom:
    """Idempotent get-or-create so every event ends up with exactly one chat room,
    whether it's auto-created at event creation or lazily requested for an older event."""
    existing = await db.scalar(select(ChatRoom).where(ChatRoom.event_id == event.id))
    if existing is not None:
        return existing

    room = ChatRoom(club_id=event.club_id, event_id=event.id, name=event.title + " · Chat")
    db.add(room)
    await db.flush()
    await db.refresh(room)
    return room


async def create_event_chat_room_service(
    event_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> ChatRoomResponse:
    event = await db.scalar(select(Event).where(Event.id == event_id))
    if event is None:
        raise AppError(404, "Event not found", "EVENT_NOT_FOUND")

    await require_club_organizer(event.club_id, current_user, db)

    room = await get_or_create_event_chat_room(event, db)
    await db.commit()
    await db.refresh(room)
    return ChatRoomResponse(id=str(room.id), name=room.name, eventId=str(room.event_id) if room.event_id else None)


async def get_event_chat_room_service(
    event_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> ChatRoomResponse:
    event = await db.scalar(select(Event).where(Event.id == event_id))
    if event is None:
        raise AppError(404, "Event not found", "EVENT_NOT_FOUND")
    if await ChatRepository(db).get_membership(event.club_id, current_user.id) is None:
        raise AppError(404, "Event not found", "EVENT_NOT_FOUND")

    organizer = await is_club_organizer(event.club_id, current_user.id, db)
    if not organizer:
        attendee = await db.scalar(
            select(EventAttendee).where(
                EventAttendee.event_id == event_id,
                EventAttendee.user_id == current_user.id,
            )
        )
        if attendee is None:
            raise AppError(403, "Access denied", "FORBIDDEN")

    room = await db.scalar(select(ChatRoom).where(ChatRoom.event_id == event_id))
    if room is None:
        raise AppError(404, "Event chat not found", "EVENT_CHAT_NOT_FOUND")

    return ChatRoomResponse(id=str(room.id), name=room.name, eventId=str(room.event_id) if room.event_id else None)
