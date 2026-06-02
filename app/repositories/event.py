from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.club import Club
from app.models.club_member import ClubMember
from app.models.event import Event, EventAttendee


class EventRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, event_id: uuid.UUID) -> Event | None:
        result = await self.db.execute(select(Event).where(Event.id == event_id))
        return result.scalar_one_or_none()

    async def list_upcoming(
        self,
        now: datetime,
        city: str | None = None,
        club_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> list[Event]:
        stmt = select(Event).where(
            and_(
                Event.date >= now,
                Event.status.in_(["scheduled", "active"]),
            )
        )
        if city:
            stmt = stmt.where(Event.city.ilike(f"%{city}%"))
        if club_id:
            stmt = stmt.where(Event.club_id == club_id)
        stmt = stmt.order_by(Event.date.asc()).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_for_member(
        self,
        user_id: uuid.UUID,
        now: datetime,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Event]:
        member_club_ids = select(ClubMember.club_id).where(ClubMember.user_id == user_id)
        stmt = (
            select(Event)
            .where(
                and_(
                    Event.club_id.in_(member_club_ids),
                    Event.date >= now,
                    Event.status.in_(["scheduled", "active"]),
                )
            )
            .order_by(Event.date.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_for_club(
        self,
        club_id: uuid.UUID,
        upcoming_only: bool = False,
        now: datetime | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> list[Event]:
        stmt = select(Event).where(Event.club_id == club_id)
        if upcoming_only and now is not None:
            stmt = stmt.where(Event.date >= now)
        stmt = stmt.order_by(Event.date.asc()).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_attendees(self, event_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(EventAttendee).where(EventAttendee.event_id == event_id)
        )
        return result.scalar() or 0

    async def get_attendance(self, event_id: uuid.UUID, user_id: uuid.UUID) -> EventAttendee | None:
        result = await self.db.execute(
            select(EventAttendee).where(
                EventAttendee.event_id == event_id,
                EventAttendee.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    def add_attendee(self, attendee: EventAttendee) -> None:
        self.db.add(attendee)

    async def remove_attendee(self, event_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self.db.execute(
            delete(EventAttendee).where(and_(EventAttendee.event_id == event_id, EventAttendee.user_id == user_id))
        )

    async def get_club_for_event(self, event: Event) -> Club | None:
        result = await self.db.execute(select(Club).where(Club.id == event.club_id))
        return result.scalar_one_or_none()
