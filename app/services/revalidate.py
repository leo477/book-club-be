from __future__ import annotations

import asyncio
import uuid

import httpx
import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = structlog.get_logger(__name__)

_TIMEOUT_SECONDS = 2.0
_CONNECT_TIMEOUT_SECONDS = 0.5


async def notify_revalidate(tags: list[str]) -> None:
    """Best-effort webhook to the web app. Never raises; bounded by _TIMEOUT_SECONDS in total."""
    settings = get_settings()
    if not (settings.WEB_REVALIDATE_URL and settings.WEB_REVALIDATE_SECRET):
        return
    try:
        async with (
            asyncio.timeout(_TIMEOUT_SECONDS),
            httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS)) as client,
        ):
            resp = await client.post(
                settings.WEB_REVALIDATE_URL,
                headers={"X-Revalidate-Secret": settings.WEB_REVALIDATE_SECRET},
                json={"tags": tags},
            )
        if resp.status_code >= 400:
            logger.warning("revalidate notify rejected", status_code=resp.status_code, tags=tags)
    except TimeoutError:
        logger.warning("revalidate notify timed out", tags=tags)
    except Exception as exc:
        logger.warning("revalidate notify failed", error=type(exc).__name__, tags=tags)


async def revalidate_club(club_id: uuid.UUID | str, db: AsyncSession | None = None) -> None:
    """Await the web revalidation so the client's next render sees fresh data.

    Call only after the DB commit, once the response is built;
    pass `db` to release its connection first. Adds at most _TIMEOUT_SECONDS of latency;
    no-op when unconfigured.
    """
    if db is not None:
        # End the (read-only) transaction so the pooled connection is not held during the webhook.
        try:
            await db.rollback()
        except SQLAlchemyError as exc:
            # The write is already committed; a dead connection must not turn into a 500.
            logger.warning("revalidate session release failed", error=type(exc).__name__)
    await notify_revalidate(["clubs", f"club:{club_id}"])
