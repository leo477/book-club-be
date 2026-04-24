from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, get_optional_user, require_event_club_organizer
from app.models.club_member import ClubMember
from app.models.event import Event, EventAttendee
from app.models.user import User
from app.schemas.events import EventResponse, RescheduleEventRequest
from app.services.event_service import build_event_response, get_event_or_404

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
    stmt = select(Event).where(
        and_(
            Event.date >= datetime.now(tz=UTC),
            Event.status.in_(["scheduled", "active"]),
        )
    )

    if city:
        stmt = stmt.where(Event.city.ilike(f"%{city}%"))
    if club_id:
        stmt = stmt.where(Event.club_id == club_id)

    stmt = stmt.order_by(Event.date.asc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    events = result.scalars().all()

    current_user_id = current_user.id if current_user else None
    return [await build_event_response(e, db, current_user_id) for e in events]


@router.get("/my")
async def list_my_events(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[EventResponse]:
    member_club_ids = select(ClubMember.club_id).where(ClubMember.user_id == current_user.id)

    stmt = (
        select(Event)
        .where(
            and_(
                Event.club_id.in_(member_club_ids),
                Event.date >= datetime.now(tz=UTC),
                Event.status.in_(["scheduled", "active"]),
            )
        )
        .order_by(Event.date.asc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    events = result.scalars().all()
    return [await build_event_response(e, db, current_user.id) for e in events]


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
) -> dict[str, int]:
    event = await get_event_or_404(event_id, db)
    if event.status in ("cancelled",):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot attend a cancelled event")

    existing = await db.execute(
        select(EventAttendee).where(and_(EventAttendee.event_id == event_id, EventAttendee.user_id == current_user.id))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already attending")

    db.add(EventAttendee(event_id=event_id, user_id=current_user.id))
    await db.commit()

    from sqlalchemy import func

    count_result = await db.execute(
        select(func.count()).select_from(EventAttendee).where(EventAttendee.event_id == event_id)
    )
    return {"attendeeCount": count_result.scalar() or 0}


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


@router.patch("/{event_id}/reschedule")
async def reschedule_event(
    event_id: uuid.UUID,
    body: RescheduleEventRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _auth: Annotated[ClubMember, Depends(require_event_club_organizer)],
) -> EventResponse:
    event = await get_event_or_404(event_id, db)
    event.date = datetime.fromisoformat(body.newDate)
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
