from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db

if TYPE_CHECKING:
    from app.models.user import User


async def get_db_dep() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db():
        yield session


def get_settings_dep() -> Settings:
    return get_settings()


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_dep),
    settings: Settings = Depends(get_settings_dep),
) -> User:
    from app.models.user import User as UserModel
    from app.services.auth_service import decode_access_token

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Not authenticated", "code": "NOT_AUTHENTICATED"},
        )

    token = auth_header.split(" ", 1)[1]
    payload = decode_access_token(token, settings)
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid token", "code": "INVALID_TOKEN"},
        )

    result = await db.execute(select(UserModel).where(UserModel.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "User not found", "code": "USER_NOT_FOUND"},
        )
    return user
