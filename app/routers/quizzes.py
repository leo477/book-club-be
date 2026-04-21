import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, is_club_organizer, require_club_organizer
from app.models.quiz import Quiz, QuizAttempt, QuizQuestion
from app.models.user import User
from app.schemas.quizzes import (
    AddQuestionRequest,
    AttemptResponse,
    CreateQuizRequest,
    QuizQuestionResponse,
    QuizResponse,
    SetActiveRequest,
    SubmitAttemptRequest,
)

QUIZ_NOT_FOUND = "Quiz not found"

router = APIRouter(prefix="/api/v1", tags=["quizzes"])


def _quiz_response(q: Quiz) -> QuizResponse:
    return QuizResponse(
        id=str(q.id),
        clubId=str(q.club_id),
        createdBy=str(q.created_by),
        title=q.title,
        description=q.description,
        isActive=q.is_active,
    )


@router.get(
    "/clubs/{club_id}/quizzes",
    status_code=status.HTTP_200_OK,
)
async def get_quizzes(
    club_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[QuizResponse]:
    result = await db.execute(select(Quiz).where(Quiz.club_id == club_id).offset(skip).limit(limit))
    return [_quiz_response(q) for q in result.scalars().all()]


@router.post(
    "/clubs/{club_id}/quizzes",
    status_code=status.HTTP_201_CREATED,
)
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
    quiz_result = await db.execute(select(Quiz).where(Quiz.id == quiz_id))
    quiz = quiz_result.scalar_one_or_none()
    if quiz is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": QUIZ_NOT_FOUND, "code": "QUIZ_NOT_FOUND"},
        )

    organizer = await is_club_organizer(quiz.club_id, current_user.id, db)

    questions_result = await db.execute(select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id))
    questions_db = questions_result.scalars().all()

    return [
        QuizQuestionResponse(
            id=str(q.id),
            quizId=str(q.quiz_id),
            question=q.question,
            options=q.options,
            correctIndex=q.correct_index if organizer else None,
        )
        for q in questions_db
    ]


@router.post(
    "/quizzes/{quiz_id}/questions",
    status_code=status.HTTP_201_CREATED,
)
async def add_question(
    quiz_id: uuid.UUID,
    req: AddQuestionRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> QuizQuestionResponse:
    quiz_result = await db.execute(select(Quiz).where(Quiz.id == quiz_id))
    quiz = quiz_result.scalar_one_or_none()
    if quiz is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": QUIZ_NOT_FOUND, "code": "QUIZ_NOT_FOUND"},
        )

    await require_club_organizer(quiz.club_id, current_user, db)

    question = QuizQuestion(
        id=uuid.uuid4(),
        quiz_id=quiz_id,
        question=req.question,
        options=req.options,
        correct_index=req.correctIndex,
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
    )


@router.patch(
    "/quizzes/{quiz_id}/active",
    status_code=status.HTTP_200_OK,
)
async def set_active(
    quiz_id: uuid.UUID,
    req: SetActiveRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> QuizResponse:
    quiz_result = await db.execute(select(Quiz).where(Quiz.id == quiz_id))
    quiz = quiz_result.scalar_one_or_none()
    if quiz is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": QUIZ_NOT_FOUND, "code": "QUIZ_NOT_FOUND"},
        )

    await require_club_organizer(quiz.club_id, current_user, db)

    quiz.is_active = req.isActive
    await db.flush()
    await db.commit()
    await db.refresh(quiz)
    return _quiz_response(quiz)


@router.post(
    "/quizzes/{quiz_id}/attempts",
    status_code=status.HTTP_201_CREATED,
)
async def submit_attempt(
    quiz_id: uuid.UUID,
    req: SubmitAttemptRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AttemptResponse:
    quiz_result = await db.execute(select(Quiz).where(Quiz.id == quiz_id))
    quiz = quiz_result.scalar_one_or_none()
    if quiz is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": QUIZ_NOT_FOUND, "code": "QUIZ_NOT_FOUND"},
        )

    if not quiz.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Quiz is not active", "code": "QUIZ_NOT_ACTIVE"},
        )

    questions_result = await db.execute(select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id))
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
