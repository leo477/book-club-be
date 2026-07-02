from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, require_admin
from app.exceptions import AppError
from app.models.support_submission import SupportSubmission, SupportSubmissionLike
from app.models.user import User
from app.schemas.support import (
    CreateSupportSubmissionRequest,
    SupportStatus,
    SupportSubmissionResponse,
    SupportType,
    UpdateSupportStatusRequest,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/support", tags=["support"])


def _build_response(
    submission: SupportSubmission,
    *,
    like_count: int = 0,
    liked_by_me: bool = False,
    hide_author: bool = False,
) -> SupportSubmissionResponse:
    return SupportSubmissionResponse(
        id=str(submission.id),
        authorId=None if hide_author else str(submission.author_id),
        type=submission.type,  # type: ignore[arg-type]
        title=submission.title,
        body=submission.body,
        status=submission.status,  # type: ignore[arg-type]
        createdAt=submission.created_at,
        updatedAt=submission.updated_at,
        likeCount=like_count,
        likedByMe=liked_by_me,
    )


async def _count_likes(submission_id: uuid.UUID, db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(SupportSubmissionLike)
        .where(SupportSubmissionLike.submission_id == submission_id)
    )
    return result.scalar_one()


async def _has_liked(submission_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> bool:
    result = await db.execute(
        select(SupportSubmissionLike.submission_id).where(
            SupportSubmissionLike.submission_id == submission_id,
            SupportSubmissionLike.user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def _bulk_like_counts(submission_ids: list[uuid.UUID], db: AsyncSession) -> dict[uuid.UUID, int]:
    if not submission_ids:
        return {}
    result = await db.execute(
        select(SupportSubmissionLike.submission_id, func.count().label("cnt"))
        .where(SupportSubmissionLike.submission_id.in_(submission_ids))
        .group_by(SupportSubmissionLike.submission_id)
    )
    return {row.submission_id: row.cnt for row in result}


async def _bulk_liked_ids(submission_ids: list[uuid.UUID], user_id: uuid.UUID, db: AsyncSession) -> set[uuid.UUID]:
    if not submission_ids:
        return set()
    result = await db.execute(
        select(SupportSubmissionLike.submission_id).where(
            SupportSubmissionLike.submission_id.in_(submission_ids),
            SupportSubmissionLike.user_id == user_id,
        )
    )
    return {row.submission_id for row in result}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_submission(
    body: CreateSupportSubmissionRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> SupportSubmissionResponse:
    initial_status = "pending" if body.type == "suggestion" else "open"
    submission = SupportSubmission(
        author_id=current_user.id,
        type=body.type,
        title=body.title,
        body=body.body,
        status=initial_status,
    )
    db.add(submission)
    await db.commit()
    await db.refresh(submission)
    logger.info("support submission created", submission_id=str(submission.id), type=submission.type)
    return _build_response(submission)


@router.get("")
async def list_submissions(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    submission_type: Annotated[SupportType | None, Query(alias="type")] = None,
    submission_status: Annotated[SupportStatus | None, Query(alias="status")] = None,
) -> list[SupportSubmissionResponse]:
    stmt = select(SupportSubmission).where(
        or_(
            SupportSubmission.type.in_(("complaint", "comment")),
            (SupportSubmission.type == "suggestion") & (SupportSubmission.status != "rejected"),
        )
    )
    if submission_type is not None:
        stmt = stmt.where(SupportSubmission.type == submission_type)
    if submission_status is not None:
        stmt = stmt.where(SupportSubmission.status == submission_status)

    stmt = stmt.order_by(SupportSubmission.created_at.desc())
    result = await db.execute(stmt)
    submissions = result.scalars().all()

    submission_ids = [s.id for s in submissions]
    like_counts = await _bulk_like_counts(submission_ids, db)
    liked_ids = await _bulk_liked_ids(submission_ids, current_user.id, db)

    is_admin = current_user.role == "admin"
    return [
        _build_response(
            s,
            like_count=like_counts.get(s.id, 0),
            liked_by_me=s.id in liked_ids,
            hide_author=s.type == "complaint" and not is_admin,
        )
        for s in submissions
    ]


@router.patch("/{submission_id}/status")
async def update_submission_status(
    submission_id: uuid.UUID,
    body: UpdateSupportStatusRequest,
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> SupportSubmissionResponse:
    result = await db.execute(select(SupportSubmission).where(SupportSubmission.id == submission_id))
    submission = result.scalar_one_or_none()
    if not submission:
        raise AppError(status.HTTP_404_NOT_FOUND, "Submission not found", "SUBMISSION_NOT_FOUND")

    submission.status = body.status
    await db.commit()
    await db.refresh(submission)
    logger.info("support submission status updated", submission_id=str(submission.id), status=submission.status)

    like_count = await _count_likes(submission.id, db)
    liked_by_me = await _has_liked(submission.id, _admin.id, db)
    return _build_response(submission, like_count=like_count, liked_by_me=liked_by_me)


@router.post("/{submission_id}/like", status_code=status.HTTP_201_CREATED)
async def like_submission(
    submission_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> SupportSubmissionResponse:
    result = await db.execute(select(SupportSubmission).where(SupportSubmission.id == submission_id))
    submission = result.scalar_one_or_none()
    if not submission:
        raise AppError(status.HTTP_404_NOT_FOUND, "Submission not found", "SUBMISSION_NOT_FOUND")

    if submission.type == "suggestion":
        raise AppError(status.HTTP_400_BAD_REQUEST, "Cannot like a suggestion", "CANNOT_LIKE_SUGGESTION")

    if await _has_liked(submission_id, current_user.id, db):
        raise AppError(status.HTTP_409_CONFLICT, "Already liked", "ALREADY_LIKED")

    db.add(SupportSubmissionLike(submission_id=submission_id, user_id=current_user.id))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppError(status.HTTP_409_CONFLICT, "Already liked", "ALREADY_LIKED") from exc

    like_count = await _count_likes(submission_id, db)
    logger.info("support submission liked", submission_id=str(submission_id), user_id=str(current_user.id))
    return _build_response(submission, like_count=like_count, liked_by_me=True)


@router.delete("/{submission_id}/like", status_code=status.HTTP_204_NO_CONTENT)
async def unlike_submission(
    submission_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> None:
    result = await db.execute(select(SupportSubmission).where(SupportSubmission.id == submission_id))
    submission = result.scalar_one_or_none()
    if not submission:
        raise AppError(status.HTTP_404_NOT_FOUND, "Submission not found", "SUBMISSION_NOT_FOUND")

    like_result = await db.execute(
        select(SupportSubmissionLike).where(
            SupportSubmissionLike.submission_id == submission_id,
            SupportSubmissionLike.user_id == current_user.id,
        )
    )
    like = like_result.scalar_one_or_none()
    if like is not None:
        await db.delete(like)
        await db.commit()
        logger.info("support submission unliked", submission_id=str(submission_id), user_id=str(current_user.id))
