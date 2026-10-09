import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep
from app.exceptions import AppError
from app.models.club_member import ClubMember
from app.models.randomizer import RandomizerSession
from app.models.user import User
from app.repositories import ClubRepository
from app.schemas.randomizer import (
    CandidateSchema,
    CreateRandomizerSessionRequest,
    RandomizerSessionResponse,
)

router = APIRouter(
    prefix="/api/v1/clubs/{club_id}/randomizer",
    tags=["randomizer"],
)


async def _require_club_member(club_id: uuid.UUID, current_user: User, db: AsyncSession) -> None:
    membership = await ClubRepository(db).get_membership(club_id, current_user.id)
    if membership is None:
        raise AppError(status.HTTP_403_FORBIDDEN, "Not authorized", "FORBIDDEN")


async def _resolve_candidates(
    club_id: uuid.UUID, body: CreateRandomizerSessionRequest, db: AsyncSession
) -> tuple[list[CandidateSchema], CandidateSchema | None]:
    ids: list[uuid.UUID] = []
    for c in body.candidates:
        try:
            candidate_id = uuid.UUID(c.userId)
        except ValueError:
            raise AppError(422, "Invalid candidate id", "INVALID_CANDIDATE") from None
        if candidate_id in ids:
            raise AppError(422, "Duplicate candidate", "DUPLICATE_CANDIDATE")
        ids.append(candidate_id)
    rows = await db.execute(
        select(User)
        .join(ClubMember, ClubMember.user_id == User.id)
        .where(ClubMember.club_id == club_id, User.id.in_(ids))
    )
    users = {u.id: u for u in rows.scalars().all()}
    if len(users) != len(ids):
        raise AppError(422, "Candidates must be members of this club", "CANDIDATE_NOT_MEMBER")
    resolved = {
        str(uid): CandidateSchema(userId=str(uid), displayName=u.display_name, avatarUrl=u.avatar_url)
        for uid, u in users.items()
    }
    candidates = [resolved[str(i)] for i in ids]
    result = None
    if body.result is not None:
        try:
            result = resolved[str(uuid.UUID(body.result.userId))]
        except (ValueError, KeyError):
            raise AppError(422, "Result must be one of the candidates", "RESULT_NOT_CANDIDATE") from None
    return candidates, result


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
    current_user: Annotated[User, Depends(get_current_user)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[RandomizerSessionResponse]:
    await _require_club_member(club_id, current_user, db)
    result = await db.execute(
        select(RandomizerSession)
        .where(RandomizerSession.club_id == club_id)
        .order_by(RandomizerSession.created_at.desc())
        .offset(skip)
        .limit(limit)
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
    await _require_club_member(club_id, current_user, db)
    candidates, result = await _resolve_candidates(club_id, body, db)
    session = RandomizerSession(
        club_id=club_id,
        created_by=current_user.id,
        purpose=body.purpose,
        candidates=[c.model_dump() for c in candidates],
        result=result.model_dump() if result else None,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _build_response(session)
