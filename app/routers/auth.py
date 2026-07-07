import json
import re
import secrets
import string
import uuid
from typing import Annotated, Literal
from urllib.parse import urlparse

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supabase_auth.types import User as SupabaseUser

from app.config import Settings
from app.dependencies import get_current_user, get_db_dep, get_redis, get_settings_dep
from app.exceptions import AppError
from app.limiter import limiter
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    OAuthExchangeResponse,
    RefreshResponse,
    SessionStatusResponse,
    UserProfileResponse,
    WsTicketResponse,
)
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
_ACCESS_COOKIE = "access_token"
_FE_ORIGIN_COOKIE = "fe_origin"
_OAUTH_FAILED_PATH = "/login?oauth=failed"
_OAUTH_HANDOFF_PREFIX = "oauth:handoff:"
_OAUTH_HANDOFF_TTL = 60
_WS_TICKET_PREFIX = "ws:ticket:"
_WS_TICKET_TTL = 60
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
        # Re-link to the Supabase identity that just authenticated. get_current_user
        # resolves users by supabase_user_id, so a stale id here (e.g. from an earlier
        # email/password signup under a different Supabase identity) would make /me
        # return 401 even after a valid token exchange. The id lookup above found no
        # other row holding it, so reassigning cannot violate the unique constraint.
        if user.supabase_user_id != supabase_user_id:
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
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=settings.ENV == "production",
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path=_AUTH_PREFIX,
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=_REFRESH_COOKIE,
        httponly=True,
        secure=settings.ENV == "production",
        samesite="lax",
        path=_AUTH_PREFIX,
    )


def _set_access_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=_ACCESS_COOKIE,
        value=token,
        httponly=True,
        secure=settings.ENV == "production",
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/api/v1",
    )


def _clear_access_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=_ACCESS_COOKIE,
        httponly=True,
        secure=settings.ENV == "production",
        samesite="lax",
        path="/api/v1",
    )


def _set_session_cookies(response: Response, access_token: str, refresh_token: str, settings: Settings) -> None:
    _set_refresh_cookie(response, refresh_token, settings)
    _set_access_cookie(response, access_token, settings)


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    _clear_refresh_cookie(response, settings)
    _clear_access_cookie(response, settings)


# noinspection PyUnusedLocal
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

    _set_session_cookies(response, auth_response.session.access_token, auth_response.session.refresh_token, settings)
    return AuthResponse(
        user=UserProfileResponse.model_validate(user),
        accessToken=auth_response.session.access_token,
        refreshToken=auth_response.session.refresh_token,
    )


# noinspection PyUnusedLocal
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

    _set_session_cookies(response, auth_response.session.access_token, auth_response.session.refresh_token, settings)
    return AuthResponse(
        user=UserProfileResponse.model_validate(user),
        accessToken=auth_response.session.access_token,
        refreshToken=auth_response.session.refresh_token,
    )


# noinspection PyUnusedLocal
@router.post("/refresh", status_code=status.HTTP_200_OK)
@limiter.limit("20/minute")
async def refresh_token(
    request: Request,  # slowapi requires this exact parameter name
    response: Response,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    refresh_token_cookie: Annotated[str | None, Cookie(alias=_REFRESH_COOKIE)] = None,
    refresh_token_body: Annotated[str | None, Body(alias="refreshToken", embed=True)] = None,
) -> RefreshResponse:
    # Cookie path is the desktop flow; the body fallback covers mobile browsers that
    # block the cross-site refresh cookie set on the backend domain. Deprecated: slated
    # for removal once the cookie-based frontend migration is fully rolled out.
    token = refresh_token_cookie or refresh_token_body
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
    _set_session_cookies(response, auth_response.session.access_token, auth_response.session.refresh_token, settings)
    return RefreshResponse(
        accessToken=auth_response.session.access_token,
        refreshToken=auth_response.session.refresh_token,
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
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
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
        return _redirect(_OAUTH_FAILED_PATH)
    client = await get_supabase_client(settings)
    # noinspection PyBroadException
    try:
        auth_response = await supabase_exchange_code(client, code)
    except HTTPException:
        return _redirect(_OAUTH_FAILED_PATH)
    except Exception:  # intentional: any failure redirects to the OAuth failure page
        logger.exception("Unexpected error during OAuth code exchange")
        return _redirect(_OAUTH_FAILED_PATH)
    if auth_response.user is None or auth_response.session is None:
        return _redirect(_OAUTH_FAILED_PATH)

    # noinspection PyBroadException
    try:
        await _get_or_create_user(db, auth_response.user, str(auth_response.user.email))
    except Exception:  # intentional: provisioning failure redirects to login
        await db.rollback()
        logger.exception("Failed to provision user during OAuth callback")
        return _redirect("/login?oauth=failed")

    # One-time handoff code so the SPA can fetch tokens from the response body via
    # oauth_exchange, which establishes the session cookies on the frontend's proxied origin.
    handoff_code = secrets.token_urlsafe(32)
    try:
        await redis.set(
            f"{_OAUTH_HANDOFF_PREFIX}{handoff_code}",
            json.dumps(
                {
                    "accessToken": auth_response.session.access_token,
                    "refreshToken": auth_response.session.refresh_token,
                    "userId": str(auth_response.user.id),
                }
            ),
            ex=_OAUTH_HANDOFF_TTL,
        )
    except Exception:  # intentional: a Redis failure must not block the OAuth flow
        logger.exception("Failed to store OAuth handoff code")
        return _redirect(_OAUTH_FAILED_PATH)

    return _redirect(f"/auth/callback?code={handoff_code}")


# noinspection PyUnusedLocal
@router.post("/oauth/exchange", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def oauth_exchange(
    request: Request,  # slowapi requires this exact parameter name
    response: Response,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    code: Annotated[str, Body(embed=True)],
) -> OAuthExchangeResponse:
    raw = await redis.getdel(f"{_OAUTH_HANDOFF_PREFIX}{code}")
    if raw is None:
        raise AppError(status.HTTP_400_BAD_REQUEST, "Invalid or expired OAuth code", "INVALID_OAUTH_CODE")
    data = json.loads(raw)
    _set_session_cookies(response, data["accessToken"], data["refreshToken"], settings)
    # Body still returned for backward compat with the currently-deployed frontend bundle;
    # remove once the cookie-based migration is fully rolled out.
    return OAuthExchangeResponse(
        accessToken=data["accessToken"],
        refreshToken=data["refreshToken"],
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    # Mutate the injected `response`, not a freshly constructed one — a new Response()
    # here would discard the delete-cookie headers just set on it.
    _clear_session_cookies(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/ws-ticket", status_code=status.HTTP_200_OK)
async def create_ws_ticket(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WsTicketResponse:
    ticket = secrets.token_urlsafe(32)
    await redis.set(f"{_WS_TICKET_PREFIX}{ticket}", str(current_user.id), ex=_WS_TICKET_TTL)
    return WsTicketResponse(ticket=ticket)


@router.get("/session-status", status_code=status.HTTP_200_OK)
async def session_status(request: Request) -> SessionStatusResponse:
    return SessionStatusResponse(hasSession=bool(request.cookies.get(_REFRESH_COOKIE)))


@router.get("/me", status_code=status.HTTP_200_OK)
async def me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserProfileResponse:
    return UserProfileResponse.model_validate(current_user)
