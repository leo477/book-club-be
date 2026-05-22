from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.quiz import Quiz, QuizAttempt, QuizQuestion, QuizSession
from app.models.user import User
from app.schemas.quizzes import (
    AttemptResponse,
    LeaderboardEntry,
    LeaderboardResponse,
    SubmitAttemptRequest,
)

QUIZ_NOT_FOUND = "Quiz not found"
SESSION_NOT_FOUND = "Session not found"


async def _get_quiz_or_404(quiz_id: uuid.UUID, db: AsyncSession) -> Quiz:
    result = await db.execute(select(Quiz).where(Quiz.id == quiz_id))
    quiz = result.scalar_one_or_none()
    if quiz is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": QUIZ_NOT_FOUND, "code": "QUIZ_NOT_FOUND"},
        )
    return quiz


async def submit_quiz_attempt(
    quiz_id: uuid.UUID,
    current_user: User,
    body: SubmitAttemptRequest,
    db: AsyncSession,
) -> AttemptResponse:
    """Score and persist a quiz attempt for the current user."""
    quiz = await _get_quiz_or_404(quiz_id, db)

    if not quiz.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Quiz is not active", "code": "QUIZ_NOT_ACTIVE"},
        )

    questions_result = await db.execute(
        select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id).order_by(QuizQuestion.position)
    )
    questions_db = questions_result.scalars().all()
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
    await _get_quiz_or_404(quiz_id, db)

    session_result = await db.execute(
        select(QuizSession).where(QuizSession.id == session_id, QuizSession.quiz_id == quiz_id)
    )
    # M-4: retain session object to use started_at for scoping attempts
    session_obj = session_result.scalar_one_or_none()
    if session_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": SESSION_NOT_FOUND, "code": "SESSION_NOT_FOUND"},
        )

    total_questions_result = await db.execute(
        select(func.count()).select_from(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id)
    )
    total_questions = total_questions_result.scalar() or 0

    attempts_result = await db.execute(
        select(QuizAttempt, User.display_name, User.avatar_url)
        .join(User, QuizAttempt.user_id == User.id)
        .where(QuizAttempt.quiz_id == quiz_id, QuizAttempt.created_at >= session_obj.started_at)
        .order_by(QuizAttempt.score.desc(), QuizAttempt.created_at.asc())
    )
    rows = attempts_result.all()

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
