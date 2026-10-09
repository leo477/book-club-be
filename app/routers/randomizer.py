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
    seen: set[uuid.UUID] = set()
    for c in body.candidates:
        try:
            candidate_id = uuid.UUID(c.userId)
        except ValueError:
            raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid candidate id", "INVALID_CANDIDATE") from None
        if candidate_id in seen:
            raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "Duplicate candidate", "DUPLICATE_CANDIDATE")
        seen.add(candidate_id)
        ids.append(candidate_id)
    rows = await db.execute(
        select(User.id, User.display_name, User.avatar_url)
        .join(ClubMember, ClubMember.user_id == User.id)
        .where(ClubMember.club_id == club_id, User.id.in_(ids))
    )
    resolved = {
        str(row.id): CandidateSchema(userId=str(row.id), displayName=row.display_name, avatarUrl=row.avatar_url)
        for row in rows
    }
    if len(resolved) != len(ids):
        raise AppError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Candidates must be members of this club", "CANDIDATE_NOT_MEMBER"
        )
    candidates = [resolved[str(i)] for i in ids]
    result = None
    if body.result is not None:
        try:
            result = resolved[str(uuid.UUID(body.result.userId))]
        except (ValueError, KeyError):
            raise AppError(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Result must be one of the candidates", "RESULT_NOT_CANDIDATE"
            ) from None
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
