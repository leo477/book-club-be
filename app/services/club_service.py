from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.club import Club
from app.models.club_member import ClubMember
from app.models.quiz import QuizAttempt
from app.models.user import User
from app.schemas.clubs import AfterMeetingVenueSchema, ClubResponse
from app.schemas.users import UserStatsResponse


async def get_club_or_404(club_id: uuid.UUID, db: AsyncSession) -> Club:
    result = await db.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")
    return club


async def get_user_stats(user_id: uuid.UUID, db: AsyncSession) -> UserStatsResponse:
    clubs_result = await db.execute(select(func.count()).select_from(ClubMember).where(ClubMember.user_id == user_id))
    quizzes_result = await db.execute(
        select(func.count()).select_from(QuizAttempt).where(QuizAttempt.user_id == user_id)
    )
    wins_result = await db.execute(
        select(func.count())
        .select_from(QuizAttempt)
        .where(QuizAttempt.user_id == user_id, QuizAttempt.score == QuizAttempt.total)
    )
    return UserStatsResponse(
        clubsJoined=clubs_result.scalar() or 0,
        quizzesTaken=quizzes_result.scalar() or 0,
        quizWins=wins_result.scalar() or 0,
        likesReceived=0,
        booksRead=0,
    )


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
