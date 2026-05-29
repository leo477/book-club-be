from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.chat import ChatMessage, ChatRoom, ChatRoomBan
from app.models.club import Club
from app.models.club_ban import ClubBan
from app.models.club_member import ClubMember
from app.models.quiz import QuizAttempt
from app.models.user import User
from app.schemas.clubs import BanRequest, BanResponse, ChampionInfo, ClubResponse, CreateClubRequest
from app.schemas.events import AfterMeetingVenueSchema, CreateEventRequest, EventResponse
from app.schemas.users import UserStatsResponse

if TYPE_CHECKING:
    pass


async def delete_club_cascade(club_id: uuid.UUID, db: AsyncSession) -> None:
    room_ids_result = await db.execute(select(ChatRoom.id).where(ChatRoom.club_id == club_id))
    room_ids = list(room_ids_result.scalars().all())
    if room_ids:
        await db.execute(sa_delete(ChatRoomBan).where(ChatRoomBan.room_id.in_(room_ids)))
        await db.execute(sa_delete(ChatMessage).where(ChatMessage.room_id.in_(room_ids)))
        await db.execute(sa_delete(ChatRoom).where(ChatRoom.club_id == club_id))
    await db.execute(sa_delete(ClubBan).where(ClubBan.club_id == club_id))
    await db.execute(sa_delete(ClubMember).where(ClubMember.club_id == club_id))

    from app.models.event import Event, EventAttendee

    event_ids_result = await db.execute(select(Event.id).where(Event.club_id == club_id))
    event_ids = list(event_ids_result.scalars().all())
    if event_ids:
        await db.execute(sa_delete(EventAttendee).where(EventAttendee.event_id.in_(event_ids)))
        await db.execute(sa_delete(Event).where(Event.club_id == club_id))

    club_result = await db.execute(select(Club).where(Club.id == club_id))
    club = club_result.scalar_one_or_none()
    if club:
        await db.delete(club)
    await db.commit()


async def get_club_or_404(club_id: uuid.UUID, db: AsyncSession) -> Club:
    result = await db.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()
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
    count_result = await db.execute(select(func.count()).select_from(ClubMember).where(ClubMember.club_id == club.id))
    member_count = count_result.scalar() or 0

    members_result = await db.execute(
        select(User.avatar_url)
        .join(ClubMember, ClubMember.user_id == User.id)
        .where(ClubMember.club_id == club.id)
        .limit(5)
    )
    previews = [r for r in members_result.scalars() if r]
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
        city=body.city,
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


async def join_club_service(
    club_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> int:
    """Perform the join transaction and return the new member count.

    C1: a PostgreSQL statement_timeout is set at the start of the transaction
    so that any stalled DB call is aborted by the server (raises OperationalError)
    rather than hanging the Python coroutine.  This avoids the coroutine-
    cancellation hazard of asyncio.wait_for with a shared AsyncSession.
    """
    from sqlalchemy import text

    # C1: enforce an 8-second ceiling on every statement in this transaction.
    # If any call stalls (lock contention, pooler issue), Postgres aborts it
    # and raises OperationalError — caught cleanly in join_club without
    # cancelling the coroutine or leaving the connection in an undefined state.
    # SET LOCAL is PostgreSQL-specific; guard with a dialect check so tests
    # running against SQLite are unaffected.
    conn = await db.connection()
    if conn.dialect.name == "postgresql":
        timeout_ms = int(_JOIN_DB_TIMEOUT_SECONDS * 1000)
        await db.execute(text(f"SET LOCAL statement_timeout = '{timeout_ms}ms'"))

    await get_club_or_404(club_id, db)

    # M-7: check for active ban (respects expires_at; NULL = permanent)
    ban_result = await db.execute(
        select(ClubBan).where(and_(ClubBan.club_id == club_id, ClubBan.user_id == current_user.id))
    )
    ban = ban_result.scalar_one_or_none()
    if ban is not None:
        now_utc = datetime.now(UTC)
        ban_active = ban.expires_at is None or ban.expires_at > now_utc
        if ban_active:
            raise AppError(403, "You are banned from this club", "CLUB_BANNED")

    existing = await db.execute(
        select(ClubMember.id).where(and_(ClubMember.club_id == club_id, ClubMember.user_id == current_user.id))
    )
    if existing.scalar_one_or_none():
        raise AppError(409, "Already a member", "ALREADY_MEMBER")

    membership = ClubMember(
        id=uuid.uuid4(),
        club_id=club_id,
        user_id=current_user.id,
        role="member",
    )
    db.add(membership)
    # M-5: guard against TOCTOU race — a concurrent join between the SELECT and INSERT
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppError(409, "Already a member", "ALREADY_MEMBER") from exc

    count_result = await db.execute(select(func.count()).select_from(ClubMember).where(ClubMember.club_id == club_id))
    return int(count_result.scalar() or 0)


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

    from sqlalchemy import delete as _delete

    await db.execute(_delete(ClubMember).where(and_(ClubMember.club_id == club_id, ClubMember.user_id == user_id)))
    await db.commit()
    await db.refresh(ban)

    return BanResponse(
        userId=str(user_id),
        clubId=str(club_id),
        bannedAt=ban.banned_at.isoformat(),
        duration=str(body.duration),
        bannedBy=str(current_user.id),
    )


async def create_event_service(
    body: CreateEventRequest,
    club_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> EventResponse:
    """Create an event for a club (organizer only)."""
    from app.dependencies import require_club_organizer
    from app.models.event import Event, EventAttendee
    from app.services.event_service import build_event_response

    await require_club_organizer(club_id, current_user, db)
    club = await get_club_or_404(club_id, db)

    event = Event(
        id=uuid.uuid4(),
        club_id=club_id,
        title=body.title,
        description=body.description,
        date=body.date,
        city=body.city,
        address=body.address,
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
    await db.commit()
    await db.refresh(event)
    return await build_event_response(event, db, current_user.id, club_name=club.name, organizer_id=club.organizer_id)


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
