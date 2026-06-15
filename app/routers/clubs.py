from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, get_optional_user, require_club_organizer
from app.exceptions import AppError
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
    create_club_service,
    create_event_service,
    delete_club_cascade,
    get_club_or_404,
    get_club_stats_service,
    leave_club_service,
    list_clubs_service,
    list_my_clubs_service,
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
    return await list_clubs_service(current_user, db, search, skip, limit)


@router.get("/my")
async def list_my_clubs(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ClubResponse]:
    return await list_my_clubs_service(current_user, db, skip, limit)


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
    await leave_club_service(club_id, current_user, db)


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
    return await get_club_stats_service(club_id, db)
