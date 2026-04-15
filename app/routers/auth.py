import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import get_current_user, get_db_dep, get_settings_dep
from app.models.user import User
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, UserProfileResponse
from app.services.auth_service import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AuthResponse:
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "Email already exists", "code": "EMAIL_EXISTS"},
        )

    user = User(
        id=uuid.uuid4(),
        email=req.email,
        password_hash=hash_password(req.password),
        display_name=req.displayName,
        role=req.role,
        socials_public=False,
        created_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    token = create_access_token({"sub": str(user.id)}, settings)
    return AuthResponse(user=UserProfileResponse.model_validate(user), accessToken=token)


@router.post("/login", status_code=status.HTTP_200_OK)
async def login(
    req: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AuthResponse:
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid credentials", "code": "INVALID_CREDENTIALS"},
        )

    token = create_access_token({"sub": str(user.id)}, settings)
    return AuthResponse(user=UserProfileResponse.model_validate(user), accessToken=token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    _current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", status_code=status.HTTP_200_OK)
async def me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserProfileResponse:
    return UserProfileResponse.model_validate(current_user)
