from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.dependencies import get_db_dep
from app.exceptions import AppError
from app.limiter import limiter
from app.models.analytics_event import AnalyticsEvent
from app.schemas.analytics import AnalyticsEventRequest
from app.services import csp_report

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

_MAX_BODY_BYTES = 2048


def _limit_body_size(request: Request) -> None:
    # Content-Length only; chunked bodies are still bounded by the field limits.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_BODY_BYTES:
        raise AppError(413, "Payload too large", "PAYLOAD_TOO_LARGE")


# noinspection PyUnusedLocal
@router.post("/event", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(_limit_body_size)])
@limiter.limit("60/minute")
async def record_event(
    request: Request,  # slowapi requires this exact parameter name
    body: AnalyticsEventRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> Response:
    db.add(AnalyticsEvent(**body.model_dump()))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _read_capped(request: Request) -> bytes | None:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > csp_report.MAX_BODY_BYTES:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > csp_report.MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


# noinspection PyUnusedLocal
@router.post("/csp-report", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def record_csp_report(
    request: Request,  # slowapi requires this exact parameter name
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> Response:
    # Browsers send application/reports+json or application/csp-report, which pydantic bodies reject,
    # and the reply is always 204 so the endpoint is no oracle for what was accepted.
    payload = await _read_capped(request)
    if payload:
        try:
            await csp_report.store(db, csp_report.parse(payload, get_settings()))
        except SQLAlchemyError:
            await db.rollback()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
