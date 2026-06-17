from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.quiz import Quiz, QuizAttempt, QuizQuestion, QuizSession
from app.models.user import User
from app.repositories import QuizRepository
from app.schemas.quizzes import (
    AttemptResponse,
    LeaderboardEntry,
    LeaderboardResponse,
    SubmitAttemptRequest,
)

QUIZ_NOT_FOUND = "Quiz not found"
SESSION_NOT_FOUND = "Session not found"


async def _get_quiz_or_404(quiz_id: uuid.UUID, db: AsyncSession) -> Quiz:
    quiz = await QuizRepository(db).get_by_id(quiz_id)
    if quiz is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": QUIZ_NOT_FOUND, "code": "QUIZ_NOT_FOUND"},
        )
    return quiz


async def list_quizzes(club_id: uuid.UUID, db: AsyncSession, skip: int, limit: int) -> list[Quiz]:
    return await QuizRepository(db).list_for_club(club_id, skip=skip, limit=limit)


async def list_questions(quiz_id: uuid.UUID, db: AsyncSession) -> list[QuizQuestion]:
    return await QuizRepository(db).get_questions(quiz_id)


async def count_questions(quiz_id: uuid.UUID, db: AsyncSession) -> int:
    return await QuizRepository(db).count_questions(quiz_id)


async def get_question(question_id: uuid.UUID, quiz_id: uuid.UUID, db: AsyncSession) -> QuizQuestion | None:
    return await QuizRepository(db).get_question(question_id, quiz_id)


async def get_questions_by_ids(
    quiz_id: uuid.UUID, question_ids: list[uuid.UUID], db: AsyncSession
) -> list[QuizQuestion]:
    return await QuizRepository(db).get_questions_by_ids(quiz_id, question_ids)


async def get_active_session(quiz_id: uuid.UUID, db: AsyncSession) -> tuple[QuizSession, int]:
    repo = QuizRepository(db)
    session = await repo.get_active_session(quiz_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": SESSION_NOT_FOUND, "code": "SESSION_NOT_FOUND"},
        )
    participant_count = await repo.count_attempts_since(quiz_id, session.started_at)
    return session, participant_count


async def get_session_or_404(session_id: uuid.UUID, quiz_id: uuid.UUID, db: AsyncSession) -> QuizSession:
    session = await QuizRepository(db).get_session(session_id, quiz_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": SESSION_NOT_FOUND, "code": "SESSION_NOT_FOUND"},
        )
    return session


async def submit_quiz_attempt(
    quiz_id: uuid.UUID,
    current_user: User,
    body: SubmitAttemptRequest,
    db: AsyncSession,
) -> AttemptResponse:
    """Score and persist a quiz attempt for the current user."""
    repo = QuizRepository(db)
    quiz = await _get_quiz_or_404(quiz_id, db)

    if not quiz.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Quiz is not active", "code": "QUIZ_NOT_ACTIVE"},
        )

    questions_db = await repo.get_questions(quiz_id)
    total = len(questions_db)

    score = sum(1 for i, q in enumerate(questions_db) if i < len(body.answers) and body.answers[i] == q.correct_index)

    attempt = QuizAttempt(
        id=uuid.uuid4(),
        quiz_id=quiz_id,
        user_id=current_user.id,
        score=score,
        total=total,
        answers=body.answers,
    )
    db.add(attempt)
    await db.flush()
    await db.commit()
    await db.refresh(attempt)

    return AttemptResponse(
        id=str(attempt.id),
        quizId=str(attempt.quiz_id),
        userId=str(attempt.user_id),
        score=attempt.score,
        total=attempt.total,
        answers=attempt.answers,
    )


async def get_quiz_leaderboard(
    quiz_id: uuid.UUID,
    session_id: uuid.UUID,
    db: AsyncSession,
) -> LeaderboardResponse:
    """Build a ranked leaderboard for a specific quiz session."""
    repo = QuizRepository(db)
    await _get_quiz_or_404(quiz_id, db)

    session_obj = await repo.get_session(session_id, quiz_id)
    if session_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": SESSION_NOT_FOUND, "code": "SESSION_NOT_FOUND"},
        )

    total_questions = await repo.count_questions(quiz_id)

    # M-4: scope attempts to this session via started_at
    rows = await repo.get_attempts_with_users_since(quiz_id, session_obj.started_at)

    seen_users: dict[str, LeaderboardEntry] = {}
    rank = 1
    for attempt, display_name, avatar_url in rows:
        user_id_str = str(attempt.user_id)
        if user_id_str not in seen_users:
            seen_users[user_id_str] = LeaderboardEntry(
                rank=rank,
                userId=user_id_str,
                displayName=display_name,
                avatarUrl=avatar_url,
                score=attempt.score,
                totalQuestions=total_questions,
                hasAttempted=True,
            )
            rank += 1

    return LeaderboardResponse(entries=list(seen_users.values()))
