from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, get_optional_user, require_club_organizer
from app.models.club import Club
from app.models.club_ban import ClubBan
from app.models.club_member import ClubMember
from app.models.user import User
from app.schemas.clubs import ClubResponse, CreateClubRequest
from app.schemas.events import CreateEventRequest, EventResponse
from app.services.club_service import build_club_response, get_club_or_404
from app.services.event_service import build_event_response

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
        like = f"%{search}%"
        stmt = stmt.where(or_(Club.name.ilike(like), Club.description.ilike(like)))

    stmt = stmt.offset(skip).limit(limit)

    result = await db.execute(stmt)
    clubs = result.scalars().all()
    return [await build_club_response(c, db) for c in clubs]


@router.get("/my")
async def list_my_clubs(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[ClubResponse]:
    member_club_ids = select(ClubMember.club_id).where(ClubMember.user_id == current_user.id)
    result = await db.execute(
        select(Club).where(or_(Club.id.in_(member_club_ids), Club.organizer_id == current_user.id))
    )
    clubs = result.scalars().all()
    return [await build_club_response(c, db) for c in clubs]


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
    if current_user.role != "organizer":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only organizers can create clubs")

    club = Club(
        id=uuid.uuid4(),
        name=body.name,
        description=body.description,
        cover_url=body.coverUrl,
        is_public=body.isPublic,
        organizer_id=current_user.id,
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


@router.post("/{club_id}/join")
async def join_club(
    club_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> dict[str, int]:
    await get_club_or_404(club_id, db)

    ban_result = await db.execute(
        select(ClubBan).where(and_(ClubBan.club_id == club_id, ClubBan.user_id == current_user.id))
    )
    if ban_result.scalar_one_or_none():
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
    await db.commit()

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


@router.get("/{club_id}/events")
async def list_club_events(
    club_id: uuid.UUID,
    current_user: Annotated[User | None, Depends(get_optional_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    upcoming_only: bool = False,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[EventResponse]:
    from datetime import UTC, datetime

    from app.models.event import Event

    club = await get_club_or_404(club_id, db)
    stmt = select(Event).where(Event.club_id == club_id)

    if upcoming_only:
        stmt = stmt.where(Event.date >= datetime.now(tz=UTC))

    stmt = stmt.order_by(Event.date.asc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    events = result.scalars().all()

    current_user_id = current_user.id if current_user else None
    return [
        await build_event_response(e, db, current_user_id, club_name=club.name, organizer_id=club.organizer_id)
        for e in events
    ]


@router.post("/{club_id}/events", status_code=status.HTTP_201_CREATED)
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
        status="scheduled",
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return await build_event_response(event, db, current_user.id, club_name=club.name, organizer_id=club.organizer_id)
