from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatRoom
from app.models.user import User


class ChatRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_room(self, room_id: uuid.UUID) -> ChatRoom | None:
        result = await self.db.execute(select(ChatRoom).where(ChatRoom.id == room_id))
        return result.scalar_one_or_none()

    async def list_rooms(self, club_id: uuid.UUID) -> list[ChatRoom]:
        result = await self.db.execute(select(ChatRoom).where(ChatRoom.club_id == club_id))
        return list(result.scalars().all())

    async def get_message(self, message_id: uuid.UUID, room_id: uuid.UUID) -> ChatMessage | None:
        result = await self.db.execute(
            select(ChatMessage).where(ChatMessage.id == message_id, ChatMessage.room_id == room_id)
        )
        return result.scalar_one_or_none()

    async def get_message_timestamp(self, message_id: uuid.UUID) -> datetime | None:
        result = await self.db.execute(select(ChatMessage.timestamp).where(ChatMessage.id == message_id))
        return result.scalar_one_or_none()  # type: ignore[return-value]

    async def list_messages(
        self,
        room_id: uuid.UUID,
        before_ts: datetime | None = None,
        limit: int = 50,
    ) -> list[tuple[ChatMessage, str]]:
        query = (
            select(ChatMessage, User.display_name)
            .join(User, ChatMessage.sender_id == User.id)
            .where(ChatMessage.room_id == room_id)
        )
        if before_ts is not None:
            query = query.where(ChatMessage.timestamp < before_ts)
        query = query.order_by(ChatMessage.timestamp.desc()).limit(limit)
        result = await self.db.execute(query)
        return list(result.all())
