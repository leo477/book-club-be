import uuid

import httpx
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push_token import PushToken

logger = structlog.get_logger(__name__)

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


async def send_expo_push_notifications(
    user_ids: list[uuid.UUID],
    title: str,
    body: str,
    data: dict[str, str],
    db: AsyncSession,
) -> None:
    """Best-effort push notification fan-out via Expo's push API.

    This is a side effect, not part of the request's core logic: it must never
    raise into the caller. Callers should fire this via ``asyncio.create_task``
    rather than awaiting it inline so a slow/failing push send never delays the
    actual API response.
    """
    if not user_ids:
        return

    try:
        tokens_result = await db.execute(select(PushToken.token).where(PushToken.user_id.in_(user_ids)))
        tokens = list(tokens_result.scalars().all())
    except Exception as exc:
        logger.error("Failed to load push tokens", error=str(exc), exc_type=type(exc).__name__)
        return

    if not tokens:
        return

    messages = [{"to": token, "title": title, "body": body, "data": data} for token in tokens]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                _EXPO_PUSH_URL,
                json=messages,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate",
                },
            )
            response.raise_for_status()
            response_data = response.json()
    except Exception as exc:
        logger.error("Expo push send failed", error=str(exc), exc_type=type(exc).__name__)
        return

    entries = response_data.get("data") or []
    stale_tokens = [
        tokens[i]
        for i, entry in enumerate(entries)
        if i < len(tokens)
        and entry.get("status") == "error"
        and (entry.get("details") or {}).get("error") == "DeviceNotRegistered"
    ]
    if stale_tokens:
        try:
            await db.execute(delete(PushToken).where(PushToken.token.in_(stale_tokens)))
            await db.commit()
        except Exception as exc:
            logger.error("Failed to clean up stale push tokens", error=str(exc), exc_type=type(exc).__name__)
