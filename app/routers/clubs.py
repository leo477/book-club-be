from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import and_, delete, extract, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, get_optional_user, require_club_organizer
from app.exceptions import AppError
from app.models.club import Club
from app.models.club_member import ClubMember
from app.models.user import User
from app.schemas.clubs import (
    ClubResponse,
    ClubStatsResponse,
    CreateClubRequest,
    JoinClubResponse,
    RescheduleMeetingRequest,
    UpdateClubRequest,
)
from app.schemas.events import CreateEventRequest, EventResponse
from app.services.club_service import (
    build_club_response,
    build_club_responses_bulk,
    create_club_service,
    create_event_service,
    delete_club_cascade,
    get_club_or_404,
    request_join_club_service,
)
from app.services.event_service import build_event_responses_bulk

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/clubs", tags=["clubs"])


@router.get("")
async def list_clubs(
    current_user: Annotated[User | None, Depends(get_optional_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    search: str | None = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ClubResponse]:
    stmt = select(Club)

    if current_user is not None:
        member_club_ids = select(ClubMember.club_id).where(ClubMember.user_id == current_user.id)
        stmt = stmt.where(or_(Club.is_public.is_(True), Club.id.in_(member_club_ids)))
    else:
        stmt = stmt.where(Club.is_public.is_(True))

    if search:
        # MN-10: escape LIKE metacharacters to prevent injection via % and _
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        stmt = stmt.where(or_(Club.name.ilike(like, escape="\\"), Club.description.ilike(like, escape="\\")))

    stmt = stmt.offset(skip).limit(limit)

    result = await db.execute(stmt)
    clubs = result.scalars().all()
    return await build_club_responses_bulk(list(clubs), db)


@router.get("/my")
async def list_my_clubs(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ClubResponse]:
    member_club_ids = select(ClubMember.club_id).where(ClubMember.user_id == current_user.id)
    result = await db.execute(
        select(Club)
        .where(or_(Club.id.in_(member_club_ids), Club.organizer_id == current_user.id))
        .offset(skip)
        .limit(limit)
    )
    clubs = result.scalars().all()
    return await build_club_responses_bulk(list(clubs), db)


@router.get("/{club_id}")
async def get_club(
    club_id: uuid.UUID,
    _current_user: Annotated[User | None, Depends(get_optional_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ClubResponse:
    club = await get_club_or_404(club_id, db)
    return await build_club_response(club, db)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_club(
    body: CreateClubRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ClubResponse:
    return await create_club_service(body, current_user, db)


@router.patch("/{club_id}")
async def update_club(
    club_id: uuid.UUID,
    body: UpdateClubRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ClubResponse:
    await require_club_organizer(club_id, current_user, db)
    club = await get_club_or_404(club_id, db)

    # M-8: true PATCH — only update fields actually provided in the request body
    field_map = {
        "name": "name",
        "description": "description",
        "isPublic": "is_public",
        "city": "city",
        "coverUrl": "cover_url",
    }
    for schema_field, model_attr in field_map.items():
        if schema_field in body.model_fields_set:
            setattr(club, model_attr, getattr(body, schema_field))

    await db.commit()
    await db.refresh(club)
    return await build_club_response(club, db)


@router.patch("/{club_id}/pause")
async def pause_club(
    club_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ClubResponse:
    await require_club_organizer(club_id, current_user, db)
    club = await get_club_or_404(club_id, db)
    club.status = "paused"
    await db.commit()
    await db.refresh(club)
    return await build_club_response(club, db)


@router.patch("/{club_id}/cancel")
async def cancel_club(
    club_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ClubResponse:
    await require_club_organizer(club_id, current_user, db)
    club = await get_club_or_404(club_id, db)
    club.status = "cancelled"
    club.cancelled_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(club)
    return await build_club_response(club, db)


@router.patch("/{club_id}/reschedule")
async def reschedule_club(
    club_id: uuid.UUID,
    body: RescheduleMeetingRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ClubResponse:
    await require_club_organizer(club_id, current_user, db)
    club = await get_club_or_404(club_id, db)
    club.next_meeting_date = body.newDate
    club.status = "active"
    await db.commit()
    await db.refresh(club)
    return await build_club_response(club, db)


@router.delete("/{club_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_club(
    club_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> None:
    await require_club_organizer(club_id, current_user, db)
    await delete_club_cascade(club_id, db)


@router.post("/{club_id}/join")
async def join_club(
    club_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> JoinClubResponse:
    log = logger.bind(club_id=str(club_id), user_id=str(current_user.id))
    try:
        join_status = await request_join_club_service(club_id, current_user, db)
    except AppError:
        # Already a structured response (404/403/409) — let it propagate.
        raise
    except SQLAlchemyError as exc:
        log.exception("join_club database error")
        try:
            await db.rollback()
        except SQLAlchemyError:
            log.exception("join_club rollback failed after database error")
        # C1: statement_timeout fires as OperationalError; surface it as a
        # bounded 503 rather than an unhandled 500.
        raise AppError(503, "Database error while joining club", "JOIN_DB_ERROR") from exc

    log.info("join_club succeeded", join_status=join_status)
    return JoinClubResponse(status=join_status)


@router.delete("/{club_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_club(
    club_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> None:
    await get_club_or_404(club_id, db)

    existing = await db.execute(
        select(ClubMember).where(and_(ClubMember.club_id == club_id, ClubMember.user_id == current_user.id))
    )
    member = existing.scalar_one_or_none()
    if not member:
        raise AppError(409, "Not a member", "NOT_A_MEMBER")

    await db.execute(
        delete(ClubMember).where(and_(ClubMember.club_id == club_id, ClubMember.user_id == current_user.id))
    )
    await db.commit()


@router.get("/{club_id}/events")
async def list_club_events(
    club_id: uuid.UUID,
    current_user: Annotated[User | None, Depends(get_optional_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    include_past: bool = False,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[EventResponse]:
    from app.models.event import Event

    club = await get_club_or_404(club_id, db)
    stmt = select(Event).where(Event.club_id == club_id)

    if not include_past:
        stmt = stmt.where(Event.date >= datetime.now(tz=UTC))

    stmt = stmt.order_by(Event.date.asc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    events = result.scalars().all()

    current_user_id = current_user.id if current_user else None
    return await build_event_responses_bulk(
        list(events), db, current_user_id, club_name=club.name, organizer_id=club.organizer_id
    )


@router.post("/{club_id}/events", status_code=status.HTTP_201_CREATED)
async def create_event(
    club_id: uuid.UUID,
    body: CreateEventRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> EventResponse:
    return await create_event_service(body, club_id, current_user, db)


@router.get("/{club_id}/stats")
async def get_club_stats(
    club_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _auth: Annotated[None, Depends(require_club_organizer)],
) -> ClubStatsResponse:
    from app.models.chat import ChatMessage, ChatRoom, ChatRoomBan
    from app.models.event import Event, EventAttendee
    from app.models.quiz import Quiz, QuizAttempt
    from app.schemas.clubs import EventAttendanceStat, MemberStatRow, MonthlyStatRow

    await get_club_or_404(club_id, db)

    top_active_result = await db.execute(
        select(User.id, User.display_name, User.avatar_url, func.count(EventAttendee.event_id).label("cnt"))
        .join(EventAttendee, EventAttendee.user_id == User.id)
        .join(Event, Event.id == EventAttendee.event_id)
        .where(Event.club_id == club_id)
        .group_by(User.id, User.display_name, User.avatar_url)
        .order_by(func.count(EventAttendee.event_id).desc())
        .limit(3)
    )
    top_active = [
        MemberStatRow(userId=str(r.id), displayName=r.display_name, avatarUrl=r.avatar_url, count=r.cnt)
        for r in top_active_result.all()
    ]

    top_winners_result = await db.execute(
        select(User.id, User.display_name, User.avatar_url, func.count(QuizAttempt.id).label("cnt"))
        .join(QuizAttempt, QuizAttempt.user_id == User.id)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .where(Quiz.club_id == club_id, QuizAttempt.score == QuizAttempt.total)
        .group_by(User.id, User.display_name, User.avatar_url)
        .order_by(func.count(QuizAttempt.id).desc())
        .limit(3)
    )
    top_winners = [
        MemberStatRow(userId=str(r.id), displayName=r.display_name, avatarUrl=r.avatar_url, count=r.cnt)
        for r in top_winners_result.all()
    ]

    attendee_count_sq = (
        select(func.count()).where(EventAttendee.event_id == Event.id).correlate(Event).scalar_subquery()
    )
    recent_result = await db.execute(
        select(Event.id, Event.title, Event.date, attendee_count_sq.label("attendee_count"))
        .where(Event.club_id == club_id, Event.status == "held")
        .order_by(Event.date.desc())
        .limit(10)
    )
    recent_attendance = [
        EventAttendanceStat(eventId=str(r.id), title=r.title, date=r.date, attendeeCount=r.attendee_count)
        for r in recent_result.all()
    ]

    # ── Feature 7: extended statistics ───────────────────────────────────────

    club_room_ids_sq = select(ChatRoom.id).where(ChatRoom.club_id == club_id).scalar_subquery()
    six_months_ago = datetime.now(UTC) - timedelta(days=183)
    now = datetime.now(UTC)

    (
        total_members_raw,
        total_events_raw,
        total_messages_raw,
        banned_users_count_raw,
        upcoming_events_count_raw,
    ) = await asyncio.gather(
        db.scalar(select(func.count(ClubMember.user_id)).where(ClubMember.club_id == club_id)),
        db.scalar(select(func.count(Event.id)).where(Event.club_id == club_id)),
        db.scalar(select(func.count(ChatMessage.id)).where(ChatMessage.room_id.in_(club_room_ids_sq))),
        db.scalar(
            select(func.count(ChatRoomBan.id))
            .join(ChatRoom, ChatRoom.id == ChatRoomBan.room_id)
            .where(
                ChatRoom.club_id == club_id,
                or_(
                    ChatRoomBan.banned_until.is_(None),
                    ChatRoomBan.banned_until > now,
                ),
            )
        ),
        db.scalar(
            select(func.count(Event.id)).where(
                Event.club_id == club_id,
                or_(
                    Event.status == "upcoming",
                    and_(
                        Event.date > now,
                        Event.status.in_(["scheduled", "active", "rescheduled"]),
                    ),
                ),
            )
        ),
    )

    total_members: int = total_members_raw or 0
    total_events: int = total_events_raw or 0
    total_messages: int = total_messages_raw or 0
    banned_users_count: int = banned_users_count_raw or 0
    upcoming_events_count: int = upcoming_events_count_raw or 0

    # Member growth: new members per calendar month for the last 6 months.
    _yr_m = extract("year", ClubMember.joined_at).label("yr")
    _mo_m = extract("month", ClubMember.joined_at).label("mo")
    growth_rows = await db.execute(
        select(_yr_m, _mo_m, func.count(ClubMember.user_id).label("cnt"))
        .where(ClubMember.club_id == club_id, ClubMember.joined_at >= six_months_ago)
        .group_by(_yr_m, _mo_m)
        .order_by(_yr_m, _mo_m)
    )
    member_growth = [MonthlyStatRow(month=f"{int(r.yr):04d}-{int(r.mo):02d}", count=r.cnt) for r in growth_rows.all()]

    # Event frequency: events per calendar month for the last 6 months.
    _yr_e = extract("year", Event.date).label("yr")
    _mo_e = extract("month", Event.date).label("mo")
    event_freq_rows = await db.execute(
        select(_yr_e, _mo_e, func.count(Event.id).label("cnt"))
        .where(Event.club_id == club_id, Event.date >= six_months_ago)
        .group_by(_yr_e, _mo_e)
        .order_by(_yr_e, _mo_e)
    )
    event_frequency = [
        MonthlyStatRow(month=f"{int(r.yr):04d}-{int(r.mo):02d}", count=r.cnt) for r in event_freq_rows.all()
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
