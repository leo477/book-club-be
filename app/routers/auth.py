import uuid
from typing import Annotated, Literal, Union

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import get_current_user, get_db_dep, get_settings_dep
from app.models.user import User
from app.schemas.auth import AuthResponse, UserProfileResponse
from app.services.auth_service import get_supabase_client, supabase_sign_in, supabase_sign_up

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    email: Annotated[EmailStr, Body()],
    password: Annotated[str, Body(min_length=8)],
    displayName: Annotated[str, Body(min_length=1, max_length=100)],  # noqa: N803
    role: Annotated[Literal["user", "organizer"], Body()] = "user",
) -> Union[AuthResponse, JSONResponse]:
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "Email already exists", "code": "EMAIL_EXISTS"},
        )

    client = await get_supabase_client(settings)
    auth_response = await supabase_sign_up(client, str(email), password, displayName, role)

    if auth_response.user is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "Auth service error", "code": "AUTH_SERVICE_ERROR"},
        )

    supabase_user_id: uuid.UUID = uuid.UUID(str(auth_response.user.id))

    user = User(
        id=uuid.uuid4(),
        supabase_user_id=supabase_user_id,
        email=str(email),
        display_name=displayName,
        role=role,
        socials_public=False,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    if auth_response.session is None:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"message": "Check your email to confirm registration", "code": "EMAIL_CONFIRMATION_REQUIRED"},
        )

    return AuthResponse(
        user=UserProfileResponse.model_validate(user),
        accessToken=auth_response.session.access_token,
        refreshToken=auth_response.session.refresh_token,
    )


@router.post("/login", status_code=status.HTTP_200_OK)
async def login(
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    email: Annotated[EmailStr, Body()],
    password: Annotated[str, Body(min_length=1)],
) -> AuthResponse:
    client = await get_supabase_client(settings)
    auth_response = await supabase_sign_in(client, str(email), password)

    if auth_response.user is None or auth_response.session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid credentials", "code": "INVALID_CREDENTIALS"},
        )

    supabase_user_id: uuid.UUID = uuid.UUID(str(auth_response.user.id))

    result = await db.execute(select(User).where(User.supabase_user_id == supabase_user_id))
    user = result.scalar_one_or_none()
    if not user:
        sb_user = auth_response.user
        display_name = (sb_user.user_metadata or {}).get("display_name", sb_user.email or "")
        role = (sb_user.user_metadata or {}).get("role", "user")
        user = User(
            id=uuid.uuid4(),
            supabase_user_id=supabase_user_id,
            email=str(email),
            display_name=str(display_name),
            role=str(role),
            socials_public=False,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)

    return AuthResponse(
        user=UserProfileResponse.model_validate(user),
        accessToken=auth_response.session.access_token,
        refreshToken=auth_response.session.refresh_token,
    )


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
