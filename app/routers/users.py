from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep
from app.models.club_member import ClubMember
from app.models.quiz import QuizAttempt
from app.models.user import User
from app.schemas.auth import UserProfileResponse
from app.schemas.users import (
    UpdateProfileRequest,
    UpdateRoleRequest,
    UpdateSocialsRequest,
    UpdateSocialsVisibilityRequest,
    UserStatsResponse,
)

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/me/stats", response_model=UserStatsResponse)
async def get_my_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> UserStatsResponse:
    clubs_result = await db.execute(
        select(func.count()).select_from(ClubMember).where(ClubMember.user_id == current_user.id)
    )
    clubs_joined = clubs_result.scalar() or 0

    quizzes_result = await db.execute(
        select(func.count()).select_from(QuizAttempt).where(QuizAttempt.user_id == current_user.id)
    )
    quizzes_taken = quizzes_result.scalar() or 0

    wins_result = await db.execute(
        select(func.count())
        .select_from(QuizAttempt)
        .where(
            QuizAttempt.user_id == current_user.id,
            QuizAttempt.score == QuizAttempt.total,
        )
    )
    quiz_wins = wins_result.scalar() or 0

    return UserStatsResponse(
        clubsJoined=clubs_joined,
        quizzesTaken=quizzes_taken,
        quizWins=quiz_wins,
        likesReceived=0,
        booksRead=0,
    )


@router.patch("/me", response_model=UserProfileResponse)
async def update_profile(
    body: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> UserProfileResponse:
    if body.displayName is not None:
        current_user.display_name = body.displayName
    await db.commit()
    await db.refresh(current_user)
    return UserProfileResponse.model_validate(current_user)


@router.patch("/me/role", response_model=UserProfileResponse)
async def update_role(
    body: UpdateRoleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> UserProfileResponse:
    current_user.role = body.role
    await db.commit()
    await db.refresh(current_user)
    return UserProfileResponse.model_validate(current_user)


@router.patch("/me/socials", response_model=UserProfileResponse)
async def update_socials(
    body: UpdateSocialsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> UserProfileResponse:
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)
    await db.commit()
    await db.refresh(current_user)
    return UserProfileResponse.model_validate(current_user)


@router.patch("/me/socials-visibility", response_model=UserProfileResponse)
async def update_socials_visibility(
    body: UpdateSocialsVisibilityRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> UserProfileResponse:
    current_user.socials_public = body.socialsPublic
    await db.commit()
    await db.refresh(current_user)
    return UserProfileResponse.model_validate(current_user)
