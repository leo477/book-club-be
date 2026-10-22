from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, require_club_owner_or_organizer
from app.models.club_ban import ClubBan
from app.models.club_join_request import ClubJoinRequest
from app.models.club_member import ClubMember
from app.models.user import User
from app.schemas.auth import build_socials
from app.schemas.clubs import (
    BanRequest,
    BanResponse,
    ChangeMemberRoleRequest,
    JoinRequestResponse,
    MemberResponse,
    MyMembershipResponse,
)
from app.services.club_service import (
    approve_join_request_service,
    ban_user_service,
    change_member_role_service,
    get_my_membership_service,
    list_join_requests_service,
    reject_join_request_service,
    remove_member_service,
    require_club_viewable,
    unban_user_service,
)
from app.services.revalidate import revalidate_club

router = APIRouter(prefix="/api/v1/clubs/{club_id}", tags=["members"])


@router.get("/members")
async def list_members(
    club_id: uuid.UUID,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[MemberResponse]:
    await require_club_viewable(club_id, current_user, db)
    total_result = await db.execute(select(func.count()).select_from(ClubMember).where(ClubMember.club_id == club_id))
    total = total_result.scalar_one()
    response.headers["X-Total-Count"] = str(total)

    result = await db.execute(
        select(ClubMember, User)
        .join(User, ClubMember.user_id == User.id)
        .where(ClubMember.club_id == club_id)
        .order_by(ClubMember.joined_at, ClubMember.user_id)
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
    await remove_member_service(club_id, user_id, current_user, db)
    await revalidate_club(club_id, db)


@router.post("/members/{user_id}/ban", status_code=status.HTTP_201_CREATED)
async def ban_member(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    body: BanRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BanResponse:
    response = await ban_user_service(club_id, user_id, body, current_user, db)
    await revalidate_club(club_id, db)
    return response


@router.delete("/bans/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unban_member(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> None:
    await unban_user_service(club_id, user_id, current_user, db)
    await revalidate_club(club_id, db)


@router.patch("/members/{user_id}/role")
async def change_member_role(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    body: ChangeMemberRoleRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> MemberResponse:
    membership = await change_member_role_service(club_id, user_id, body.role, current_user, db)

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one()
    socials = {k: v for k, v in build_socials(user).items() if v is not None} if user.socials_public else None
    response = MemberResponse(
        userId=str(user.id),
        displayName=user.display_name,
        avatarUrl=user.avatar_url,
        role=membership.role,
        socials=socials,
        socialsPublic=user.socials_public,
    )
    await revalidate_club(club_id, db)
    return response


@router.get("/bans")
async def list_bans(
    club_id: uuid.UUID,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[BanResponse]:
    await require_club_owner_or_organizer(club_id, current_user, db)

    # an expired ban no longer applies (see the check in join), so it is not listed or counted
    active = and_(
        ClubBan.club_id == club_id,
        or_(ClubBan.expires_at.is_(None), ClubBan.expires_at > datetime.now(UTC)),
    )
    total_result = await db.execute(select(func.count()).select_from(ClubBan).where(active))
    total = total_result.scalar_one()
    response.headers["X-Total-Count"] = str(total)

    result = await db.execute(
        select(ClubBan, User.display_name, User.avatar_url)
        .join(User, User.id == ClubBan.user_id)
        .where(active)
        .order_by(ClubBan.banned_at, ClubBan.user_id)
        .offset(skip)
        .limit(limit)
    )

    return [
        BanResponse(
            userId=str(b.user_id),
            clubId=str(b.club_id),
            bannedAt=b.banned_at.isoformat(),
            duration=b.duration,
            bannedBy=str(b.banned_by),
            displayName=display_name,
            avatarUrl=avatar_url,
        )
        for b, display_name, avatar_url in result.all()
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
    await revalidate_club(club_id, db)
    return {"memberCount": member_count}


@router.post("/join-requests/{user_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_join_request(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> None:
    await reject_join_request_service(club_id, user_id, current_user, db)
    await revalidate_club(club_id, db)


@router.get("/my-membership")
async def get_my_membership(
    club_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> MyMembershipResponse:
    return await get_my_membership_service(club_id, current_user, db)
