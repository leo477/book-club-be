import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep
from app.models.user import User
from app.schemas.book_vote import AddBookOptionRequest, BookVoteRoundResponse
from app.services.book_vote_service import (
    add_option_service,
    close_round_service,
    create_round_service,
    get_current_round_service,
    remove_option_service,
    unvote_service,
    vote_service,
)

router = APIRouter(prefix="/api/v1/clubs/{club_id}/book-vote", tags=["book-vote"])


@router.get("/round")
async def get_current_round(
    club_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BookVoteRoundResponse | None:
    return await get_current_round_service(club_id, current_user, db)


@router.post("/rounds", status_code=status.HTTP_201_CREATED)
async def create_round(
    club_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BookVoteRoundResponse:
    return await create_round_service(club_id, current_user, db)


@router.post("/rounds/{round_id}/options", status_code=status.HTTP_201_CREATED)
async def add_option(
    club_id: uuid.UUID,
    round_id: uuid.UUID,
    body: AddBookOptionRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BookVoteRoundResponse:
    return await add_option_service(club_id, round_id, body.title, body.author, current_user, db)


@router.delete("/options/{option_id}")
async def remove_option(
    club_id: uuid.UUID,
    option_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BookVoteRoundResponse:
    return await remove_option_service(club_id, option_id, current_user, db)


@router.post("/options/{option_id}/vote")
async def vote(
    club_id: uuid.UUID,
    option_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BookVoteRoundResponse:
    return await vote_service(club_id, option_id, current_user, db)


@router.delete("/options/{option_id}/vote")
async def unvote(
    club_id: uuid.UUID,
    option_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BookVoteRoundResponse:
    return await unvote_service(club_id, option_id, current_user, db)


@router.post("/rounds/{round_id}/close")
async def close_round(
    club_id: uuid.UUID,
    round_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BookVoteRoundResponse:
    return await close_round_service(club_id, round_id, current_user, db)
