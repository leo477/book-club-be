from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import get_current_user, get_db_dep, get_settings_dep
from app.models.club import Club
from app.models.club_ban import ClubBan
from app.models.club_member import ClubMember
from app.models.user import User
from app.schemas.clubs import (
    ClubResponse,
    CreateClubRequest,
    RescheduleRequest,
    build_club_response,
)

router = APIRouter(prefix="/api/v1/clubs", tags=["clubs"])


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db_dep),
    settings: Settings = Depends(get_settings_dep),
) -> User | None:
    try:
        return await get_current_user(request=request, db=db, settings=settings)
    # noinspection PyBroadException
    except HTTPException:
        return None


# noinspection PyShadowingNames
async def _require_organizer(club_id: uuid.UUID, user: User, db: AsyncSession) -> ClubMember:
    result = await db.execute(
        select(ClubMember).where(
            and_(
                ClubMember.club_id == club_id,
                ClubMember.user_id == user.id,
                ClubMember.role == "organizer",
            )
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return membership


@router.get("", response_model=list[ClubResponse])
async def list_clubs(
    search: str | None = None,
    city: str | None = None,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db_dep),
) -> list[ClubResponse]:
    stmt = select(Club)

    if current_user is not None:
        member_club_ids = select(ClubMember.club_id).where(ClubMember.user_id == current_user.id)
        stmt = stmt.where(or_(Club.is_public == True, Club.id.in_(member_club_ids)))  # noqa: E712
    else:
        stmt = stmt.where(Club.is_public == True)  # noqa: E712

    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Club.name.ilike(like), Club.description.ilike(like)))
    if city:
        stmt = stmt.where(Club.city.ilike(f"%{city}%"))

    result = await db.execute(stmt)
    clubs = result.scalars().all()
    return [await build_club_response(c, db) for c in clubs]


@router.get("/my", response_model=list[ClubResponse])
async def list_my_clubs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> list[ClubResponse]:
    member_club_ids = select(ClubMember.club_id).where(ClubMember.user_id == current_user.id)
    result = await db.execute(
        select(Club).where(or_(Club.id.in_(member_club_ids), Club.organizer_id == current_user.id))
    )
    clubs = result.scalars().all()
    return [await build_club_response(c, db) for c in clubs]


@router.get("/{club_id}", response_model=ClubResponse)
async def get_club(
    club_id: str,
    _current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db_dep),
) -> ClubResponse:
    result = await db.execute(select(Club).where(Club.id == uuid.UUID(club_id)))
    club = result.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club not found")
    return await build_club_response(club, db)


@router.post("", response_model=ClubResponse, status_code=status.HTTP_201_CREATED)
async def create_club(
    body: CreateClubRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> ClubResponse:
    if current_user.role != "organizer":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only organizers can create clubs")

    club = Club(
        id=uuid.uuid4(),
        name=body.name,
        description=body.description,
        is_public=body.isPublic,
        city=body.city,
        tags=body.tags,
        meeting_duration_minutes=body.meetingDurationMinutes,
        after_meeting_venue=body.afterMeetingVenue.model_dump() if body.afterMeetingVenue else None,
        organizer_id=current_user.id,
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


@router.patch("/{club_id}/pause", response_model=ClubResponse)
async def pause_club(
    club_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> ClubResponse:
    cid = uuid.UUID(club_id)
    _ = await _require_organizer(cid, current_user, db)

    result = await db.execute(select(Club).where(Club.id == cid))
    club = result.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club not found")

    club.status = "paused"
    await db.commit()
    await db.refresh(club)
    return await build_club_response(club, db)


@router.patch("/{club_id}/cancel", response_model=ClubResponse)
async def cancel_club(
    club_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> ClubResponse:
    cid = uuid.UUID(club_id)
    _ = await _require_organizer(cid, current_user, db)

    result = await db.execute(select(Club).where(Club.id == cid))
    club = result.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club not found")

    club.status = "cancelled"
    club.cancelled_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(club)
    return await build_club_response(club, db)


@router.patch("/{club_id}/reschedule", response_model=ClubResponse)
async def reschedule_club(
    club_id: str,
    body: RescheduleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> ClubResponse:
    cid = uuid.UUID(club_id)
    _ = await _require_organizer(cid, current_user, db)

    result = await db.execute(select(Club).where(Club.id == cid))
    club = result.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club not found")

    club.next_meeting_date = datetime.fromisoformat(body.newDate)
    await db.commit()
    await db.refresh(club)
    return await build_club_response(club, db)


@router.post("/{club_id}/join")
async def join_club(
    club_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> dict[str, int]:
    cid = uuid.UUID(club_id)

    result = await db.execute(select(Club).where(Club.id == cid))
    club = result.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club not found")

    ban_result = await db.execute(
        select(ClubBan).where(and_(ClubBan.club_id == cid, ClubBan.user_id == current_user.id))
    )
    if ban_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are banned from this club")

    existing = await db.execute(
        select(ClubMember).where(and_(ClubMember.club_id == cid, ClubMember.user_id == current_user.id))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already a member")

    membership = ClubMember(
        id=uuid.uuid4(),
        club_id=cid,
        user_id=current_user.id,
        role="member",
    )
    db.add(membership)
    await db.commit()

    count_result = await db.execute(select(func.count()).select_from(ClubMember).where(ClubMember.club_id == cid))
    member_count = count_result.scalar() or 0
    return {"memberCount": member_count}


@router.delete("/{club_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_club(
    club_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> None:
    cid = uuid.UUID(club_id)

    result = await db.execute(select(Club).where(Club.id == cid))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club not found")

    existing = await db.execute(
        select(ClubMember).where(and_(ClubMember.club_id == cid, ClubMember.user_id == current_user.id))
    )
    member = existing.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not a member")

    await db.execute(delete(ClubMember).where(and_(ClubMember.club_id == cid, ClubMember.user_id == current_user.id)))
    await db.commit()
