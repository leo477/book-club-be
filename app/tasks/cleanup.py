import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

_EVENT_CHAT_GRACE_PERIOD = timedelta(days=5)


def _countdown_text(days_left: int) -> str:
    if days_left <= 0:
        return "Чат події буде видалено сьогодні."
    if days_left == 1:
        return "Залишився 1 день до видалення цього чату."
    if 2 <= days_left <= 4:
        return f"Залишилось {days_left} дні до видалення цього чату."
    return f"Залишилось {days_left} днів до видалення цього чату."


async def run_event_chat_lifecycle_pass(db: AsyncSession, *, now: datetime | None = None) -> None:
    """One pass of the event-chat lifecycle: post a daily countdown message for rooms
    within the 5-day post-event grace period, and delete rooms past it. Split out from
    the scheduling loop below so it can be exercised directly in tests against a
    caller-provided session, without going through `asyncio.sleep`/`AsyncSessionLocal`."""
    from sqlalchemy import delete, select

    from app.config import SYSTEM_USER_ID
    from app.models.chat import ChatMessage, ChatRoom, ChatRoomBan, MessageRead
    from app.models.event import Event
    from app.routers.chat import manager as ws_manager

    now = now or datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    rooms_result = await db.execute(
        select(ChatRoom, Event).join(Event, Event.id == ChatRoom.event_id).where(Event.date < now)
    )
    rows = rooms_result.all()

    expired_ids: list[uuid.UUID] = []
    for room, event in rows:
        event_date = event.date if event.date.tzinfo is not None else event.date.replace(tzinfo=UTC)
        deletion_at = event_date + _EVENT_CHAT_GRACE_PERIOD
        if now >= deletion_at:
            expired_ids.append(room.id)
            continue

        days_left = (deletion_at - now).days
        already_posted_today = await db.scalar(
            select(ChatMessage.id).where(
                ChatMessage.room_id == room.id,
                ChatMessage.sender_id == SYSTEM_USER_ID,
                ChatMessage.timestamp >= today_start,
            )
        )
        if already_posted_today is not None:
            continue

        text = _countdown_text(days_left)
        bot_msg = ChatMessage(room_id=room.id, sender_id=SYSTEM_USER_ID, text=text)
        db.add(bot_msg)
        await db.commit()
        await db.refresh(bot_msg)

        await ws_manager.broadcast(
            str(room.id),
            {
                "type": "message",
                "payload": {
                    "id": str(bot_msg.id),
                    "senderId": str(SYSTEM_USER_ID),
                    "senderName": "Book Club Bot",
                    "text": text,
                    "timestamp": bot_msg.timestamp.isoformat(),
                    "isSystem": True,
                },
            },
        )
        logger.info("cleanup_expired_event_chat_rooms: posted countdown", room_id=str(room.id))

    if expired_ids:
        await db.execute(delete(MessageRead).where(MessageRead.room_id.in_(expired_ids)))
        await db.execute(delete(ChatRoomBan).where(ChatRoomBan.room_id.in_(expired_ids)))
        await db.execute(delete(ChatMessage).where(ChatMessage.room_id.in_(expired_ids)))
        await db.execute(delete(ChatRoom).where(ChatRoom.id.in_(expired_ids)))
        await db.commit()
        logger.info("cleanup_expired_event_chat_rooms: deleted expired rooms", count=len(expired_ids))


async def cleanup_expired_event_chat_rooms() -> None:
    """Event chat rooms stay active for 5 days after the event date, with the Book Club
    Bot posting a daily countdown message, then get deleted. Runs hourly (not daily like
    the club-room cleanup) so the once-per-day bot message stays close to real time; the
    "one message per calendar day" invariant is enforced by checking for an existing
    system message today, not by the run interval."""
    from app.database import AsyncSessionLocal

    while True:
        try:
            await asyncio.sleep(3600)  # wait 1 h between runs
            async with AsyncSessionLocal() as db:
                await run_event_chat_lifecycle_pass(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("cleanup_expired_event_chat_rooms: unexpected error", exc_info=exc)


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
