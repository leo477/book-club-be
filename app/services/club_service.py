from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.chat import ChatRoom
from app.models.club import Club
from app.models.club_ban import ClubBan
from app.models.club_join_request import ClubJoinRequest
from app.models.club_member import ClubMember
from app.models.quiz import QuizAttempt
from app.models.user import User
from app.repositories import ClubRepository
from app.schemas.clubs import (
    BanRequest,
    BanResponse,
    ChampionInfo,
    ClubResponse,
    ClubStatsResponse,
    CreateClubRequest,
    EventAttendanceStat,
    JoinRequestResponse,
    MemberStatRow,
    MonthlyStatRow,
    MyMembershipResponse,
)
from app.schemas.events import AfterMeetingVenueSchema, CreateEventRequest, EventResponse
from app.schemas.users import UserStatsResponse

logger = structlog.get_logger(__name__)


async def delete_club_cascade(club_id: uuid.UUID, db: AsyncSession) -> None:
    repo = ClubRepository(db)
    await repo.cascade_delete(club_id)
    await db.commit()


async def get_club_or_404(club_id: uuid.UUID, db: AsyncSession) -> Club:
    repo = ClubRepository(db)
    club = await repo.get_by_id(club_id)
    if not club:
        raise AppError(404, "Club not found", "CLUB_NOT_FOUND")
    return club


async def get_user_stats(user_id: uuid.UUID, db: AsyncSession) -> UserStatsResponse:
    from sqlalchemy import case

    db.expire_all()
    clubs_result = await db.execute(select(func.count()).select_from(ClubMember).where(ClubMember.user_id == user_id))

    quiz_result = await db.execute(
        select(
            func.count().label("total"),
            func.count(case((QuizAttempt.score == QuizAttempt.total, 1))).label("wins"),
        )
        .select_from(QuizAttempt)
        .where(QuizAttempt.user_id == user_id)
    )
    quiz_row = quiz_result.one()

    return UserStatsResponse(
        clubsJoined=clubs_result.scalar() or 0,
        quizzesTaken=quiz_row.total or 0,
        quizWins=quiz_row.wins or 0,
        likesReceived=0,
        booksRead=0,
    )


async def _get_current_champion(club_id: uuid.UUID, db: AsyncSession) -> ChampionInfo | None:
    from app.models.event import Event

    result = await db.execute(
        select(Event)
        .where(
            Event.club_id == club_id,
            Event.status == "held",
            Event.has_winner.is_(True),
            Event.winner_id.isnot(None),
        )
        .order_by(Event.date.desc())
        .limit(1)
    )
    event = result.scalar_one_or_none()
    if not event:
        return None
    return ChampionInfo(
        userId=str(event.winner_id),
        displayName=event.winner_name or "",
        avatarUrl=None,
        wins=1,
        eventTitle=event.title,
    )


async def build_club_response(club: Club, db: AsyncSession) -> ClubResponse:
    repo = ClubRepository(db)
    member_count = await repo.count_members(club.id)
    previews = await repo.get_member_avatar_previews(club.id, limit=5)
    champion = await _get_current_champion(club.id, db)
    return _assemble_club_response(club, member_count, previews, champion)


