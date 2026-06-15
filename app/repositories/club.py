from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatRoom, ChatRoomBan
from app.models.club import Club
from app.models.club_ban import ClubBan
from app.models.club_join_request import ClubJoinRequest
from app.models.club_member import ClubMember
from app.models.event import Event, EventAttendee
from app.models.user import User

logger = structlog.get_logger(__name__)


class ClubRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, club_id: uuid.UUID) -> Club | None:
        result = await self.db.execute(select(Club).where(Club.id == club_id))
        return result.scalar_one_or_none()

    async def list_public_or_member(
        self,
        user_id: uuid.UUID | None = None,
        search: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> list[Club]:
        stmt = select(Club)
        if user_id is not None:
            member_club_ids = select(ClubMember.club_id).where(ClubMember.user_id == user_id)
            stmt = stmt.where(or_(Club.is_public.is_(True), Club.id.in_(member_club_ids)))
        else:
            stmt = stmt.where(Club.is_public.is_(True))
        if search:
            # MN-10: escape LIKE metacharacters to prevent injection via % and _
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            stmt = stmt.where(or_(Club.name.ilike(like, escape="\\"), Club.description.ilike(like, escape="\\")))
        stmt = stmt.offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_for_user(self, user_id: uuid.UUID, skip: int = 0, limit: int = 20) -> list[Club]:
        member_club_ids = select(ClubMember.club_id).where(ClubMember.user_id == user_id)
        result = await self.db.execute(
            select(Club)
            .where(or_(Club.id.in_(member_club_ids), Club.organizer_id == user_id))
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_members(self, club_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(ClubMember).where(ClubMember.club_id == club_id)
        )
        return result.scalar() or 0

    async def get_member_avatar_previews(self, club_id: uuid.UUID, limit: int = 5) -> list[str]:
        result = await self.db.execute(
            select(User.avatar_url)
            .join(ClubMember, ClubMember.user_id == User.id)
            .where(ClubMember.club_id == club_id)
            .limit(limit)
        )
        return [r for r in result.scalars() if r]

    async def is_banned(self, club_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        now = datetime.now(UTC)
        result = await self.db.execute(
            select(ClubBan).where(
                and_(
                    ClubBan.club_id == club_id,
                    ClubBan.user_id == user_id,
                    or_(ClubBan.expires_at.is_(None), ClubBan.expires_at > now),
                )
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_membership(self, club_id: uuid.UUID, user_id: uuid.UUID) -> ClubMember | None:
        result = await self.db.execute(
            select(ClubMember).where(and_(ClubMember.club_id == club_id, ClubMember.user_id == user_id))
        )
        return result.scalar_one_or_none()

    def add_member(self, member: ClubMember) -> None:
        self.db.add(member)

    async def remove_member(self, club_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self.db.execute(
            delete(ClubMember).where(and_(ClubMember.club_id == club_id, ClubMember.user_id == user_id))
        )

    async def cascade_delete(self, club_id: uuid.UUID) -> None:
        room_ids_result = await self.db.execute(select(ChatRoom.id).where(ChatRoom.club_id == club_id))
        room_ids = list(room_ids_result.scalars().all())
        if room_ids:
            await self.db.execute(delete(ChatRoomBan).where(ChatRoomBan.room_id.in_(room_ids)))
            await self.db.execute(delete(ChatMessage).where(ChatMessage.room_id.in_(room_ids)))
            await self.db.execute(delete(ChatRoom).where(ChatRoom.club_id == club_id))
        else:
            logger.debug("delete_club_cascade: no chat rooms found for club", club_id=str(club_id))
        await self.db.execute(delete(ClubBan).where(ClubBan.club_id == club_id))
        await self.db.execute(delete(ClubJoinRequest).where(ClubJoinRequest.club_id == club_id))
        await self.db.execute(delete(ClubMember).where(ClubMember.club_id == club_id))

        event_ids_result = await self.db.execute(select(Event.id).where(Event.club_id == club_id))
        event_ids = list(event_ids_result.scalars().all())
        if event_ids:
            await self.db.execute(delete(EventAttendee).where(EventAttendee.event_id.in_(event_ids)))
            await self.db.execute(delete(Event).where(Event.club_id == club_id))
        else:
            logger.debug("delete_club_cascade: no events found for club", club_id=str(club_id))

        club_result = await self.db.execute(select(Club).where(Club.id == club_id))
        club = club_result.scalar_one_or_none()
        if club:
            await self.db.delete(club)
