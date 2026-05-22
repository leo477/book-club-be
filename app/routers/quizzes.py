import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, is_club_organizer, require_club_organizer
from app.models.quiz import Quiz, QuizAttempt, QuizQuestion, QuizSession
from app.models.user import User
from app.schemas.quizzes import (
    AddQuestionRequest,
    AttemptResponse,
    CreateQuizRequest,
    CreateSessionRequest,
    LeaderboardEntry,
    LeaderboardResponse,
    QuizQuestionResponse,
    QuizResponse,
    QuizSessionResponse,
    ReorderQuestionsRequest,
    SetActiveRequest,
    SubmitAttemptRequest,
    UpdateQuestionRequest,
    UpdateQuizRequest,
)

QUIZ_NOT_FOUND = "Quiz not found"
QUESTION_NOT_FOUND = "Question not found"
SESSION_NOT_FOUND = "Session not found"

router = APIRouter(prefix="/api/v1", tags=["quizzes"])


def _quiz_response(q: Quiz) -> QuizResponse:
    return QuizResponse(
        id=str(q.id),
        clubId=str(q.club_id),
        createdBy=str(q.created_by),
        title=q.title,
        description=q.description,
        isActive=q.is_active,
        status="active" if q.is_active else "draft",
    )


def _session_response(s: QuizSession, participant_count: int) -> QuizSessionResponse:
    return QuizSessionResponse(
        id=str(s.id),
        quizId=str(s.quiz_id),
        eventId=str(s.event_id) if s.event_id else None,
        startedBy=str(s.started_by),
        startedAt=s.started_at.isoformat(),
        closedAt=s.closed_at.isoformat() if s.closed_at else None,
        participantCount=participant_count,
    )


async def _get_quiz_or_404(quiz_id: uuid.UUID, db: AsyncSession) -> Quiz:
    result = await db.execute(select(Quiz).where(Quiz.id == quiz_id))
    quiz = result.scalar_one_or_none()
    if quiz is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": QUIZ_NOT_FOUND, "code": "QUIZ_NOT_FOUND"},
        )
    return quiz


