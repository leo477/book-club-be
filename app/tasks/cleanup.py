import asyncio
from datetime import UTC, datetime, timedelta

import structlog

logger = structlog.get_logger(__name__)


async def cleanup_inactive_chat_rooms() -> None:
    """Feature 5: daily background task — deletes non-event chat rooms with no messages
    in the last 30 days AND created more than 30 days ago."""
    from sqlalchemy import delete, select

    from app.database import AsyncSessionLocal
    from app.models.chat import ChatMessage, ChatRoom, ChatRoomBan, MessageRead

    while True:
        try:
            await asyncio.sleep(86400)  # wait 24 h between runs
            cutoff = datetime.now(UTC) - timedelta(days=30)
            async with AsyncSessionLocal() as db:
                # Find stale rooms: no event_id, created > 30 days ago, no recent messages.
                recent_msg_subq = (
                    select(ChatMessage.room_id).where(ChatMessage.timestamp >= cutoff).distinct().scalar_subquery()
                )
                stale_rooms_result = await db.execute(
                    select(ChatRoom.id).where(
                        ChatRoom.event_id.is_(None),
                        ChatRoom.created_at < cutoff,
                        ChatRoom.id.not_in(recent_msg_subq),
                    )
                )
                stale_ids = [row[0] for row in stale_rooms_result.all()]

                if not stale_ids:
                    logger.info("cleanup_inactive_chat_rooms: no stale rooms found")
                    continue

                await db.execute(delete(MessageRead).where(MessageRead.room_id.in_(stale_ids)))
                await db.execute(delete(ChatRoomBan).where(ChatRoomBan.room_id.in_(stale_ids)))
                await db.execute(delete(ChatMessage).where(ChatMessage.room_id.in_(stale_ids)))
                await db.execute(delete(ChatRoom).where(ChatRoom.id.in_(stale_ids)))
                await db.commit()
                logger.info("cleanup_inactive_chat_rooms: deleted stale rooms", count=len(stale_ids))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("cleanup_inactive_chat_rooms: unexpected error", exc_info=exc)
