from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, require_club_organizer
from app.exceptions import AppError
from app.models.club import Club
from app.models.club_ban import ClubBan
from app.models.club_join_request import ClubJoinRequest
from app.models.club_member import ClubMember
from app.models.user import User
from app.schemas.auth import build_socials
from app.schemas.clubs import (
    BanRequest,
    BanResponse,
    JoinRequestResponse,
    MemberResponse,
    MyMembershipResponse,
)
from app.services.club_service import (
    approve_join_request_service,
    ban_user_service,
    get_my_membership_service,
    list_join_requests_service,
    reject_join_request_service,
)

router = APIRouter(prefix="/api/v1/clubs/{club_id}", tags=["members"])


@router.get("/members")
async def list_members(
    club_id: uuid.UUID,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[MemberResponse]:
    total_result = await db.execute(select(func.count()).select_from(ClubMember).where(ClubMember.club_id == club_id))
    total = total_result.scalar_one()
    response.headers["X-Total-Count"] = str(total)

    result = await db.execute(
        select(ClubMember, User)
        .join(User, ClubMember.user_id == User.id)
        .where(ClubMember.club_id == club_id)
        .offset(skip)
        .limit(limit)
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


@router.post("/members/{user_id}/ban", status_code=status.HTTP_201_CREATED)
async def ban_member(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    body: BanRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BanResponse:
    return await ban_user_service(club_id, user_id, body, current_user, db)


@router.get("/bans")
async def list_bans(
    club_id: uuid.UUID,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
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

    total_result = await db.execute(select(func.count()).select_from(ClubBan).where(ClubBan.club_id == club_id))
    total = total_result.scalar_one()
    response.headers["X-Total-Count"] = str(total)

    result = await db.execute(select(ClubBan).where(ClubBan.club_id == club_id).offset(skip).limit(limit))
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


@router.get("/join-requests")
async def list_join_requests(
    club_id: uuid.UUID,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JoinRequestResponse]:
    requests = await list_join_requests_service(club_id, current_user, db, skip, limit)

    total_result = await db.execute(
        select(func.count())
        .select_from(ClubJoinRequest)
        .where(and_(ClubJoinRequest.club_id == club_id, ClubJoinRequest.status == "pending"))
    )
    response.headers["X-Total-Count"] = str(total_result.scalar_one())
    return requests


@router.post("/join-requests/{user_id}/approve")
async def approve_join_request(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> dict[str, int]:
    member_count = await approve_join_request_service(club_id, user_id, current_user, db)
    return {"memberCount": member_count}


@router.post("/join-requests/{user_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_join_request(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> None:
    await reject_join_request_service(club_id, user_id, current_user, db)


@router.get("/my-membership")
async def get_my_membership(
    club_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> MyMembershipResponse:
    return await get_my_membership_service(club_id, current_user, db)
