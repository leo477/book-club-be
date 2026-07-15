from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep
from app.models.push_token import PushToken
from app.models.user import User
from app.schemas.notifications import RegisterPushTokenRequest, UnregisterPushTokenRequest

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.post("/register-token", status_code=status.HTTP_204_NO_CONTENT)
async def register_token(
    body: RegisterPushTokenRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> None:
    existing = await db.scalar(select(PushToken).where(PushToken.token == body.token))
    if existing is not None:
        # Token reuse across app reinstalls / different logged-in users on the same
        # device — a token should map to at most one user at a time.
        existing.user_id = current_user.id
        existing.platform = body.platform
    else:
        db.add(PushToken(user_id=current_user.id, token=body.token, platform=body.platform))
    await db.commit()


@router.delete("/register-token", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_token(
    body: UnregisterPushTokenRequest,
    _current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> None:
    # Delete regardless of which user currently owns the token — a logout should
    # always succeed at clearing it, idempotently (no error if already gone).
    await db.execute(delete(PushToken).where(PushToken.token == body.token))
    await db.commit()
