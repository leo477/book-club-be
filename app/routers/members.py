from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, get_optional_user, require_club_organizer
from app.exceptions import AppError
from app.models.club import Club
from app.models.club_ban import ClubBan
from app.models.club_member import ClubMember
from app.models.user import User
from app.schemas.auth import build_socials
from app.schemas.clubs import BanRequest, BanResponse, MemberResponse

router = APIRouter(prefix="/api/v1/clubs/{club_id}", tags=["members"])


@router.get("/members", response_model=list[MemberResponse])
async def list_members(
    club_id: uuid.UUID,
    _current_user: Annotated[User | None, Depends(get_optional_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[MemberResponse]:
    result = await db.execute(
        select(ClubMember, User).join(User, ClubMember.user_id == User.id).where(ClubMember.club_id == club_id)
    )
    rows = result.all()

    members: list[MemberResponse] = []
    for membership, user in rows:
        socials = {k: v for k, v in build_socials(user).items() if v is not None} if user.socials_public else None
        members.append(
            MemberResponse(
                userId=str(user.id),
                displayName=user.display_name,
                avatarUrl=user.avatar_url,
                role=membership.role,
                socials=socials,
                socialsPublic=user.socials_public,
            )
        )
    return members


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> None:
    _ = await require_club_organizer(club_id, current_user, db)

    existing = await db.execute(
        select(ClubMember).where(and_(ClubMember.club_id == club_id, ClubMember.user_id == user_id))
    )
    if not existing.scalar_one_or_none():
        raise AppError(404, "Member not found", "MEMBER_NOT_FOUND")

    await db.execute(delete(ClubMember).where(and_(ClubMember.club_id == club_id, ClubMember.user_id == user_id)))
    await db.commit()


@router.post("/members/{user_id}/ban", response_model=BanResponse, status_code=status.HTTP_201_CREATED)
async def ban_member(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    body: BanRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BanResponse:
    _ = await require_club_organizer(club_id, current_user, db)

    user_result = await db.execute(select(User).where(User.id == user_id))
    if not user_result.scalar_one_or_none():
        raise AppError(404, "User not found", "USER_NOT_FOUND")

    # M-7: compute expires_at from duration; None = permanent ban
    duration_days_map = {"1": 1, "3": 3, "5": 5}
    duration_str = str(body.duration)
    now_utc = datetime.now(UTC)
    expires_at = (
        now_utc + timedelta(days=duration_days_map[duration_str])
        if duration_str in duration_days_map
        else None  # "permanent"
    )

    ban = ClubBan(
        id=uuid.uuid4(),
        club_id=club_id,
        user_id=user_id,
        banned_by=current_user.id,
        duration=duration_str,
        expires_at=expires_at,
    )
    db.add(ban)

    await db.execute(delete(ClubMember).where(and_(ClubMember.club_id == club_id, ClubMember.user_id == user_id)))
    await db.commit()
    await db.refresh(ban)

    return BanResponse(
        userId=str(user_id),
        clubId=str(club_id),
        bannedAt=ban.banned_at.isoformat(),
        duration=str(body.duration),
        bannedBy=str(current_user.id),
    )


@router.get("/bans", response_model=list[BanResponse])
async def list_bans(
    club_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[BanResponse]:
    # L6: require_club_organizer checks club_members for role="organizer", but a
    # brand-new club's organizer_id in the clubs table is the authoritative source.
    # If the club_members record is missing (data inconsistency / race on creation),
    # fall back to checking Club.organizer_id directly so the endpoint returns []
    # instead of 403, avoiding a spurious frontend warning for new clubs.
    club_result = await db.execute(select(Club).where(Club.id == club_id))
    club = club_result.scalar_one_or_none()
    if club is None:
        raise AppError(404, "Club not found", "CLUB_NOT_FOUND")

    if club.organizer_id != current_user.id:
        # Not the club owner — check club_members for organizer role (e.g. delegated).
        try:
            await require_club_organizer(club_id, current_user, db)
        except AppError:
            raise AppError(403, "Not authorized", "FORBIDDEN") from None

    result = await db.execute(select(ClubBan).where(ClubBan.club_id == club_id))
    bans = result.scalars().all()

    return [
        BanResponse(
            userId=str(b.user_id),
            clubId=str(b.club_id),
            bannedAt=b.banned_at.isoformat(),
            duration=b.duration,
            bannedBy=str(b.banned_by),
        )
        for b in bans
    ]
