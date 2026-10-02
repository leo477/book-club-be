from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db_dep
from app.exceptions import AppError
from app.limiter import limiter
from app.models.analytics_event import AnalyticsEvent
from app.schemas.analytics import AnalyticsEventRequest

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
