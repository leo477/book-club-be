from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep
from app.models.user import User
from app.schemas.auth import UserProfileResponse
from app.schemas.users import (
    UpdateProfileRequest,
    UpdateRoleRequest,
    UpdateSocialsRequest,
    UpdateSocialsVisibilityRequest,
    UserStatsResponse,
)
from app.services.club_service import get_user_stats

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/me/stats")
async def get_my_stats(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> UserStatsResponse:
    return await get_user_stats(current_user.id, db)


@router.patch("/me")
async def update_profile(
    body: UpdateProfileRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> UserProfileResponse:
    if body.displayName is not None:
        current_user.display_name = body.displayName
    await db.commit()
    await db.refresh(current_user)
    return UserProfileResponse.model_validate(current_user)


@router.patch("/me/role")
async def update_role(
    body: UpdateRoleRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> UserProfileResponse:
    current_user.role = body.role
    await db.commit()
    await db.refresh(current_user)
    return UserProfileResponse.model_validate(current_user)


@router.patch("/me/socials")
async def update_socials(
    body: UpdateSocialsRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> UserProfileResponse:
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)
    await db.commit()
    await db.refresh(current_user)
    return UserProfileResponse.model_validate(current_user)


@router.patch("/me/socials-visibility")
async def update_socials_visibility(
    body: UpdateSocialsVisibilityRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> UserProfileResponse:
    current_user.socials_public = body.socialsPublic
    await db.commit()
    await db.refresh(current_user)
    return UserProfileResponse.model_validate(current_user)
