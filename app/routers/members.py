from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import get_current_user, get_db_dep, get_settings_dep
from app.models.club_ban import ClubBan
from app.models.club_member import ClubMember
from app.models.user import User
from app.schemas.clubs import BanRequest, BanResponse, MemberResponse

router = APIRouter(prefix="/api/v1/clubs/{club_id}", tags=["members"])


async def get_optional_user(
    db: AsyncSession = Depends(get_db_dep),
    settings: Settings = Depends(get_settings_dep),
) -> User | None:
    try:
        return await get_current_user(db=db, settings=settings)
    except HTTPException:
        return None


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


@router.get("/members", response_model=list[MemberResponse])
async def list_members(
    club_id: str,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db_dep),
) -> list[MemberResponse]:
    cid = uuid.UUID(club_id)
    result = await db.execute(
        select(ClubMember, User)
        .join(User, ClubMember.user_id == User.id)
        .where(ClubMember.club_id == cid)
    )
    rows = result.all()

    members: list[MemberResponse] = []
    for membership, user in rows:
        socials: dict | None = None
        if user.socials_public:
            socials = {
                k: v
                for k, v in {
                    "telegram": user.telegram,
                    "instagram": user.instagram,
                    "twitter": user.twitter,
                    "linkedin": user.linkedin,
                    "github": user.github,
                    "goodreads": user.goodreads,
                }.items()
                if v is not None
            }
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
    club_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> None:
    cid = uuid.UUID(club_id)
    uid = uuid.UUID(user_id)
    await _require_organizer(cid, current_user, db)

    existing = await db.execute(
        select(ClubMember).where(
            and_(ClubMember.club_id == cid, ClubMember.user_id == uid)
        )
    )
    if not existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    await db.execute(
        delete(ClubMember).where(
            and_(ClubMember.club_id == cid, ClubMember.user_id == uid)
        )
    )
    await db.commit()


@router.post("/members/{user_id}/ban", response_model=BanResponse, status_code=status.HTTP_201_CREATED)
async def ban_member(
    club_id: str,
    user_id: str,
    body: BanRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> BanResponse:
    cid = uuid.UUID(club_id)
    uid = uuid.UUID(user_id)
    await _require_organizer(cid, current_user, db)

    user_result = await db.execute(select(User).where(User.id == uid))
    if not user_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    banned_at = datetime.now(tz=UTC)
    ban = ClubBan(
        id=uuid.uuid4(),
        club_id=cid,
        user_id=uid,
        banned_by=current_user.id,
        banned_at=banned_at,
        duration=str(body.duration),
    )
    db.add(ban)

    await db.execute(
        delete(ClubMember).where(
            and_(ClubMember.club_id == cid, ClubMember.user_id == uid)
        )
    )
    await db.commit()

    return BanResponse(
        userId=str(uid),
        clubId=str(cid),
        bannedAt=banned_at.isoformat(),
        duration=str(body.duration),
        bannedBy=str(current_user.id),
    )


@router.get("/bans", response_model=list[BanResponse])
async def list_bans(
    club_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> list[BanResponse]:
    cid = uuid.UUID(club_id)
    await _require_organizer(cid, current_user, db)

    result = await db.execute(select(ClubBan).where(ClubBan.club_id == cid))
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
