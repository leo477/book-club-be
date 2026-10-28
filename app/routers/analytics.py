import asyncio
from typing import Annotated

import structlog
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

logger = structlog.get_logger(__name__)

_MAX_BODY_BYTES = 2048
_CSP_BODY_READ_TIMEOUT_SECONDS = 3.0


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


async def _read_capped(request: Request, deadline: float) -> bytes | None:
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
        # wait_for's cancel can be swallowed by the BaseHTTPMiddleware receive task group when it
        # lands as a chunk arrives, and it never fires twice; the deadline is re-checked per chunk.
        if asyncio.get_running_loop().time() >= deadline:
            return None
    return b"".join(chunks)


# noinspection PyUnusedLocal
@router.post("/csp-report", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
@limiter.limit("30/minute")
async def record_csp_report(
    request: Request,  # slowapi requires this exact parameter name
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> Response:
    # Browsers send application/reports+json or application/csp-report, which pydantic bodies reject,
    # and the reply is always 204 so the endpoint is no oracle for what was accepted.
    try:
        # Bounded so an anonymous slow-drip body cannot hold the coroutine open; also covers ClientDisconnect.
        deadline = asyncio.get_running_loop().time() + _CSP_BODY_READ_TIMEOUT_SECONDS
        payload = await asyncio.wait_for(_read_capped(request, deadline), _CSP_BODY_READ_TIMEOUT_SECONDS)
    except Exception:  # CancelledError is a BaseException and still propagates
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if payload:
        try:
            await csp_report.store(db, csp_report.parse(payload, get_settings()))
        except SQLAlchemyError:
            logger.warning("record_csp_report: database error, report dropped")
            await db.rollback()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
