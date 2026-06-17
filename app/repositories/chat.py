from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatRoom, ChatRoomBan
from app.models.club_member import ClubMember
from app.models.user import User


class ChatRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_room(self, room_id: uuid.UUID) -> ChatRoom | None:
        result = await self.db.execute(select(ChatRoom).where(ChatRoom.id == room_id))
        return result.scalar_one_or_none()

    async def list_rooms(self, club_id: uuid.UUID) -> list[ChatRoom]:
        result = await self.db.execute(select(ChatRoom).where(ChatRoom.club_id == club_id).distinct())
        return list(result.scalars().unique().all())

    async def get_room_by_name(self, club_id: uuid.UUID, name: str) -> ChatRoom | None:
        result = await self.db.execute(select(ChatRoom).where(ChatRoom.club_id == club_id, ChatRoom.name == name))
        return result.scalar_one_or_none()

    async def get_user_by_supabase_id(self, supabase_user_id: uuid.UUID) -> User | None:
        result = await self.db.execute(select(User).where(User.supabase_user_id == supabase_user_id))
        return result.scalar_one_or_none()

    async def get_membership(self, club_id: uuid.UUID, user_id: uuid.UUID) -> ClubMember | None:
        result = await self.db.execute(
            select(ClubMember).where(ClubMember.club_id == club_id, ClubMember.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_active_ban(self, room_id: uuid.UUID, user_id: uuid.UUID, now: datetime) -> ChatRoomBan | None:
        result = await self.db.execute(
            select(ChatRoomBan).where(
                ChatRoomBan.room_id == room_id,
                ChatRoomBan.user_id == user_id,
                (ChatRoomBan.banned_until.is_(None)) | (ChatRoomBan.banned_until > now),
            )
        )
        return result.scalar_one_or_none()

    async def get_message(self, message_id: uuid.UUID, room_id: uuid.UUID) -> ChatMessage | None:
        result = await self.db.execute(
            select(ChatMessage).where(ChatMessage.id == message_id, ChatMessage.room_id == room_id)
        )
        return result.scalar_one_or_none()

    async def get_message_timestamp(self, message_id: uuid.UUID) -> datetime | None:
        result = await self.db.execute(select(ChatMessage.timestamp).where(ChatMessage.id == message_id))
        return result.scalar_one_or_none()

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
        return list(result.tuples().all())

    async def list_messages_paginated(
        self,
        room_id: uuid.UUID,
        before_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[tuple[ChatMessage, str]]:
        query = (
            select(ChatMessage, User.display_name)
            .join(User, ChatMessage.sender_id == User.id)
            .where(ChatMessage.room_id == room_id)
        )
        if before_id is not None:
            cursor_ts_subq = select(ChatMessage.timestamp).where(ChatMessage.id == before_id).scalar_subquery()
            query = query.where(
                (ChatMessage.timestamp < cursor_ts_subq)
                | ((ChatMessage.timestamp == cursor_ts_subq) & (ChatMessage.id < before_id))
            )
        query = query.order_by(ChatMessage.timestamp.desc(), ChatMessage.id.desc()).limit(limit)
        result = await self.db.execute(query)
        return list(result.tuples().all())
