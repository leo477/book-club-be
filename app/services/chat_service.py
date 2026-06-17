import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def get_room_or_404(room_id: uuid.UUID, db: AsyncSession) -> ChatRoom:
    repo = ChatRepository(db)
    room = await repo.get_room(room_id)
    if not room:
        raise AppError(404, "Chat room not found", "ROOM_NOT_FOUND")
    return room


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


async def list_chat_rooms_service(club_id: uuid.UUID, db: AsyncSession) -> list[ChatRoomResponse]:
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
    db: AsyncSession,
    before_id: str | None = None,
    limit: int = 50,
) -> list[ChatMessageResponse]:
    await get_room_or_404(room_id, db)

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
    await get_room_or_404(room_id, db)

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
    repo = ChatRepository(db)
    msg = await repo.get_message(message_id, room_id)
    if msg is None:
        raise AppError(404, "Message not found", "MESSAGE_NOT_FOUND")

    if msg.sender_id != current_user.id:
        room = await get_room_or_404(room_id, db)
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


async def mark_room_as_read_service(
    room_id: uuid.UUID,
    body: MarkReadRequest,
    current_user: User,
    db: AsyncSession,
) -> None:
    repo = ChatRepository(db)
    last_read_id = uuid.UUID(body.last_read_message_id)
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

    room = await get_room_or_404(room_id, db)
    await require_club_organizer(room.club_id, current_user, db)

    await db.execute(sa_delete(MessageRead).where(MessageRead.room_id == room_id))
    await db.execute(sa_delete(ChatRoomBan).where(ChatRoomBan.room_id == room_id))
    await db.execute(sa_delete(ChatMessage).where(ChatMessage.room_id == room_id))
    await db.delete(room)
    await db.commit()


async def create_event_chat_room_service(
    event_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> ChatRoomResponse:
    event = await db.scalar(select(Event).where(Event.id == event_id))
    if event is None:
        raise AppError(404, "Event not found", "EVENT_NOT_FOUND")

    await require_club_organizer(event.club_id, current_user, db)

    existing = await db.scalar(select(ChatRoom).where(ChatRoom.event_id == event_id))
    if existing is not None:
        raise AppError(409, "Event chat room already exists", "EVENT_CHAT_ALREADY_EXISTS")

    room = ChatRoom(club_id=event.club_id, event_id=event_id, name=event.title + " · Chat")
    db.add(room)
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
