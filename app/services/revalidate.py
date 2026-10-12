from __future__ import annotations

import uuid

import httpx
import structlog
from fastapi import BackgroundTasks

from app.config import get_settings

logger = structlog.get_logger(__name__)

_TIMEOUT_SECONDS = 2.0


async def notify_revalidate(tags: list[str]) -> None:
    settings = get_settings()
    if not (settings.WEB_REVALIDATE_URL and settings.WEB_REVALIDATE_SECRET):
        return
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                settings.WEB_REVALIDATE_URL,
                headers={"X-Revalidate-Secret": settings.WEB_REVALIDATE_SECRET},
                json={"tags": tags},
            )
        if resp.status_code >= 400:
            logger.warning("revalidate notify rejected", status_code=resp.status_code, tags=tags)
    except httpx.HTTPError as exc:
        logger.warning("revalidate notify failed", error=type(exc).__name__, tags=tags)


def schedule_club_revalidate(background_tasks: BackgroundTasks, club_id: uuid.UUID | str) -> None:
    background_tasks.add_task(notify_revalidate, ["clubs", f"club:{club_id}"])
