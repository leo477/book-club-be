from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.exceptions import AppError
from app.models.club_member import ClubMember
from app.models.user import User


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

    from app.models.user import User as UserModel
    from app.services.auth_service import decode_access_token

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise AppError(status.HTTP_401_UNAUTHORIZED, "Not authenticated", "NOT_AUTHENTICATED")

    token = auth_header.split(" ", 1)[1]
    payload = decode_access_token(token, settings)
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise AppError(status.HTTP_401_UNAUTHORIZED, "Invalid token", "INVALID_TOKEN")

    result = await db.execute(select(UserModel).where(UserModel.supabase_user_id == uuid.UUID(user_id)))
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
    from sqlalchemy import and_, select

    from app.models.club_member import ClubMember as ClubMemberModel

    result = await db.execute(
        select(ClubMemberModel).where(
            and_(
                ClubMemberModel.club_id == club_id,
                ClubMemberModel.user_id == user_id,
                ClubMemberModel.role == "organizer",
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


async def require_event_club_organizer(
    event_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ClubMember:
    from app.models.event import Event as EventModel

    result = await db.execute(select(EventModel).where(EventModel.id == event_id))
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
        return None


async def get_redis(request: Request) -> aioredis.Redis:
    """Return a Redis client backed by the shared connection pool from app state."""
    pool = request.app.state.redis_pool
    return aioredis.Redis(connection_pool=pool)
