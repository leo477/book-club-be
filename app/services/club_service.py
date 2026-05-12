from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.club import Club
from app.models.club_member import ClubMember
from app.models.quiz import QuizAttempt
from app.models.user import User
from app.schemas.clubs import ClubResponse
from app.schemas.events import AfterMeetingVenueSchema
from app.schemas.users import UserStatsResponse


def _assemble_club_response(club: Club, member_count: int, previews: list[str]) -> ClubResponse:
    after_meeting_venue = None
    if club.after_meeting_venue:
        after_meeting_venue = AfterMeetingVenueSchema(**club.after_meeting_venue)
    return ClubResponse(
        id=str(club.id),
        name=club.name,
        description=club.description,
        coverUrl=club.cover_url,
        organizerId=str(club.organizer_id),
        isPublic=club.is_public,
        memberCount=member_count,
        memberPreviews=previews,
        createdAt=club.created_at.isoformat() if club.created_at else "",
        status=club.status,
        city=club.city,
        nextMeetingDate=club.next_meeting_date.isoformat() if club.next_meeting_date else None,
        address=club.address,
        lat=club.lat,
        lng=club.lng,
        theme=club.theme,
        currentBook=club.current_book,
        tags=club.tags or [],
        meetingDurationMinutes=club.meeting_duration_minutes,
        afterMeetingVenue=after_meeting_venue,
        cancelledAt=club.cancelled_at.isoformat() if club.cancelled_at else None,
    )


async def build_club_responses_bulk(clubs: list[Club], db: AsyncSession) -> list[ClubResponse]:
    """Build ClubResponse objects for a list of clubs using bulk queries (2 queries total)."""
    if not clubs:
        return []

    club_ids = [c.id for c in clubs]

    counts_result = await db.execute(
        select(ClubMember.club_id, func.count().label("cnt"))
        .where(ClubMember.club_id.in_(club_ids))
        .group_by(ClubMember.club_id)
    )
    member_counts: dict[uuid.UUID, int] = {row.club_id: row.cnt for row in counts_result}

    previews_result = await db.execute(
        select(ClubMember.club_id, User.avatar_url)
        .join(User, ClubMember.user_id == User.id)
        .where(ClubMember.club_id.in_(club_ids), User.avatar_url.isnot(None))
    )
    previews_map: dict[uuid.UUID, list[str]] = {}
    for row in previews_result:
        lst = previews_map.setdefault(row.club_id, [])
        if len(lst) < 5:
            lst.append(row.avatar_url)

    return [
        _assemble_club_response(club, member_counts.get(club.id, 0), previews_map.get(club.id, [])) for club in clubs
    ]


async def get_club_or_404(club_id: uuid.UUID, db: AsyncSession) -> Club:
    result = await db.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()
    if not club:
        raise AppError(404, "Club not found", "CLUB_NOT_FOUND")
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
    return _assemble_club_response(club, member_count, previews)
