from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book_vote import BookVoteOption, BookVoteRound, BookVoteVote


class BookVoteRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_latest_round(self, club_id: uuid.UUID) -> BookVoteRound | None:
        result = await self.db.execute(
            select(BookVoteRound)
            .where(BookVoteRound.club_id == club_id)
            .order_by(BookVoteRound.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_round(self, round_id: uuid.UUID) -> BookVoteRound | None:
        result = await self.db.execute(select(BookVoteRound).where(BookVoteRound.id == round_id))
        return result.scalar_one_or_none()

    def create_round(self, club_id: uuid.UUID) -> BookVoteRound:
        round_ = BookVoteRound(id=uuid.uuid4(), club_id=club_id, status="open")
        self.db.add(round_)
        return round_

    async def get_option(self, option_id: uuid.UUID) -> BookVoteOption | None:
        result = await self.db.execute(select(BookVoteOption).where(BookVoteOption.id == option_id))
        return result.scalar_one_or_none()

    async def get_options(self, round_id: uuid.UUID) -> list[BookVoteOption]:
        result = await self.db.execute(
            select(BookVoteOption).where(BookVoteOption.round_id == round_id).order_by(BookVoteOption.created_at)
        )
        return list(result.scalars().all())

    def add_option(self, round_id: uuid.UUID, title: str, author: str) -> BookVoteOption:
        option = BookVoteOption(id=uuid.uuid4(), round_id=round_id, title=title, author=author or None)
        self.db.add(option)
        return option

    async def delete_option(self, option: BookVoteOption) -> None:
        await self.db.delete(option)

    async def get_vote_counts(self, round_id: uuid.UUID) -> dict[uuid.UUID, int]:
        result = await self.db.execute(
            select(BookVoteVote.option_id, func.count())
            .where(BookVoteVote.round_id == round_id)
            .group_by(BookVoteVote.option_id)
        )
        return dict(result.tuples().all())

    async def count_votes_for_option(self, option_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(BookVoteVote).where(BookVoteVote.option_id == option_id)
        )
        return result.scalar() or 0

    async def get_user_vote(self, round_id: uuid.UUID, user_id: uuid.UUID) -> BookVoteVote | None:
        result = await self.db.execute(
            select(BookVoteVote).where(BookVoteVote.round_id == round_id, BookVoteVote.user_id == user_id)
        )
        return result.scalar_one_or_none()

    def add_vote(self, round_id: uuid.UUID, option_id: uuid.UUID, user_id: uuid.UUID) -> BookVoteVote:
        vote = BookVoteVote(id=uuid.uuid4(), round_id=round_id, option_id=option_id, user_id=user_id)
        self.db.add(vote)
        return vote

    async def remove_vote(self, vote: BookVoteVote) -> None:
        await self.db.delete(vote)