async def build_club_responses_bulk(clubs: list[Club], db: AsyncSession) -> list[ClubResponse]:
    if not clubs:
        return []

    club_ids = [club.id for club in clubs]

    # 1. Отримуємо кількість учасників для всіх клубів одним запитом
    counts_result = await db.execute(
        select(ClubMember.club_id, func.count(ClubMember.user_id))
        .where(ClubMember.club_id.in_(club_ids))
        .group_by(ClubMember.club_id)
    )
    counts_map: dict[uuid.UUID, int] = {row[0]: row[1] for row in counts_result.all()}

    subq = (
        select(
            ClubMember.club_id,
            User.avatar_url,
            func.row_number()
            .over(
                partition_by=ClubMember.club_id,
                order_by=ClubMember.joined_at.desc(),
            )
            .label("rn"),
        )
        .join(User, User.id == ClubMember.user_id)
        .where(ClubMember.club_id.in_(club_ids))
        .subquery()
    )

    previews_result = await db.execute(select(subq.c.club_id, subq.c.avatar_url).where(subq.c.rn <= 5))

    previews_map: dict[uuid.UUID, list[str]] = {}
    for row in previews_result.all():
        cid, url = row
        if url:
            previews_map.setdefault(cid, []).append(url)

    from app.models.event import Event

    # Bulk-load current champions: one latest held+winner event per club
    subq_rn = (
        select(
            Event.id,
            Event.club_id,
            Event.winner_id,
            Event.winner_name,
            Event.title,
            Event.date,
            func.row_number().over(partition_by=Event.club_id, order_by=Event.date.desc()).label("rn"),
        )
        .where(
            Event.club_id.in_(club_ids),
            Event.status == "held",
            Event.has_winner.is_(True),
            Event.winner_id.isnot(None),
        )
        .subquery()
    )
    champions_result = await db.execute(
        select(
            subq_rn.c.club_id,
            subq_rn.c.winner_id,
            subq_rn.c.winner_name,
            subq_rn.c.title,
            subq_rn.c.date,
        ).where(subq_rn.c.rn == 1)
    )
    champion_map: dict[uuid.UUID, ChampionInfo] = {}
    for row in champions_result.all():
        champion_map[row.club_id] = ChampionInfo(
            userId=str(row.winner_id),
            displayName=row.winner_name or "",
            avatarUrl=None,
            wins=1,
            eventTitle=row.title,
        )

    responses = []
    for club in clubs:
        responses.append(
            _assemble_club_response(
                club=club,
                member_count=counts_map.get(club.id, 0),
                previews=previews_map.get(club.id, []),
                champion=champion_map.get(club.id),
            )
        )

    return responses


# ---------------------------------------------------------------------------
# Service-layer business logic (extracted from routers)
# ---------------------------------------------------------------------------

# C1: hard cap for the join handler — mirrors the constant in clubs.py router.
_JOIN_DB_TIMEOUT_SECONDS = 8.0


