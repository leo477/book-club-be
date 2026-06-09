from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import status as http_status
from sqlalchemy import and_, func, select
from sqlalchemy import select as sa_select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.event import Event, EventAttendee
from app.models.user import User
from app.schemas.events import AfterMeetingVenueSchema, AttendEventResponse, EventResponse


def _assemble_event_response(
    event: Event,
    attendee_count: int,
    is_attending: bool,
    club_name: str,
    organizer_id: uuid.UUID | None,
) -> EventResponse:
    return EventResponse(
        id=str(event.id),
        clubId=str(event.club_id),
        clubName=club_name,
        organizerId=str(organizer_id) if organizer_id else "",
        title=event.title,
        description=event.description,
        date=event.date.isoformat() if event.date else "",
        city=event.city,
        address=event.address,
        lat=event.lat,
        lng=event.lng,
        status=event.status,
        cancelledAt=event.cancelled_at.isoformat() if event.cancelled_at else None,
        coverUrl=event.cover_url,
        bookTitle=event.book_title,
        theme=event.theme,
        tags=event.tags or [],
        durationMinutes=event.duration_minutes,
        afterMeetingVenue=(AfterMeetingVenueSchema(**event.after_meeting_venue) if event.after_meeting_venue else None),
        attendeeCount=attendee_count,
        isAttending=is_attending,
        hasWinner=event.has_winner,
        winnerId=str(event.winner_id) if event.winner_id else None,
        winnerName=event.winner_name,
        googleBookId=event.google_book_id,
    )


# ---------------------------------------------------------------------------
# Bulk helper (avoids N+1 for event list endpoints)
# ---------------------------------------------------------------------------


async def build_event_responses_bulk(
    events: list[Event],
    db: AsyncSession,
    current_user_id: uuid.UUID | None = None,
    club_name: str | None = None,
    organizer_id: uuid.UUID | None = None,
) -> list[EventResponse]:
    """Build EventResponse objects for a list of events using bulk queries."""
    if not events:
        return []

    event_ids = [e.id for e in events]

    # Bulk attendee counts
    counts_result = await db.execute(
        select(EventAttendee.event_id, func.count().label("cnt"))
        .where(EventAttendee.event_id.in_(event_ids))
        .group_by(EventAttendee.event_id)
    )
    attendee_counts: dict[uuid.UUID, int] = {row.event_id: row.cnt for row in counts_result}

    # Bulk is_attending flags for current user
    attending_set: set[uuid.UUID] = set()
    if current_user_id is not None:
        attending_result = await db.execute(
            select(EventAttendee.event_id).where(
                EventAttendee.event_id.in_(event_ids),
                EventAttendee.user_id == current_user_id,
            )
        )
        attending_set = {row.event_id for row in attending_result}

    # Bulk-load club info when not provided
    club_name_map: dict[uuid.UUID, str] = {}
    organizer_id_map: dict[uuid.UUID, uuid.UUID] = {}
    if club_name is None or organizer_id is None:
        from app.models.club import Club

        club_ids = list({e.club_id for e in events})
        clubs_result = await db.execute(select(Club).where(Club.id.in_(club_ids)))
        for club in clubs_result.scalars():
            club_name_map[club.id] = club.name
            organizer_id_map[club.id] = club.organizer_id

    return [
        _assemble_event_response(
            event,
            attendee_counts.get(event.id, 0),
            event.id in attending_set,
            club_name or club_name_map.get(event.club_id, ""),
            organizer_id or organizer_id_map.get(event.club_id),
        )
        for event in events
    ]


