from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event, EventAttendee
from app.schemas.events import AfterMeetingVenueSchema, EventResponse


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
        afterMeetingVenue=(
            AfterMeetingVenueSchema(**event.after_meeting_venue) if event.after_meeting_venue else None
        ),
        attendeeCount=attendee_count,
        isAttending=is_attending,
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


async def get_event_or_404(event_id: uuid.UUID, db: AsyncSession) -> Event:
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


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
