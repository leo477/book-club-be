from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.club import Club
from app.models.club_member import ClubMember
from app.models.user import User


class AfterMeetingVenueSchema(BaseModel):
    name: str
    address: str
    description: str | None = None
    lat: float | None = None
    lng: float | None = None


class CurrentBookSchema(BaseModel):
    title: str
    author: str
    description: str


class MeetingHistoryItemSchema(BaseModel):
    id: str
    date: str
    status: Literal["held", "cancelled", "rescheduled"]
    notes: str | None = None


class ClubResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    coverUrl: str | None
    organizerId: str
    isPublic: bool
    memberCount: int
    createdAt: str
    city: str
    nextMeetingDate: str | None
    address: str | None
    lat: float | None
    lng: float | None
    theme: str | None
    currentBook: CurrentBookSchema | None = None
    memberPreviews: list[str] = []
    status: str
    cancelledAt: str | None = None
    tags: list[str] = []
    meetingDurationMinutes: int | None
    afterMeetingVenue: AfterMeetingVenueSchema | None
    meetingHistory: list[MeetingHistoryItemSchema] = []


class CreateClubRequest(BaseModel):
    name: str
    description: str | None = None
    isPublic: bool = True
    city: str
    tags: list[str] = []
    meetingDurationMinutes: int | None = None
    afterMeetingVenue: AfterMeetingVenueSchema | None = None


class RescheduleRequest(BaseModel):
    newDate: str


class BanRequest(BaseModel):
    duration: Literal[1, 3, 5, "permanent"]


class BanResponse(BaseModel):
    userId: str
    clubId: str
    bannedAt: str
    duration: str
    bannedBy: str


class MemberResponse(BaseModel):
    userId: str
    displayName: str
    avatarUrl: str | None
    role: str
    socials: dict[str, str] | None
    socialsPublic: bool


async def build_club_response(club: Club, db: AsyncSession) -> ClubResponse:
    count_result = await db.execute(select(func.count()).select_from(ClubMember).where(ClubMember.club_id == club.id))
    member_count = count_result.scalar() or 0

    members_result = await db.execute(
        select(User.avatar_url)
        .join(ClubMember, ClubMember.user_id == User.id)
        .where(ClubMember.club_id == club.id)
        .limit(5)
    )
    previews = [r for r in members_result.scalars() if r]

    return ClubResponse(
        id=str(club.id),
        name=club.name,
        description=club.description,
        coverUrl=club.cover_url,
        organizerId=str(club.organizer_id),
        isPublic=club.is_public,
        memberCount=member_count,
        createdAt=club.created_at.isoformat() if club.created_at else "",
        city=club.city,
        nextMeetingDate=club.next_meeting_date.isoformat() if club.next_meeting_date else None,
        address=club.address,
        lat=club.lat,
        lng=club.lng,
        theme=club.theme,
        memberPreviews=previews,
        status=club.status,
        cancelledAt=club.cancelled_at.isoformat() if club.cancelled_at else None,
        tags=club.tags or [],
        meetingDurationMinutes=club.meeting_duration_minutes,
        afterMeetingVenue=AfterMeetingVenueSchema(**club.after_meeting_venue) if club.after_meeting_venue else None,
        meetingHistory=[],
        currentBook=None,
    )
