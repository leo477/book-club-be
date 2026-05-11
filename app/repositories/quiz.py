from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.quiz import Quiz, QuizAttempt, QuizQuestion, QuizSession
from app.models.user import User


class QuizRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, quiz_id: uuid.UUID) -> Quiz | None:
        result = await self.db.execute(select(Quiz).where(Quiz.id == quiz_id))
        return result.scalar_one_or_none()

    async def list_for_club(self, club_id: uuid.UUID, skip: int = 0, limit: int = 20) -> list[Quiz]:
        result = await self.db.execute(select(Quiz).where(Quiz.club_id == club_id).offset(skip).limit(limit))
        return list(result.scalars().all())

    async def get_questions(self, quiz_id: uuid.UUID) -> list[QuizQuestion]:
        result = await self.db.execute(
            select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id).order_by(QuizQuestion.position)
        )
        return list(result.scalars().all())

    async def get_question(self, question_id: uuid.UUID, quiz_id: uuid.UUID) -> QuizQuestion | None:
        result = await self.db.execute(
            select(QuizQuestion).where(QuizQuestion.id == question_id, QuizQuestion.quiz_id == quiz_id)
        )
        return result.scalar_one_or_none()

    async def count_questions(self, quiz_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id)
        )
        return result.scalar() or 0

    async def get_active_session(self, quiz_id: uuid.UUID) -> QuizSession | None:
        result = await self.db.execute(
            select(QuizSession)
            .where(QuizSession.quiz_id == quiz_id, QuizSession.closed_at.is_(None))
            .order_by(QuizSession.started_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_session(self, session_id: uuid.UUID, quiz_id: uuid.UUID) -> QuizSession | None:
        result = await self.db.execute(
            select(QuizSession).where(QuizSession.id == session_id, QuizSession.quiz_id == quiz_id)
        )
        return result.scalar_one_or_none()

    async def count_attempts(self, quiz_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(QuizAttempt).where(QuizAttempt.quiz_id == quiz_id)
        )
        return result.scalar() or 0

    async def get_attempts_with_users(self, quiz_id: uuid.UUID) -> list[tuple[QuizAttempt, str, str | None]]:
        result = await self.db.execute(
            select(QuizAttempt, User.display_name, User.avatar_url)
            .join(User, QuizAttempt.user_id == User.id)
            .where(QuizAttempt.quiz_id == quiz_id)
            .order_by(QuizAttempt.score.desc(), QuizAttempt.created_at.asc())
        )
        return list(result.all())
