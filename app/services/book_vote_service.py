from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import require_club_organizer
from app.exceptions import AppError
from app.models.book_vote import BookVoteOption, BookVoteRound
from app.models.user import User
from app.repositories import BookVoteRepository, ClubRepository
from app.schemas.book_vote import BookOptionResponse, BookVoteRoundResponse

ROUND_NOT_FOUND = "Voting round not found"
OPTION_NOT_FOUND = "Book option not found"
ROUND_CLOSED = "Voting round is closed"


async def _require_club_member(club_id: uuid.UUID, current_user: User, db: AsyncSession) -> None:
    membership = await ClubRepository(db).get_membership(club_id, current_user.id)
    if membership is None:
        raise AppError(status.HTTP_403_FORBIDDEN, "Not authorized", "FORBIDDEN")


async def _get_round_for_club_or_404(
    round_id: uuid.UUID, club_id: uuid.UUID, repo: BookVoteRepository
) -> BookVoteRound:
    round_ = await repo.get_round(round_id)
    if round_ is None or round_.club_id != club_id:
        raise AppError(status.HTTP_404_NOT_FOUND, ROUND_NOT_FOUND, "ROUND_NOT_FOUND")
    return round_


async def _get_option_for_club_or_404(
    option_id: uuid.UUID, club_id: uuid.UUID, repo: BookVoteRepository
) -> tuple[BookVoteOption, BookVoteRound]:
    option = await repo.get_option(option_id)
    if option is None:
        raise AppError(status.HTTP_404_NOT_FOUND, OPTION_NOT_FOUND, "OPTION_NOT_FOUND")
    round_ = await _get_round_for_club_or_404(option.round_id, club_id, repo)
    return option, round_


async def _build_round_response(
    round_: BookVoteRound, user_id: uuid.UUID, repo: BookVoteRepository
) -> BookVoteRoundResponse:
    options = await repo.get_options(round_.id)
    vote_counts = await repo.get_vote_counts(round_.id)
    user_vote = await repo.get_user_vote(round_.id, user_id)
    voted_option_id = user_vote.option_id if user_vote else None

    return BookVoteRoundResponse(
        id=str(round_.id),
        clubId=str(round_.club_id),
        status=round_.status,
        totalVotes=sum(vote_counts.values()),
        winnerId=str(round_.winner_option_id) if round_.winner_option_id else None,
        options=[
            BookOptionResponse(
                id=str(o.id),
                title=o.title,
                author=o.author or "",
                votes=vote_counts.get(o.id, 0),
                hasVoted=o.id == voted_option_id,
            )
            for o in options
        ],
    )


async def get_current_round_service(
    club_id: uuid.UUID, current_user: User, db: AsyncSession
) -> BookVoteRoundResponse | None:
    await _require_club_member(club_id, current_user, db)
    repo = BookVoteRepository(db)
    round_ = await repo.get_latest_round(club_id)
    if round_ is None:
        return None
    return await _build_round_response(round_, current_user.id, repo)


async def create_round_service(club_id: uuid.UUID, current_user: User, db: AsyncSession) -> BookVoteRoundResponse:
    await require_club_organizer(club_id, current_user, db)
    repo = BookVoteRepository(db)
    round_ = repo.create_round(club_id)
    await db.commit()
    await db.refresh(round_)
    return await _build_round_response(round_, current_user.id, repo)


async def add_option_service(
    club_id: uuid.UUID, round_id: uuid.UUID, title: str, author: str, current_user: User, db: AsyncSession
) -> BookVoteRoundResponse:
    await require_club_organizer(club_id, current_user, db)
    repo = BookVoteRepository(db)
    round_ = await _get_round_for_club_or_404(round_id, club_id, repo)
    if round_.status != "open":
        raise AppError(status.HTTP_409_CONFLICT, ROUND_CLOSED, "ROUND_CLOSED")

    repo.add_option(round_id, title.strip(), author.strip())
    await db.commit()
    await db.refresh(round_)
    return await _build_round_response(round_, current_user.id, repo)


async def remove_option_service(
    club_id: uuid.UUID, option_id: uuid.UUID, current_user: User, db: AsyncSession
) -> BookVoteRoundResponse:
    await require_club_organizer(club_id, current_user, db)
    repo = BookVoteRepository(db)
    option, round_ = await _get_option_for_club_or_404(option_id, club_id, repo)
    if round_.status != "open":
        raise AppError(status.HTTP_409_CONFLICT, ROUND_CLOSED, "ROUND_CLOSED")

    votes = await repo.count_votes_for_option(option_id)
    if votes > 0:
        raise AppError(status.HTTP_409_CONFLICT, "Cannot remove an option with votes", "OPTION_HAS_VOTES")

    await repo.delete_option(option)
    await db.commit()
    await db.refresh(round_)
    return await _build_round_response(round_, current_user.id, repo)


async def vote_service(
    club_id: uuid.UUID, option_id: uuid.UUID, current_user: User, db: AsyncSession
) -> BookVoteRoundResponse:
    await _require_club_member(club_id, current_user, db)
    repo = BookVoteRepository(db)
    _option, round_ = await _get_option_for_club_or_404(option_id, club_id, repo)
    if round_.status != "open":
        raise AppError(status.HTTP_409_CONFLICT, ROUND_CLOSED, "ROUND_CLOSED")

    existing = await repo.get_user_vote(round_.id, current_user.id)
    if existing is not None and existing.option_id == option_id:
        return await _build_round_response(round_, current_user.id, repo)
    if existing is not None:
        await repo.remove_vote(existing)
        await db.flush()
    repo.add_vote(round_.id, option_id, current_user.id)
    await db.commit()
    return await _build_round_response(round_, current_user.id, repo)


async def unvote_service(
    club_id: uuid.UUID, option_id: uuid.UUID, current_user: User, db: AsyncSession
) -> BookVoteRoundResponse:
    await _require_club_member(club_id, current_user, db)
    repo = BookVoteRepository(db)
    _option, round_ = await _get_option_for_club_or_404(option_id, club_id, repo)

    existing = await repo.get_user_vote(round_.id, current_user.id)
    if existing is not None and existing.option_id == option_id:
        await repo.remove_vote(existing)
        await db.commit()
    return await _build_round_response(round_, current_user.id, repo)


async def close_round_service(
    club_id: uuid.UUID, round_id: uuid.UUID, current_user: User, db: AsyncSession
) -> BookVoteRoundResponse:
    await require_club_organizer(club_id, current_user, db)
    repo = BookVoteRepository(db)
    round_ = await _get_round_for_club_or_404(round_id, club_id, repo)

    if round_.status == "open":
        options = await repo.get_options(round_.id)
        vote_counts = await repo.get_vote_counts(round_.id)
        # Ties broken by option creation order, matching the pre-backend client logic.
        winner_id = max(options, key=lambda o: vote_counts.get(o.id, 0)).id if options else None
        round_.status = "closed"
        round_.winner_option_id = winner_id
        round_.closed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(round_)

    return await _build_round_response(round_, current_user.id, repo)