@router.get("/clubs/{club_id}/quizzes", status_code=status.HTTP_200_OK)
async def get_quizzes(
    club_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[QuizResponse]:
    result = await db.execute(select(Quiz).where(Quiz.club_id == club_id).offset(skip).limit(limit))
    return [_quiz_response(q) for q in result.scalars().all()]


@router.post("/clubs/{club_id}/quizzes", status_code=status.HTTP_201_CREATED)
async def create_quiz(
    club_id: uuid.UUID,
    req: CreateQuizRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> QuizResponse:
    await require_club_organizer(club_id, current_user, db)

    quiz = Quiz(
        id=uuid.uuid4(),
        club_id=club_id,
        created_by=current_user.id,
        title=req.title,
        description=req.description,
        is_active=False,
    )
    db.add(quiz)
    await db.flush()
    await db.commit()
    await db.refresh(quiz)
    return _quiz_response(quiz)


@router.get("/quizzes/{quiz_id}", status_code=status.HTTP_200_OK)
async def get_quiz(
    quiz_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> QuizResponse:
    quiz = await _get_quiz_or_404(quiz_id, db)
    return _quiz_response(quiz)


@router.patch("/quizzes/{quiz_id}", status_code=status.HTTP_200_OK)
async def update_quiz(
    quiz_id: uuid.UUID,
    req: UpdateQuizRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> QuizResponse:
    quiz = await _get_quiz_or_404(quiz_id, db)
    await require_club_organizer(quiz.club_id, current_user, db)

    quiz.title = req.title
    quiz.description = req.description
    await db.commit()
    await db.refresh(quiz)
    return _quiz_response(quiz)


@router.get(
    "/quizzes/{quiz_id}/questions",
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
)
async def get_questions(
    quiz_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[QuizQuestionResponse]:
    quiz = await _get_quiz_or_404(quiz_id, db)
    organizer = await is_club_organizer(quiz.club_id, current_user.id, db)

    questions_result = await db.execute(
        select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id).order_by(QuizQuestion.position)
    )
    questions_db = questions_result.scalars().all()

    return [
        QuizQuestionResponse(
            id=str(q.id),
            quizId=str(q.quiz_id),
            question=q.question,
            options=q.options,
            correctIndex=q.correct_index if organizer else None,
            position=q.position,
        )
        for q in questions_db
    ]


@router.post("/quizzes/{quiz_id}/questions", status_code=status.HTTP_201_CREATED)
async def add_question(
    quiz_id: uuid.UUID,
    req: AddQuestionRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> QuizQuestionResponse:
    quiz = await _get_quiz_or_404(quiz_id, db)
    await require_club_organizer(quiz.club_id, current_user, db)

    count_result = await db.execute(
        select(func.count()).select_from(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id)
    )
    position = count_result.scalar() or 0

    question = QuizQuestion(
        id=uuid.uuid4(),
        quiz_id=quiz_id,
        question=req.question,
        options=req.options,
        correct_index=req.correctIndex,
        position=position,
    )
    db.add(question)
    await db.flush()
    await db.commit()
    await db.refresh(question)

    return QuizQuestionResponse(
        id=str(question.id),
        quizId=str(question.quiz_id),
        question=question.question,
        options=question.options,
        correctIndex=question.correct_index,
        position=question.position,
    )


@router.patch(
    "/quizzes/{quiz_id}/questions/{question_id}",
    status_code=status.HTTP_200_OK,
)
async def update_question(
    quiz_id: uuid.UUID,
    question_id: uuid.UUID,
    req: UpdateQuestionRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> QuizQuestionResponse:
    quiz = await _get_quiz_or_404(quiz_id, db)
    await require_club_organizer(quiz.club_id, current_user, db)

    q_result = await db.execute(
        select(QuizQuestion).where(QuizQuestion.id == question_id, QuizQuestion.quiz_id == quiz_id)
    )
    question = q_result.scalar_one_or_none()
    if question is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": QUESTION_NOT_FOUND, "code": "QUESTION_NOT_FOUND"},
        )

    if req.question is not None:
        question.question = req.question
    if req.options is not None:
        question.options = req.options
    if req.correctIndex is not None:
        question.correct_index = req.correctIndex

    await db.commit()
    await db.refresh(question)

    return QuizQuestionResponse(
        id=str(question.id),
        quizId=str(question.quiz_id),
        question=question.question,
        options=question.options,
        correctIndex=question.correct_index,
        position=question.position,
    )


@router.delete("/quizzes/{quiz_id}/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(
    quiz_id: uuid.UUID,
    question_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    quiz = await _get_quiz_or_404(quiz_id, db)
    await require_club_organizer(quiz.club_id, current_user, db)

    q_result = await db.execute(
        select(QuizQuestion).where(QuizQuestion.id == question_id, QuizQuestion.quiz_id == quiz_id)
    )
    question = q_result.scalar_one_or_none()
    if question is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": QUESTION_NOT_FOUND, "code": "QUESTION_NOT_FOUND"},
        )

    await db.delete(question)
    await db.commit()


@router.put("/quizzes/{quiz_id}/questions/order", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_questions(
    quiz_id: uuid.UUID,
    req: ReorderQuestionsRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    quiz = await _get_quiz_or_404(quiz_id, db)
    await require_club_organizer(quiz.club_id, current_user, db)

    # M-2: load all questions in a single query instead of N per-item SELECTs
    ordered_ids = [uuid.UUID(qid) for qid in req.order]
    q_result = await db.execute(
        select(QuizQuestion).where(
            QuizQuestion.quiz_id == quiz_id,
            QuizQuestion.id.in_(ordered_ids),
        )
    )
    questions_map = {q.id: q for q in q_result.scalars().all()}
    for position, qid in enumerate(ordered_ids):
        question = questions_map.get(qid)
        if question is not None:
            question.position = position

    await db.commit()


@router.patch("/quizzes/{quiz_id}/active", status_code=status.HTTP_200_OK)
async def set_active(
    quiz_id: uuid.UUID,
    req: SetActiveRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> QuizResponse:
    quiz = await _get_quiz_or_404(quiz_id, db)
    await require_club_organizer(quiz.club_id, current_user, db)

    quiz.is_active = req.isActive
    await db.flush()
    await db.commit()
    await db.refresh(quiz)
    return _quiz_response(quiz)


@router.post("/quizzes/{quiz_id}/attempts", status_code=status.HTTP_201_CREATED)
async def submit_attempt(
    quiz_id: uuid.UUID,
    req: SubmitAttemptRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AttemptResponse:
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

    score = sum(1 for i, q in enumerate(questions_db) if i < len(req.answers) and req.answers[i] == q.correct_index)

    attempt = QuizAttempt(
        id=uuid.uuid4(),
        quiz_id=quiz_id,
        user_id=current_user.id,
        score=score,
        total=total,
        answers=req.answers,
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


@router.post("/quizzes/{quiz_id}/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    quiz_id: uuid.UUID,
    req: CreateSessionRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> QuizSessionResponse:
    quiz = await _get_quiz_or_404(quiz_id, db)
    await require_club_organizer(quiz.club_id, current_user, db)

    session = QuizSession(
        id=uuid.uuid4(),
        quiz_id=quiz_id,
        event_id=req.eventId,
        started_by=current_user.id,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return _session_response(session, participant_count=0)


@router.get("/quizzes/{quiz_id}/sessions/active", status_code=status.HTTP_200_OK)
async def get_active_session(
    quiz_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> QuizSessionResponse:
    await _get_quiz_or_404(quiz_id, db)

    result = await db.execute(
        select(QuizSession)
        .where(QuizSession.quiz_id == quiz_id, QuizSession.closed_at.is_(None))
        .order_by(QuizSession.started_at.desc())
        .limit(1)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": SESSION_NOT_FOUND, "code": "SESSION_NOT_FOUND"},
        )

    # M-3: scope participant count to this session only (attempts after session started)
    count_result = await db.execute(
        select(func.count())
        .select_from(QuizAttempt)
        .where(QuizAttempt.quiz_id == quiz_id, QuizAttempt.created_at >= session.started_at)
    )
    participant_count = count_result.scalar() or 0

    return _session_response(session, participant_count)


@router.get(
    "/quizzes/{quiz_id}/sessions/{session_id}/leaderboard",
    status_code=status.HTTP_200_OK,
)
async def get_leaderboard(
    quiz_id: uuid.UUID,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> LeaderboardResponse:
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


@router.patch("/quizzes/{quiz_id}/sessions/{session_id}/close", status_code=status.HTTP_204_NO_CONTENT)
async def close_session(
    quiz_id: uuid.UUID,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    quiz = await _get_quiz_or_404(quiz_id, db)
    await require_club_organizer(quiz.club_id, current_user, db)

    session_result = await db.execute(
        select(QuizSession).where(QuizSession.id == session_id, QuizSession.quiz_id == quiz_id)
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": SESSION_NOT_FOUND, "code": "SESSION_NOT_FOUND"},
        )

    session.closed_at = datetime.now(UTC)
    await db.commit()
