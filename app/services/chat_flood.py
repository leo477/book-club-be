"""Per user+room flood limiter for the chat WebSocket.

The budget is keyed by the authenticated (room, user) pair, never by connection,
so opening extra sockets or tabs does not multiply it. The in-process sliding
window is always enforced; when Redis is reachable a short-TTL shared counter is
enforced as well so the budget also holds across instances. Redis failures are
swallowed and degrade to the in-process window only.

Caveats: the in-process window is dropped when a user's last socket in the room
closes (reconnecting after a full disconnect starts a fresh local window), and
the Redis side is a fixed window, so up to ~2x the budget can pass across a
window boundary. Callers must pass a canonical room id (str(UUID)).
"""

import asyncio
import time
from collections import deque

import redis.asyncio as aioredis
import structlog

logger = structlog.get_logger(__name__)

FLOOD_MAX_MESSAGES = 10
FLOOD_WINDOW_SECONDS = 10
_REDIS_TIMEOUT_SECONDS = 0.5
_REDIS_PREFIX = "ws:flood:"
_REDIS_BACKOFF_SECONDS = 30.0
# Atomic fixed-window counter; also heals a key that somehow lost its TTL.
_INCR_SCRIPT = (
    "local c=redis.call('INCR',KEYS[1]) "
    "if c==1 or redis.call('PTTL',KEYS[1])==-1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end "
    "return c"
)


class ChatFloodLimiter:
    def __init__(self, max_messages: int = FLOOD_MAX_MESSAGES, window: float = FLOOD_WINDOW_SECONDS) -> None:
        self._max = max_messages
        self._window = window
        self._stamps: dict[tuple[str, str], deque[float]] = {}
        self._refs: dict[tuple[str, str], int] = {}
        self._redis_retry_at = 0.0  # circuit breaker: skip Redis until this monotonic time

    def acquire(self, room_id: str, user_id: str) -> None:
        """Register a connection for this user+room."""
        key = (room_id, user_id)
        self._refs[key] = self._refs.get(key, 0) + 1
        self._stamps.setdefault(key, deque(maxlen=self._max))

    def release(self, room_id: str, user_id: str) -> None:
        """Unregister a connection; drop state once the user's last socket in the room is gone."""
        key = (room_id, user_id)
        remaining = self._refs.get(key, 0) - 1
        if remaining > 0:
            self._refs[key] = remaining
            return
        self._refs.pop(key, None)
        self._stamps.pop(key, None)

    def _local_limited(self, room_id: str, user_id: str) -> bool:
        stamps = self._stamps.get((room_id, user_id))
        if stamps is None:  # no registered connection: nothing to track (and nothing would release it)
            return False
        now = time.monotonic()
        stamps.append(now)
        return len(stamps) == self._max and (now - stamps[0]) < self._window

    async def _redis_limited(self, redis: aioredis.Redis, room_id: str, user_id: str) -> bool:
        if time.monotonic() < self._redis_retry_at:
            return False
        key = f"{_REDIS_PREFIX}{room_id}:{user_id}"
        # Arm the breaker before awaiting so concurrent first failures don't stampede Redis.
        self._redis_retry_at = time.monotonic() + _REDIS_BACKOFF_SECONDS
        try:
            async with asyncio.timeout(_REDIS_TIMEOUT_SECONDS):
                count = await redis.eval(_INCR_SCRIPT, 1, key, int(self._window))  # type: ignore[misc]
        except Exception:  # intentional: Redis trouble must never break chat
            logger.debug("Flood limiter Redis unavailable; using in-process window only", exc_info=True)
            return False
        self._redis_retry_at = 0.0
        return isinstance(count, int) and count >= self._max

    async def is_limited(self, room_id: str, user_id: str, redis: aioredis.Redis | None = None) -> bool:
        """Record one inbound frame for user+room and report whether it exceeds the budget."""
        limited = self._local_limited(room_id, user_id)
        if redis is not None and await self._redis_limited(redis, room_id, user_id):
            limited = True
        return limited


flood_limiter = ChatFloodLimiter()
