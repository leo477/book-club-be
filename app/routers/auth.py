import re
import secrets
import string
import uuid
from typing import Annotated, Literal
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supabase_auth.types import User as SupabaseUser

from app.config import Settings
from app.dependencies import get_current_user, get_db_dep, get_settings_dep
from app.limiter import limiter
from app.models.user import User
from app.schemas.auth import AuthResponse, RefreshResponse, UserProfileResponse
from app.services.auth_service import (
    get_supabase_client,
    supabase_exchange_code,
    supabase_oauth_url,
    supabase_refresh,
    supabase_sign_in,
    supabase_sign_up,
)

logger = structlog.get_logger()

_AUTH_PREFIX = "/api/v1/auth"
router = APIRouter(prefix=_AUTH_PREFIX, tags=["auth"])

_REFRESH_COOKIE = "refresh_token"
_FE_ORIGIN_COOKIE = "fe_origin"
_DISPLAY_NAME_ALPHABET = string.ascii_letters + string.digits


def _resolve_frontend_origin(candidate: str | None, settings: Settings) -> str | None:
    """Validate a candidate frontend origin against the allowlist to prevent open redirects."""
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if (
        re.fullmatch(settings.CORS_ORIGIN_REGEX, origin)
        or origin == settings.FRONTEND_URL
        or origin == "http://localhost:4200"
    ):
        return origin
    return None


def _looks_like_email(text: str) -> bool:
    local, sep, domain = text.partition("@")
    return bool(sep and local and "." in domain)


def _random_display_name() -> str:
    suffix = "".join(secrets.choice(_DISPLAY_NAME_ALPHABET) for _ in range(6))
    return f"Reader_{suffix}"


def _sanitize_display_name(display_name: str) -> str:
    if not display_name or _looks_like_email(display_name.strip()):
        return _random_display_name()
    return display_name


async def _get_or_create_user(db: AsyncSession, sb_user: SupabaseUser, email: str) -> User:
    """Find the local User for a Supabase auth user, creating one on first sign-in.

    Display name and role are taken from Supabase user_metadata. OAuth providers
    (e.g. Google) populate full_name/name rather than display_name, so all are tried.
    """
    supabase_user_id: uuid.UUID = uuid.UUID(str(sb_user.id))
    result = await db.execute(select(User).where(User.supabase_user_id == supabase_user_id))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    # Email collision: a local user already exists for this email (e.g. registered via
    # email/password, or a prior OAuth attempt under a different Supabase identity).
    # Link the Supabase identity to the existing row instead of inserting a duplicate,
    # which would violate the unique email constraint and surface as a 500.
    existing = await db.execute(select(User).where(User.email == email))
    user = existing.scalar_one_or_none()
    if user is not None:
        if user.supabase_user_id is None:
            user.supabase_user_id = supabase_user_id
            await db.commit()
            await db.refresh(user)
        return user

    metadata = sb_user.user_metadata or {}
    raw_name = metadata.get("display_name") or metadata.get("full_name") or metadata.get("name") or email
    display_name = _sanitize_display_name(str(raw_name))
    role = str(metadata.get("role", "user"))
    if role not in ("user", "organizer"):
        role = "user"

    user = User(
        id=uuid.uuid4(),
        supabase_user_id=supabase_user_id,
        email=email,
        display_name=display_name,
        role=role,
        socials_public=False,
    )
    db.add(user)
    await db.flush()
    await db.commit()
    await db.refresh(user)
    return user


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    secure = settings.ENV == "production"
    samesite: Literal["lax", "none"] = "none" if settings.ENV == "production" else "lax"
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path=_AUTH_PREFIX,
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    secure = settings.ENV == "production"
    samesite: Literal["lax", "none"] = "none" if settings.ENV == "production" else "lax"
    response.delete_cookie(
        key=_REFRESH_COOKIE,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path=_AUTH_PREFIX,
    )


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=None)
@limiter.limit("5/minute")
async def register(
    request: Request,  # slowapi requires this exact parameter name
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    email: Annotated[EmailStr, Body()],
    password: Annotated[str, Body(min_length=8)],
    display_name: Annotated[str, Body(alias="displayName", min_length=1, max_length=100)],
    role: Annotated[Literal["user", "organizer"], Body()] = "user",
) -> AuthResponse | JSONResponse:
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "Email already exists", "code": "EMAIL_EXISTS"},
        )

    if _looks_like_email(display_name.strip()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "Display name cannot be an email address", "code": "INVALID_DISPLAY_NAME"},
        )
    client = await get_supabase_client(settings)
    auth_response = await supabase_sign_up(client, str(email), password, display_name, role)

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
        display_name=display_name,
        role=role,
        socials_public=False,
    )
    db.add(user)
    await db.flush()
    await db.commit()
    await db.refresh(user)

    if auth_response.session is None:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"message": "Check your email to confirm registration", "code": "EMAIL_CONFIRMATION_REQUIRED"},
        )

    _set_refresh_cookie(response, auth_response.session.refresh_token, settings)
    return AuthResponse(
        user=UserProfileResponse.model_validate(user),
        accessToken=auth_response.session.access_token,
    )


