from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, get_optional_user, require_event_club_organizer
from app.models.club_member import ClubMember
from app.models.event import Event, EventAttendee
from app.models.user import User
from app.schemas.events import AttendEventResponse, EventResponse, EventUpdatePayload, RescheduleEventRequest
from app.services.event_service import attend_event_service, build_event_response, fetch_enriched_event_list, get_event_or_404

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.get("")
async def list_events(
    current_user: Annotated[User | None, Depends(get_optional_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    city: str | None = None,
    club_id: uuid.UUID | None = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[EventResponse]:
    filters = [
        Event.date >= datetime.now(tz=UTC),
        Event.status.in_(["scheduled", "active"]),
    ]
    if city:
        filters.append(Event.city.ilike(f"%{city}%"))
    if club_id:
        filters.append(Event.club_id == club_id)
    current_user_id = current_user.id if current_user else None
    return await fetch_enriched_event_list(filters, db, current_user_id, skip, limit)


@router.get("/my")
async def list_my_events(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[EventResponse]:
    member_club_ids = select(ClubMember.club_id).where(ClubMember.user_id == current_user.id)
    filters = [
        Event.club_id.in_(member_club_ids),
        Event.date >= datetime.now(tz=UTC),
        Event.status.in_(["scheduled", "active"]),
    ]
    return await fetch_enriched_event_list(filters, db, current_user.id, skip, limit)


@router.get("/{event_id}")
async def get_event(
    event_id: uuid.UUID,
    current_user: Annotated[User | None, Depends(get_optional_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> EventResponse:
    event = await get_event_or_404(event_id, db)
    current_user_id = current_user.id if current_user else None
    return await build_event_response(event, db, current_user_id)


@router.post("/{event_id}/attend", status_code=status.HTTP_201_CREATED)
async def attend_event(
    event_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> AttendEventResponse:
    return await attend_event_service(event_id, current_user, db)


@router.delete("/{event_id}/attend", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_attendance(
    event_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> None:
    await get_event_or_404(event_id, db)
    await db.execute(
        delete(EventAttendee).where(and_(EventAttendee.event_id == event_id, EventAttendee.user_id == current_user.id))
    )
    await db.commit()


@router.patch("/{event_id}")
async def update_event(
    event_id: uuid.UUID,
    body: EventUpdatePayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _auth: Annotated[ClubMember, Depends(require_event_club_organizer)],
) -> EventResponse:
    event = await get_event_or_404(event_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "after_meeting_venue":
            if value is None:
                setattr(event, field, None)
            elif isinstance(value, dict):
                setattr(event, field, value)
            else:
                setattr(event, field, value.model_dump())
        else:
            setattr(event, field, value)
    await db.commit()
    await db.refresh(event)
    return await build_event_response(event, db, current_user.id)


@router.patch("/{event_id}/reschedule")
async def reschedule_event(
    event_id: uuid.UUID,
    body: RescheduleEventRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _auth: Annotated[ClubMember, Depends(require_event_club_organizer)],
) -> EventResponse:
    event = await get_event_or_404(event_id, db)
    event.date = body.newDate
    event.status = "rescheduled"
    if body.newAddress is not None:
        event.address = body.newAddress
    if body.newCity is not None:
        event.city = body.newCity
    await db.commit()
    await db.refresh(event)
    return await build_event_response(event, db, current_user.id)


@router.patch("/{event_id}/cancel")
async def cancel_event(
    event_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _auth: Annotated[ClubMember, Depends(require_event_club_organizer)],
) -> EventResponse:
    event = await get_event_or_404(event_id, db)
    event.status = "cancelled"
    event.cancelled_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(event)
    return await build_event_response(event, db, current_user.id)
