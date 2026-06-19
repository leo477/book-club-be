from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.event import Event, EventAttendee
from app.models.user import User
from app.repositories import EventRepository
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
    event = await EventRepository(db).get_by_id(event_id)
    if not event:
        raise AppError(404, "Event not found", "EVENT_NOT_FOUND")
    return event


async def _resolve_join_request_status(
    repo: EventRepository,
    event: Event,
    current_user: User,
    db: AsyncSession,
) -> Literal["none", "pending", "member"]:
    """Auto-request club membership when attending an event, mapping the result to a status."""
    if await repo.get_membership_id(event.club_id, current_user.id) is not None:
        return "member"

    from app.services.club_service import request_join_club_service

    try:
        result = await request_join_club_service(event.club_id, current_user, db, source="event")
    except AppError as exc:
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
        if code == "CLUB_BANNED":
            return "none"
        raise
    return "pending" if result in ("pending", "already_requested") else "none"


async def attend_event_service(
    event_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> AttendEventResponse:
    repo = EventRepository(db)
    event = await get_event_or_404(event_id, db)

    if event.status == "cancelled":
        raise AppError(http_status.HTTP_400_BAD_REQUEST, "Cannot attend a cancelled event", "EVENT_CANCELLED")

    event_date = event.date if event.date.tzinfo is not None else event.date.replace(tzinfo=UTC)
    if event_date - datetime.now(tz=UTC) < timedelta(days=3):
        raise AppError(http_status.HTTP_400_BAD_REQUEST, "Registration closed", "REGISTRATION_CLOSED")

    if await repo.get_attendance(event_id, current_user.id) is not None:
        raise AppError(http_status.HTTP_409_CONFLICT, "Already attending", "ALREADY_ATTENDING")

    repo.add_attendee(EventAttendee(event_id=event_id, user_id=current_user.id))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppError(http_status.HTTP_409_CONFLICT, "Already attending", "ALREADY_ATTENDING") from exc

    attendee_count = await repo.count_attendees(event_id)
    join_request_status = await _resolve_join_request_status(repo, event, current_user, db)

    return AttendEventResponse(attendeeCount=attendee_count, joinRequestStatus=join_request_status)


async def build_event_response(
    event: Event,
    db: AsyncSession,
    current_user_id: uuid.UUID | None = None,
    club_name: str | None = None,
    organizer_id: uuid.UUID | None = None,
) -> EventResponse:
    repo = EventRepository(db)
    attendee_count = await repo.count_attendees(event.id)

    is_attending = False
    if current_user_id is not None:
        is_attending = await repo.get_attendance(event.id, current_user_id) is not None

    # Load club info if not provided
    if club_name is None or organizer_id is None:
        club = await repo.get_club_for_event(event)
        if club:
            club_name = club_name or club.name
            organizer_id = organizer_id or club.organizer_id

    return _assemble_event_response(event, attendee_count, is_attending, club_name or "", organizer_id)


async def cancel_attendance_service(
    event_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> None:
    repo = EventRepository(db)
    await get_event_or_404(event_id, db)
    await repo.remove_attendee(event_id, current_user.id)
    await db.commit()


async def set_event_winner_service(
    event_id: uuid.UUID,
    winner_id: uuid.UUID,
    current_user_id: uuid.UUID,
    db: AsyncSession,
) -> EventResponse:
    repo = EventRepository(db)
    event = await get_event_or_404(event_id, db)
    if event.status != "held":
        raise AppError(400, "Event must be held to set a winner", "EVENT_NOT_HELD")

    if await repo.get_attendance(event_id, winner_id) is None:
        raise AppError(400, "Winner must be an attendee of this event", "WINNER_NOT_ATTENDEE")

    winner = await repo.get_user(winner_id)
    if not winner:
        raise AppError(404, "User not found", "USER_NOT_FOUND")

    event.has_winner = True
    event.winner_id = winner_id
    event.winner_name = winner.display_name
    await db.commit()
    await db.refresh(event)
    return await build_event_response(event, db, current_user_id)
