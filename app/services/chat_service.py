import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.chat import ChatRoom, ChatRoomBan


async def get_room_or_404(room_id: uuid.UUID, db: AsyncSession) -> ChatRoom:
    room = await db.scalar(select(ChatRoom).where(ChatRoom.id == room_id))
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
