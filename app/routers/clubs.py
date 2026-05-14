from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, get_optional_user, require_club_organizer
from app.exceptions import AppError
from app.models.club import Club
from app.models.club_ban import ClubBan
from app.models.club_member import ClubMember
from app.models.user import User
from app.schemas.clubs import ClubResponse, CreateClubRequest, RescheduleMeetingRequest, UpdateClubRequest
from app.schemas.events import CreateEventRequest, EventResponse
from app.services.club_service import (
    build_club_response,
    build_club_responses_bulk,
    delete_club_cascade,
    get_club_or_404,
)
from app.services.event_service import build_event_response, build_event_responses_bulk

router = APIRouter(prefix="/api/v1/clubs", tags=["clubs"])


@router.get("", response_model=list[ClubResponse])
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


@router.get("/my", response_model=list[ClubResponse])
async def list_my_clubs(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[ClubResponse]:
    member_club_ids = select(ClubMember.club_id).where(ClubMember.user_id == current_user.id)
    result = await db.execute(
        select(Club).where(or_(Club.id.in_(member_club_ids), Club.organizer_id == current_user.id))
    )
    clubs = result.scalars().all()
    return await build_club_responses_bulk(list(clubs), db)


@router.get("/{club_id}", response_model=ClubResponse)
async def get_club(
    club_id: uuid.UUID,
    _current_user: Annotated[User | None, Depends(get_optional_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ClubResponse:
    club = await get_club_or_404(club_id, db)
    return await build_club_response(club, db)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ClubResponse)
async def create_club(
    body: CreateClubRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ClubResponse:
    if current_user.role != "organizer":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only organizers can create clubs")

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
    await db.commit()
    await db.refresh(club)
    return await build_club_response(club, db)


@router.patch("/{club_id}", response_model=ClubResponse)
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


@router.patch("/{club_id}/pause", response_model=ClubResponse)
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


@router.patch("/{club_id}/cancel", response_model=ClubResponse)
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


@router.patch("/{club_id}/reschedule", response_model=ClubResponse)
async def reschedule_club(
    club_id: uuid.UUID,
    body: RescheduleMeetingRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ClubResponse:
    await require_club_organizer(club_id, current_user, db)
    club = await get_club_or_404(club_id, db)
    club.next_meeting_date = datetime.fromisoformat(body.newDate)
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
) -> dict[str, int]:
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
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are banned from this club")

    existing = await db.execute(
        select(ClubMember).where(and_(ClubMember.club_id == club_id, ClubMember.user_id == current_user.id))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already a member")

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
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already a member") from exc

    count_result = await db.execute(select(func.count()).select_from(ClubMember).where(ClubMember.club_id == club_id))
    member_count = count_result.scalar() or 0
    return {"memberCount": member_count}


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
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not a member")

    await db.execute(
        delete(ClubMember).where(and_(ClubMember.club_id == club_id, ClubMember.user_id == current_user.id))
    )
    await db.commit()


@router.get("/{club_id}/events", response_model=list[EventResponse])
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


@router.post("/{club_id}/events", status_code=status.HTTP_201_CREATED, response_model=EventResponse)
async def create_event(
    club_id: uuid.UUID,
    body: CreateEventRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> EventResponse:
    _ = await require_club_organizer(club_id, current_user, db)
    club = await get_club_or_404(club_id, db)

    from app.models.event import Event

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
        status="scheduled",
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return await build_event_response(event, db, current_user.id, club_name=club.name, organizer_id=club.organizer_id)
