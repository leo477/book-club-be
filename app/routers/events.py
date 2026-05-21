from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, get_optional_user, require_event_club_organizer
from app.models.club_member import ClubMember
from app.models.event import Event, EventAttendee
from app.models.user import User
from app.schemas.events import AttendEventResponse, EventResponse, RescheduleEventRequest
from app.services.event_service import build_event_response, build_event_responses_bulk, get_event_or_404

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.get("", response_model=list[EventResponse])
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
    return await build_event_responses_bulk(list(events), db, current_user_id)


@router.get("/my", response_model=list[EventResponse])
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
    return await build_event_responses_bulk(list(events), db, current_user.id)


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: uuid.UUID,
    current_user: Annotated[User | None, Depends(get_optional_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> EventResponse:
    event = await get_event_or_404(event_id, db)
    current_user_id = current_user.id if current_user else None
    return await build_event_response(event, db, current_user_id)


@router.post("/{event_id}/attend", status_code=status.HTTP_201_CREATED, response_model=AttendEventResponse)
async def attend_event(
    event_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> AttendEventResponse:
    event = await get_event_or_404(event_id, db)
    if event.status == "cancelled":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot attend a cancelled event")

    event_date = event.date if event.date.tzinfo is not None else event.date.replace(tzinfo=UTC)
    if event_date - datetime.now(tz=UTC) < timedelta(days=3):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Registration closed")

    member_result = await db.execute(
        select(ClubMember.id).where(and_(ClubMember.club_id == event.club_id, ClubMember.user_id == current_user.id))
    )
    auto_joined = False
    if member_result.scalar_one_or_none() is None:
        db.add(
            ClubMember(
                id=uuid.uuid4(),
                club_id=event.club_id,
                user_id=current_user.id,
                role="member",
            )
        )
        await db.flush()
        auto_joined = True

    existing = await db.execute(
        select(EventAttendee).where(and_(EventAttendee.event_id == event_id, EventAttendee.user_id == current_user.id))
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already attending")

    db.add(EventAttendee(event_id=event_id, user_id=current_user.id))
    # M-5: guard against TOCTOU race — concurrent attend between SELECT and INSERT
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already attending") from exc

    count_result = await db.execute(
        select(func.count()).select_from(EventAttendee).where(EventAttendee.event_id == event_id)
    )
    return AttendEventResponse(attendeeCount=count_result.scalar() or 0, autoJoined=auto_joined)


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


@router.patch("/{event_id}/reschedule", response_model=EventResponse)
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


@router.patch("/{event_id}/cancel", response_model=EventResponse)
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
