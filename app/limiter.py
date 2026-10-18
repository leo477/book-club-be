import time
from collections.abc import Callable
from typing import Any

import structlog
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter, RateLimiter
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.config import get_settings

logger = structlog.get_logger(__name__)

_REDIS_OPTIONS: dict[str, Any] = {"socket_connect_timeout": 0.3, "socket_timeout": 0.3}
_BREAKER_COOLDOWN_SECONDS = 15.0


class ResilientLimiter(Limiter):
    """Falls back to per-process in-memory counters while the Redis storage is failing.

    The route limits and key functions registered on this limiter are reused as-is; only the
    strategy that counts hits is swapped. After a storage error the breaker stays open for a
    cooldown so a dead Redis is not hit (and waited on) per request, then one request probes it.
    slowapi's own ``swallow_errors`` and in-memory fallback do not fit: the former leaves
    ``request.state.view_rate_limit`` unset, which the endpoint wrapper then reads, and the
    latter applies a single global limit list instead of the per-route limits.
    """

    _clock = staticmethod(time.monotonic)

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        super().__init__(*args, **kwargs)
        self._fallback_strategy: RateLimiter = FixedWindowRateLimiter(MemoryStorage())
        self._open_until: float | None = None
        self._degraded = False

    @property
    def limiter(self) -> RateLimiter:
        if self._open_until is not None and self._clock() < self._open_until:
            return self._fallback_strategy
        return self._limiter

    def _check_request_limit(
        self, request: Request, endpoint_func: Callable[..., Any] | None, in_middleware: bool = True
    ) -> None:
        try:
            super()._check_request_limit(request, endpoint_func, in_middleware)
        except RateLimitExceeded:
            raise
        except Exception as exc:
            if not self._degraded:
                logger.warning("Rate limit storage unavailable; using in-memory limits", error=type(exc).__name__)
            self._degraded = True
            self._open_until = self._clock() + _BREAKER_COOLDOWN_SECONDS
            super()._check_request_limit(request, endpoint_func, in_middleware)
        else:
            if self._degraded and self._open_until is not None and self._clock() >= self._open_until:
                logger.info("Rate limit storage recovered")
                self._degraded = False
                self._open_until = None


def build_limiter(storage_uri: str, env: str = "development") -> Limiter:
    options: dict[str, Any] = {**_REDIS_OPTIONS, "key_prefix": f"LIMITS:{env}"} if storage_uri else {}
    return ResilientLimiter(
        key_func=get_remote_address,
        storage_uri=storage_uri or None,
        storage_options=options,
    )


_settings = get_settings()
limiter = build_limiter(_settings.REDIS_URL, _settings.ENV)