async def fetch_enriched_event_list(
    filter_clauses: list[Any],
    db: AsyncSession,
    current_user_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 20,
) -> list[EventResponse]:
    from sqlalchemy import literal

    from app.models.club import Club

    attendee_count_sq = (
        select(func.count()).where(EventAttendee.event_id == Event.id).correlate(Event).scalar_subquery()
    )

    if current_user_id is not None:
        is_attending_col = (
            select(func.count())
            .where(
                EventAttendee.event_id == Event.id,
                EventAttendee.user_id == current_user_id,
            )
            .correlate(Event)
            .scalar_subquery()
            > 0
        ).label("is_attending")
    else:
        is_attending_col = literal(False).label("is_attending")

    stmt = (
        select(
            Event,
            Club.name.label("club_name"),
            Club.organizer_id.label("organizer_id"),
            attendee_count_sq.label("attendee_count"),
            is_attending_col,
        )
        .join(Club, Event.club_id == Club.id)
        .where(*filter_clauses)
        .order_by(Event.date.asc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(stmt)
    return [
        _assemble_event_response(
            row.Event,
            row.attendee_count,
            bool(row.is_attending),
            row.club_name,
            row.organizer_id,
        )
        for row in result
    ]


async def get_event_or_404(event_id: uuid.UUID, db: AsyncSession) -> Event:
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise AppError(404, "Event not found", "EVENT_NOT_FOUND")
    return event


async def attend_event_service(
    event_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> AttendEventResponse:
    event = await get_event_or_404(event_id, db)

    if event.status == "cancelled":
        raise AppError(http_status.HTTP_400_BAD_REQUEST, "Cannot attend a cancelled event", "EVENT_CANCELLED")

    event_date = event.date if event.date.tzinfo is not None else event.date.replace(tzinfo=UTC)
    if event_date - datetime.now(tz=UTC) < timedelta(days=3):
        raise AppError(http_status.HTTP_400_BAD_REQUEST, "Registration closed", "REGISTRATION_CLOSED")

    existing = await db.execute(
        sa_select(EventAttendee).where(
            and_(EventAttendee.event_id == event_id, EventAttendee.user_id == current_user.id)
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise AppError(http_status.HTTP_409_CONFLICT, "Already attending", "ALREADY_ATTENDING")

    db.add(EventAttendee(event_id=event_id, user_id=current_user.id))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppError(http_status.HTTP_409_CONFLICT, "Already attending", "ALREADY_ATTENDING") from exc

    count_result = await db.execute(
        sa_select(func.count()).select_from(EventAttendee).where(EventAttendee.event_id == event_id)
    )
    attendee_count = count_result.scalar() or 0

    from app.models.club_member import ClubMember
    from app.services.club_service import request_join_club_service

    member_result = await db.execute(
        sa_select(ClubMember.id).where(and_(ClubMember.club_id == event.club_id, ClubMember.user_id == current_user.id))
    )
    join_request_status: Literal["none", "pending", "member"]
    if member_result.scalar_one_or_none() is not None:
        join_request_status = "member"
    else:
        try:
            result = await request_join_club_service(event.club_id, current_user, db, source="event")
            join_request_status = "pending" if result in ("pending", "already_requested") else "none"
        except AppError as exc:
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            if code == "CLUB_BANNED":
                join_request_status = "none"
            else:
                raise

    return AttendEventResponse(attendeeCount=attendee_count, joinRequestStatus=join_request_status)


async def build_event_response(
    event: Event,
    db: AsyncSession,
    current_user_id: uuid.UUID | None = None,
    club_name: str | None = None,
    organizer_id: uuid.UUID | None = None,
) -> EventResponse:
    count_result = await db.execute(
        select(func.count()).select_from(EventAttendee).where(EventAttendee.event_id == event.id)
    )
    attendee_count = count_result.scalar() or 0

    is_attending = False
    if current_user_id is not None:
        attending_result = await db.execute(
            select(EventAttendee).where(
                EventAttendee.event_id == event.id,
                EventAttendee.user_id == current_user_id,
            )
        )
        is_attending = attending_result.scalar_one_or_none() is not None

    # Load club info if not provided
    if club_name is None or organizer_id is None:
        from app.models.club import Club

        club_result = await db.execute(select(Club).where(Club.id == event.club_id))
        club = club_result.scalar_one_or_none()
        if club:
            club_name = club_name or club.name
            organizer_id = organizer_id or club.organizer_id

    return _assemble_event_response(event, attendee_count, is_attending, club_name or "", organizer_id)
