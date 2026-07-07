from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.exceptions import AppError
from app.models.club_member import ClubMember
from app.models.event import Event
from app.models.user import User
from app.services.auth_service import decode_access_token


async def get_db_dep() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db():
        yield session


def get_settings_dep() -> Settings:
    return get_settings()


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> User:
    cached: User | None = getattr(request.state, "_current_user", None)
    if cached is not None:
        return cached

    # Explicit Bearer header (Swagger/tooling/API clients) takes priority over an ambient
    # access_token cookie, so a caller presenting a specific credential is never silently
    # overridden by whatever session cookie happens to be attached to the request.
    auth_header = request.headers.get("Authorization")
    request.state._auth_via_bearer = bool(auth_header and auth_header.startswith("Bearer "))
    token: str | None
    if auth_header and request.state._auth_via_bearer:
        token = auth_header.split(" ", 1)[1]
    else:
        token = request.cookies.get("access_token")
        if not token:
            raise AppError(status.HTTP_401_UNAUTHORIZED, "Not authenticated", "NOT_AUTHENTICATED")

    payload = decode_access_token(token, settings)
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise AppError(status.HTTP_401_UNAUTHORIZED, "Invalid token", "INVALID_TOKEN")

    result = await db.execute(select(User).where(User.supabase_user_id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise AppError(status.HTTP_401_UNAUTHORIZED, "User not found", "USER_NOT_FOUND")

    request.state._current_user = user
    return user


async def _fetch_organizer_membership(
    club_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> ClubMember | None:
    """Shared DB query for organizer membership checks (MN-12: eliminates duplicate logic)."""
    result = await db.execute(
        select(ClubMember).where(
            and_(
                ClubMember.club_id == club_id,
                ClubMember.user_id == user_id,
                ClubMember.role == "organizer",
            )
        )
    )
    return result.scalar_one_or_none()


async def require_club_organizer(
    club_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ClubMember:
    membership = await _fetch_organizer_membership(club_id, current_user.id, db)
    if not membership:
        raise AppError(status.HTTP_403_FORBIDDEN, "Not authorized", "FORBIDDEN")
    return membership


async def require_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role != "admin":
        raise AppError(status.HTTP_403_FORBIDDEN, "Not authorized", "FORBIDDEN")
    return current_user


async def require_event_club_organizer(
    event_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ClubMember:
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise AppError(404, "Event not found", "EVENT_NOT_FOUND")

    return await require_club_organizer(event.club_id, current_user, db)


async def is_club_organizer(club_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> bool:
    """Check organizer status without raising; delegates to shared query (MN-12)."""
    return await _fetch_organizer_membership(club_id, user_id, db) is not None


async def get_optional_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> User | None:
    try:
        return await get_current_user(request=request, db=db, settings=settings)
    except HTTPException:
        # Unauthenticated access is permitted for optional-auth endpoints.
        return None


def get_redis(request: Request) -> aioredis.Redis:
    """Return a Redis client backed by the shared connection pool from app state."""
    pool = request.app.state.redis_pool
    return aioredis.Redis(connection_pool=pool)