async def list_clubs_service(
    current_user: User | None,
    db: AsyncSession,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> list[ClubResponse]:
    repo = ClubRepository(db)
    user_id = current_user.id if current_user else None
    clubs = await repo.list_public_or_member(user_id, search, skip, limit)
    return await build_club_responses_bulk(clubs, db)


async def list_my_clubs_service(
    current_user: User,
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
) -> list[ClubResponse]:
    repo = ClubRepository(db)
    clubs = await repo.list_for_user(current_user.id, skip, limit)
    return await build_club_responses_bulk(clubs, db)


async def leave_club_service(
    club_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> None:
    await get_club_or_404(club_id, db)
    repo = ClubRepository(db)
    if await repo.get_membership(club_id, current_user.id) is None:
        raise AppError(409, "Not a member", "NOT_A_MEMBER")
    await repo.remove_member(club_id, current_user.id)
    await db.commit()


async def create_club_service(
    body: CreateClubRequest,
    current_user: User,
    db: AsyncSession,
) -> ClubResponse:
    """Create a club and add the organizer as the first member."""
    if current_user.role != "organizer":
        raise AppError(403, "Only organizers can create clubs", "FORBIDDEN")

    existing = await db.execute(select(Club).where(Club.organizer_id == current_user.id).limit(1))
    if existing.scalar_one_or_none() is not None:
        raise AppError(409, "You already own a club", "ORGANIZER_ALREADY_HAS_CLUB")

    club = Club(
        id=uuid.uuid4(),
        name=body.name,
        description=body.description,
        cover_url=body.coverUrl,
        is_public=body.isPublic,
        organizer_id=current_user.id,
        city=(body.city or "").strip() or None,
        tags=body.tags or [],
        meeting_duration_minutes=body.meetingDurationMinutes,
        after_meeting_venue=body.afterMeetingVenue.model_dump() if body.afterMeetingVenue else None,
        status="active",
    )
    db.add(club)
    await db.flush()

    membership = ClubMember(
        id=uuid.uuid4(),
        club_id=club.id,
        user_id=current_user.id,
        role="organizer",
    )
    db.add(membership)

    general_room = ChatRoom(
        id=uuid.uuid4(),
        club_id=club.id,
        name=f"{club.name} · General",
    )
    db.add(general_room)
    await db.commit()
    await db.refresh(club)
    return await build_club_response(club, db)


async def request_join_club_service(
    club_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
    source: str = "manual",
) -> Literal["pending", "already_requested", "member"]:
    """Create (or reactivate) a pending join request for the club.

    Returns one of "pending" | "already_requested" | "member".

    C1: a PostgreSQL statement_timeout is set at the start of the transaction
    so that any stalled DB call is aborted by the server (raises OperationalError)
    rather than hanging the Python coroutine.  This avoids the coroutine-
    cancellation hazard of asyncio.wait_for with a shared AsyncSession.
    """
    from sqlalchemy import text

    # C1: enforce an 8-second ceiling on every statement in this transaction.
    # SET LOCAL is PostgreSQL-specific; guard with a dialect check so tests
    # running against SQLite are unaffected.
    conn = await db.connection()
    if conn.dialect.name == "postgresql":
        timeout_ms = int(_JOIN_DB_TIMEOUT_SECONDS * 1000)
        await db.execute(text(f"SET LOCAL statement_timeout = '{timeout_ms}ms'"))

    await get_club_or_404(club_id, db)
    repo = ClubRepository(db)

    # M-7: check for active ban (respects expires_at; NULL = permanent)
    if await repo.is_banned(club_id, current_user.id):
        raise AppError(403, "You are banned from this club", "CLUB_BANNED")

    if await repo.get_membership(club_id, current_user.id) is not None:
        if source == "manual":
            raise AppError(409, "Already a member", "ALREADY_MEMBER")
        return "member"

    join_request = await repo.get_join_request(club_id, current_user.id)
    if join_request is not None:
        if join_request.status == "pending":
            return "already_requested"
        if join_request.status == "approved":
            return "member"
        # rejected -> flip back to pending
        join_request.status = "pending"
        join_request.source = source
        join_request.decided_by = None
        join_request.decided_at = None
        await db.commit()
        return "pending"

    db.add(
        ClubJoinRequest(
            id=uuid.uuid4(),
            club_id=club_id,
            user_id=current_user.id,
            status="pending",
            source=source,
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return "already_requested"
    return "pending"


async def list_join_requests_service(
    club_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
    skip: int,
    limit: int,
) -> list[JoinRequestResponse]:
    from app.dependencies import require_club_organizer

    await require_club_organizer(club_id, current_user, db)

    repo = ClubRepository(db)
    rows = await repo.list_pending_join_requests_with_users(club_id, skip, limit)
    return [
        JoinRequestResponse(
            userId=str(user.id),
            displayName=user.display_name,
            avatarUrl=user.avatar_url,
            status=req.status,
            source=req.source,
            createdAt=req.created_at.isoformat(),
        )
        for req, user in rows
    ]


async def approve_join_request_service(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> int:
    from app.dependencies import require_club_organizer

    await require_club_organizer(club_id, current_user, db)

    repo = ClubRepository(db)
    join_request = await repo.get_pending_join_request(club_id, user_id)
    if join_request is None:
        raise AppError(404, "Join request not found", "JOIN_REQUEST_NOT_FOUND")

    already_member = await repo.get_membership(club_id, user_id) is not None

    now_utc = datetime.now(UTC)
    join_request.status = "approved"
    join_request.decided_by = current_user.id
    join_request.decided_at = now_utc

    if not already_member:
        repo.add_member(ClubMember(id=uuid.uuid4(), club_id=club_id, user_id=user_id, role="member"))

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Member row already exists (race) — re-apply the approval without the insert.
        retry_request = await repo.get_join_request(club_id, user_id)
        if retry_request is not None:
            retry_request.status = "approved"
            retry_request.decided_by = current_user.id
            retry_request.decided_at = now_utc
            await db.commit()

    return await repo.count_members(club_id)


async def reject_join_request_service(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> None:
    from app.dependencies import require_club_organizer

    await require_club_organizer(club_id, current_user, db)

    repo = ClubRepository(db)
    join_request = await repo.get_pending_join_request(club_id, user_id)
    if join_request is None:
        raise AppError(404, "Join request not found", "JOIN_REQUEST_NOT_FOUND")

    join_request.status = "rejected"
    join_request.decided_by = current_user.id
    join_request.decided_at = datetime.now(UTC)
    await db.commit()


async def get_my_membership_service(
    club_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> MyMembershipResponse:
    await get_club_or_404(club_id, db)
    repo = ClubRepository(db)

    member = await repo.get_membership(club_id, current_user.id)
    if member is not None:
        return MyMembershipResponse(isMember=True, role=member.role)

    join_request = await repo.get_latest_join_request(club_id, current_user.id)
    if join_request is not None and join_request.status == "pending":
        return MyMembershipResponse(isMember=False, joinRequestStatus="pending")
    if join_request is not None and join_request.status == "rejected":
        return MyMembershipResponse(isMember=False, joinRequestStatus="rejected")
    return MyMembershipResponse(isMember=False)


async def ban_user_service(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    body: BanRequest,
    current_user: User,
    db: AsyncSession,
) -> BanResponse:
    """Ban a user from a club, removing their membership."""
    from app.dependencies import require_club_organizer

    await require_club_organizer(club_id, current_user, db)

    user_result = await db.execute(select(User).where(User.id == user_id))
    if not user_result.scalar_one_or_none():
        raise AppError(404, "User not found", "USER_NOT_FOUND")

    # M-7: compute expires_at from duration; None = permanent ban
    duration_days_map = {"1": 1, "3": 3, "5": 5}
    duration_str = str(body.duration)
    now_utc = datetime.now(UTC)
    expires_at = (
        now_utc + timedelta(days=duration_days_map[duration_str])
        if duration_str in duration_days_map
        else None  # "permanent"
    )

    ban = ClubBan(
        id=uuid.uuid4(),
        club_id=club_id,
        user_id=user_id,
        banned_by=current_user.id,
        duration=duration_str,
        expires_at=expires_at,
    )
    db.add(ban)

    repo = ClubRepository(db)
    await repo.remove_member(club_id, user_id)
    await db.commit()
    await db.refresh(ban)

    return BanResponse(
        userId=str(user_id),
        clubId=str(club_id),
        bannedAt=ban.banned_at.isoformat(),
        duration=str(body.duration),
        bannedBy=str(current_user.id),
    )


async def unban_user_service(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> None:
    from app.dependencies import require_club_organizer

    await require_club_organizer(club_id, current_user, db)

    result = await db.execute(select(ClubBan).where(ClubBan.club_id == club_id, ClubBan.user_id == user_id))
    bans = result.scalars().all()
    if not bans:
        raise AppError(404, "No active ban found for this user", "BAN_NOT_FOUND")

    for ban in bans:
        await db.delete(ban)
    await db.commit()


async def change_member_role_service(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    new_role: str,
    current_user: User,
    db: AsyncSession,
) -> ClubMember:
    from app.dependencies import require_club_organizer

    await require_club_organizer(club_id, current_user, db)
    club = await get_club_or_404(club_id, db)

    repo = ClubRepository(db)
    member = await repo.get_membership(club_id, user_id)
    if member is None:
        raise AppError(404, "Member not found", "MEMBER_NOT_FOUND")

    if member.role == new_role:
        return member

    if new_role == "member":
        if club.organizer_id == user_id:
            raise AppError(400, "Cannot demote the club owner", "CANNOT_DEMOTE_OWNER")
        organizer_count = await db.execute(
            select(func.count())
            .select_from(ClubMember)
            .where(ClubMember.club_id == club_id, ClubMember.role == "organizer")
        )
        if organizer_count.scalar_one() <= 1:
            raise AppError(400, "Cannot demote the last organizer", "LAST_ORGANIZER")

    member.role = new_role
    await db.commit()
    await db.refresh(member)
    return member


async def create_event_service(
    body: CreateEventRequest,
    club_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> EventResponse:
    """Create an event for a club (organizer only)."""
    from app.dependencies import require_club_organizer
    from app.models.event import Event, EventAttendee
    from app.services.chat_service import get_or_create_event_chat_room
    from app.services.event_service import build_event_response

    await require_club_organizer(club_id, current_user, db)
    club = await get_club_or_404(club_id, db)

    event = Event(
        id=uuid.uuid4(),
        club_id=club_id,
        title=body.title,
        description=body.description,
        date=body.date,
        city=body.city.strip(),
        address=body.address,
        lat=body.lat,
        lng=body.lng,
        theme=body.theme,
        tags=body.tags,
        duration_minutes=body.durationMinutes,
        after_meeting_venue=body.afterMeetingVenue.model_dump() if body.afterMeetingVenue else None,
        cover_url=body.coverUrl,
        book_title=body.bookTitle,
        google_book_id=body.googleBookId,
        status="scheduled",
    )
    db.add(event)
    db.add(EventAttendee(event_id=event.id, user_id=current_user.id))
    await db.flush()
    await get_or_create_event_chat_room(event, db)
    await db.commit()
    await db.refresh(event)
    return await build_event_response(event, db, current_user.id, club_name=club.name, organizer_id=club.organizer_id)


async def get_club_stats_service(club_id: uuid.UUID, db: AsyncSession) -> ClubStatsResponse:
    """Aggregate club analytics: top members, attendance, growth, frequency, counts."""
    await get_club_or_404(club_id, db)
    repo = ClubRepository(db)

    top_active = [
        MemberStatRow(userId=str(uid), displayName=name, avatarUrl=avatar, count=cnt)
        for uid, name, avatar, cnt in await repo.top_active_members(club_id)
    ]
    top_winners = [
        MemberStatRow(userId=str(uid), displayName=name, avatarUrl=avatar, count=cnt)
        for uid, name, avatar, cnt in await repo.top_quiz_winners(club_id)
    ]
    recent_attendance = [
        EventAttendanceStat(eventId=str(eid), title=title, date=date, attendeeCount=cnt)
        for eid, title, date, cnt in await repo.recent_held_events_with_attendance(club_id)
    ]

    now = datetime.now(UTC)
    six_months_ago = now - timedelta(days=183)

    total_members = await repo.count_members(club_id)
    total_events = await repo.count_events(club_id)
    total_messages = await repo.count_messages(club_id)
    banned_users_count = await repo.count_active_room_bans(club_id, now)
    upcoming_events_count = await repo.count_upcoming_events(club_id, now)

    member_growth = [
        MonthlyStatRow(month=f"{yr:04d}-{mo:02d}", count=cnt)
        for yr, mo, cnt in await repo.member_growth_by_month(club_id, six_months_ago)
    ]
    event_frequency = [
        MonthlyStatRow(month=f"{yr:04d}-{mo:02d}", count=cnt)
        for yr, mo, cnt in await repo.event_frequency_by_month(club_id, six_months_ago)
    ]

    return ClubStatsResponse(
        topActive=top_active,
        topWinners=top_winners,
        recentAttendance=recent_attendance,
        totalMembers=total_members,
        totalEvents=total_events,
        totalMessages=total_messages,
        memberGrowth=member_growth,
        eventFrequency=event_frequency,
        bannedUsersCount=banned_users_count,
        upcomingEventsCount=upcoming_events_count,
    )


def _assemble_club_response(
    club: Club, member_count: int, previews: list[str], champion: ChampionInfo | None = None
) -> ClubResponse:
    return ClubResponse(
        id=str(club.id),
        name=club.name,
        description=club.description,
        coverUrl=club.cover_url,
        organizerId=str(club.organizer_id),
        isPublic=club.is_public,
        memberCount=member_count,
        memberPreviews=previews,
        createdAt=club.created_at,
        status=club.status,
        city=club.city,
        nextMeetingDate=club.next_meeting_date,
        address=club.address,
        lat=club.lat,
        lng=club.lng,
        theme=club.theme,
        currentBook=club.current_book,
        tags=club.tags or [],
        meetingDurationMinutes=club.meeting_duration_minutes,
        afterMeetingVenue=AfterMeetingVenueSchema.model_validate(club.after_meeting_venue)
        if club.after_meeting_venue
        else None,
        cancelledAt=club.cancelled_at,
        currentChampion=champion,
    )
