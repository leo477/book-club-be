from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep, require_admin
from app.exceptions import AppError
from app.models.support_submission import SupportSubmission
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


def _build_response(submission: SupportSubmission, *, hide_author: bool = False) -> SupportSubmissionResponse:
    return SupportSubmissionResponse(
        id=str(submission.id),
        authorId=None if hide_author else str(submission.author_id),
        type=submission.type,  # type: ignore[arg-type]
        title=submission.title,
        body=submission.body,
        status=submission.status,  # type: ignore[arg-type]
        createdAt=submission.created_at,
        updatedAt=submission.updated_at,
    )


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
    is_admin = current_user.role == "admin"
    return [
        _build_response(s, hide_author=s.type == "complaint" and not is_admin)
        for s in result.scalars().all()
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
    return _build_response(submission)
