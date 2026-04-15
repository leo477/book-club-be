import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep
from app.models.randomizer import RandomizerSession
from app.models.user import User
from app.schemas.randomizer import (
    CandidateSchema,
    CreateRandomizerSessionRequest,
    RandomizerSessionResponse,
)

router = APIRouter(
    prefix="/api/v1/clubs/{club_id}/randomizer",
    tags=["randomizer"],
)


def _build_response(session: RandomizerSession) -> RandomizerSessionResponse:
    candidates = [CandidateSchema(**c) for c in (session.candidates or [])]
    result = CandidateSchema(**session.result) if session.result else None
    return RandomizerSessionResponse(
        id=str(session.id),
        clubId=str(session.club_id),
        createdBy=str(session.created_by),
        purpose=session.purpose,
        candidates=candidates,
        result=result,
        createdAt=session.created_at.isoformat(),
    )


@router.get("/history", status_code=status.HTTP_200_OK)
async def get_history(
    club_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> list[RandomizerSessionResponse]:
    result = await db.execute(
        select(RandomizerSession)
        .where(RandomizerSession.club_id == club_id)
        .order_by(RandomizerSession.created_at.desc())
    )
    sessions = result.scalars().all()
    return [_build_response(s) for s in sessions]


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    club_id: uuid.UUID,
    body: CreateRandomizerSessionRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RandomizerSessionResponse:
    session = RandomizerSession(
        club_id=club_id,
        created_by=current_user.id,
        purpose=body.purpose,
        candidates=[c.model_dump() for c in body.candidates],
        result=body.result.model_dump() if body.result else None,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _build_response(session)
