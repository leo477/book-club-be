from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.club import Club
from app.models.club_member import ClubMember
from app.models.user import User
from app.schemas.clubs import AfterMeetingVenueSchema, ClubResponse


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
