import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, is_club_organizer, require_club_organizer
from app.exceptions import AppError
from app.models.quiz import Quiz, QuizQuestion, QuizSession
from app.models.user import User
from app.repositories import QuizRepository
from app.schemas.quizzes import (
    AddQuestionRequest,
    AttemptResponse,
    CreateQuizRequest,
    CreateSessionRequest,
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
from app.services.quiz_service import (
    count_questions,
    get_active_session,
    get_question,
    get_questions_by_ids,
    get_quiz_leaderboard,
    get_session_or_404,
    list_questions,
    list_quizzes,
    submit_quiz_attempt,
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
    quiz = await QuizRepository(db).get_by_id(quiz_id)
    if quiz is None:
        raise AppError(status.HTTP_404_NOT_FOUND, QUIZ_NOT_FOUND, "QUIZ_NOT_FOUND")
    return quiz


@router.get("/clubs/{club_id}/quizzes", status_code=status.HTTP_200_OK)
async def get_quizzes(
    club_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[QuizResponse]:
    quizzes = await list_quizzes(club_id, db, skip=skip, limit=limit)
    return [_quiz_response(q) for q in quizzes]


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

    questions_db = await list_questions(quiz_id, db)

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

    position = await count_questions(quiz_id, db)

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

    question = await get_question(question_id, quiz_id, db)
    if question is None:
        raise AppError(status.HTTP_404_NOT_FOUND, QUESTION_NOT_FOUND, "QUESTION_NOT_FOUND")

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

    question = await get_question(question_id, quiz_id, db)
    if question is None:
        raise AppError(status.HTTP_404_NOT_FOUND, QUESTION_NOT_FOUND, "QUESTION_NOT_FOUND")

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
    questions = await get_questions_by_ids(quiz_id, ordered_ids, db)
    questions_map = {q.id: q for q in questions}
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
    return await submit_quiz_attempt(quiz_id, current_user, req, db)


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
async def get_active_session_route(
    quiz_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> QuizSessionResponse:
    await _get_quiz_or_404(quiz_id, db)
    session, participant_count = await get_active_session(quiz_id, db)
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
    return await get_quiz_leaderboard(quiz_id, session_id, db)


@router.patch("/quizzes/{quiz_id}/sessions/{session_id}/close", status_code=status.HTTP_204_NO_CONTENT)
async def close_session(
    quiz_id: uuid.UUID,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    quiz = await _get_quiz_or_404(quiz_id, db)
    await require_club_organizer(quiz.club_id, current_user, db)

    session = await get_session_or_404(session_id, quiz_id, db)
    session.closed_at = datetime.now(UTC)
    await db.commit()