@router.post("/login", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def login(
    request: Request,  # slowapi requires this exact parameter name
    response: Response,
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

    user = await _get_or_create_user(db, auth_response.user, str(email))

    _set_refresh_cookie(response, auth_response.session.refresh_token, settings)
    return AuthResponse(
        user=UserProfileResponse.model_validate(user),
        accessToken=auth_response.session.access_token,
    )


@router.post("/refresh", status_code=status.HTTP_200_OK)
async def refresh_token(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    refresh_token_cookie: Annotated[str | None, Cookie(alias=_REFRESH_COOKIE)] = None,
) -> RefreshResponse:
    token = refresh_token_cookie
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Refresh token missing", "code": "MISSING_REFRESH_TOKEN"},
        )
    client = await get_supabase_client(settings)
    auth_response = await supabase_refresh(client, token)
    if auth_response.session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid or expired refresh token", "code": "INVALID_REFRESH_TOKEN"},
        )
    _set_refresh_cookie(response, auth_response.session.refresh_token, settings)
    return RefreshResponse(
        accessToken=auth_response.session.access_token,
    )


@router.get("/oauth/google")
@limiter.limit("10/minute")
async def oauth_google(
    request: Request,  # slowapi requires this exact parameter name
    settings: Annotated[Settings, Depends(get_settings_dep)],
    origin: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    client = await get_supabase_client(settings)
    redirect_to = f"{settings.BACKEND_URL}{_AUTH_PREFIX}/callback"
    url = await supabase_oauth_url(client, "google", redirect_to)
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)

    candidate = origin or request.headers.get("referer")
    fe_origin = _resolve_frontend_origin(candidate, settings)
    if fe_origin:
        # fe_origin is not raw user input: _resolve_frontend_origin only returns a
        # value that matched the CORS allowlist (regex + known URLs), and the callback
        # re-validates it against allowed_frontends before use. CodeQL false positive.
        response.set_cookie(
            key=_FE_ORIGIN_COOKIE,
            value=fe_origin,
            httponly=True,
            secure=settings.ENV == "production",
            samesite="lax",
            max_age=600,
            path=_AUTH_PREFIX,
        )
    return response


@router.get("/callback")
async def oauth_callback(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    code: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    # _resolve_frontend_origin already enforces the allowlist (CORS regex + FRONTEND_URL
    # + localhost), so any value it returns is a safe redirect target, including dynamic
    # Vercel preview origins. Anything else (tampered/evil cookie) resolves to None.
    candidate_frontend = _resolve_frontend_origin(request.cookies.get(_FE_ORIGIN_COOKIE), settings)
    frontend = candidate_frontend or settings.FRONTEND_URL

    def _redirect(path: str) -> RedirectResponse:
        response = RedirectResponse(f"{frontend}{path}", status_code=status.HTTP_302_FOUND)
        response.delete_cookie(_FE_ORIGIN_COOKIE, path=_AUTH_PREFIX)
        return response

    if not code:
        return _redirect("/login?oauth=failed")
    client = await get_supabase_client(settings)
    try:
        auth_response = await supabase_exchange_code(client, code)
    except HTTPException:
        return _redirect("/login?oauth=failed")
    except Exception:
        logger.exception("Unexpected error during OAuth code exchange")
        return _redirect("/login?oauth=failed")
    if auth_response.user is None or auth_response.session is None:
        return _redirect("/login?oauth=failed")

    try:
        await _get_or_create_user(db, auth_response.user, str(auth_response.user.email))
    except Exception:
        await db.rollback()
        logger.exception("Failed to provision user during OAuth callback")
        return _redirect("/login?oauth=failed")

    redirect = _redirect("/auth/callback")
    _set_refresh_cookie(redirect, auth_response.session.refresh_token, settings)
    return redirect


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    _clear_refresh_cookie(response, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", status_code=status.HTTP_200_OK)
async def me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserProfileResponse:
    return UserProfileResponse.model_validate(current_user)
