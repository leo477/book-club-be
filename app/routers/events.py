from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, get_optional_user, require_event_club_organizer
from app.exceptions import AppError
from app.models.chat import ChatMessage, ChatRoom, ChatRoomBan, MessageRead
from app.models.club import Club
from app.models.club_member import ClubMember
from app.models.event import Event
from app.models.user import User
from app.schemas.events import (
    AttendEventResponse,
    EventResponse,
    EventUpdatePayload,
    RescheduleEventRequest,
    SetWinnerRequest,
)
from app.services.club_service import can_view_club, get_club_or_404
from app.services.event_service import (
    attend_event_service,
    build_event_response,
    cancel_attendance_service,
    fetch_enriched_event_list,
    get_event_or_404,
    set_event_winner_service,
)
from app.services.revalidate import schedule_club_revalidate

router = APIRouter(prefix="/api/v1/events", tags=["events"])


async def _delete_event_chat_room(event_id: uuid.UUID, db: AsyncSession) -> None:
    """Delete the chat room linked to this event (if any), including all child records."""
    room_result = await db.execute(select(ChatRoom).where(ChatRoom.event_id == event_id))
    room = room_result.scalar_one_or_none()
    if room is None:
        return
    room_id = room.id
    await db.execute(delete(MessageRead).where(MessageRead.room_id == room_id))
    await db.execute(delete(ChatRoomBan).where(ChatRoomBan.room_id == room_id))
    await db.execute(delete(ChatMessage).where(ChatMessage.room_id == room_id))
    await db.delete(room)


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
        # MN-10: escape LIKE metacharacters to prevent injection via % and _
        escaped_city = city.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(Event.city.ilike(f"%{escaped_city}%", escape="\\"))
    if club_id:
        filters.append(Event.club_id == club_id)
    if current_user is None or current_user.role != "admin":
        visible = [Event.club_id.in_(select(Club.id).where(Club.is_public.is_(True)))]
        if current_user is not None:
            visible.append(Event.club_id.in_(select(ClubMember.club_id).where(ClubMember.user_id == current_user.id)))
        filters.append(or_(*visible))
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
    club = await get_club_or_404(event.club_id, db)
    if not await can_view_club(club, current_user, db):
        raise AppError(404, "Event not found", "EVENT_NOT_FOUND")
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
    await cancel_attendance_service(event_id, current_user, db)


@router.patch("/{event_id}")
async def update_event(
    event_id: uuid.UUID,
    body: EventUpdatePayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _auth: Annotated[ClubMember, Depends(require_event_club_organizer)],
    background_tasks: BackgroundTasks,
) -> EventResponse:
    event = await get_event_or_404(event_id, db)
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if field == "after_meeting_venue":
            if value is None:
                setattr(event, field, None)
            elif isinstance(value, dict):
                setattr(event, field, value)
            else:
                setattr(event, field, value.model_dump())
        else:
            setattr(event, field, value)
    # Feature 4: delete the associated event chat room when the event ends.
    if updates.get("status") in ("held", "cancelled"):
        await _delete_event_chat_room(event_id, db)
    await db.commit()
    await db.refresh(event)
    schedule_club_revalidate(background_tasks, event.club_id)
    return await build_event_response(event, db, current_user.id)


@router.patch("/{event_id}/reschedule")
async def reschedule_event(
    event_id: uuid.UUID,
    body: RescheduleEventRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _auth: Annotated[ClubMember, Depends(require_event_club_organizer)],
    background_tasks: BackgroundTasks,
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
    schedule_club_revalidate(background_tasks, event.club_id)
    return await build_event_response(event, db, current_user.id)


@router.patch("/{event_id}/cancel")
async def cancel_event(
    event_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _auth: Annotated[ClubMember, Depends(require_event_club_organizer)],
    background_tasks: BackgroundTasks,
) -> EventResponse:
    event = await get_event_or_404(event_id, db)
    event.status = "cancelled"
    event.cancelled_at = datetime.now(tz=UTC)
    await _delete_event_chat_room(event_id, db)
    await db.commit()
    await db.refresh(event)
    schedule_club_revalidate(background_tasks, event.club_id)
    return await build_event_response(event, db, current_user.id)


@router.patch("/{event_id}/winner")
async def set_event_winner(
    event_id: uuid.UUID,
    body: SetWinnerRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _auth: Annotated[ClubMember, Depends(require_event_club_organizer)],
    background_tasks: BackgroundTasks,
) -> EventResponse:
    result = await set_event_winner_service(event_id, body.winner_id, current_user.id, db)
    schedule_club_revalidate(background_tasks, result.clubId)
    return result
