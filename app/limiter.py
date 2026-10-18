from collections.abc import Callable
from typing import Any

import structlog
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.config import get_settings

logger = structlog.get_logger(__name__)

_REDIS_OPTIONS: dict[str, Any] = {"socket_connect_timeout": 1, "socket_timeout": 1}


class FailOpenLimiter(Limiter):
    """Lets requests through when the rate-limit storage errors instead of returning a 500.

    slowapi's own ``swallow_errors`` skips setting ``request.state.view_rate_limit``,
    which its endpoint wrapper then reads, so the error resurfaces as an AttributeError.
    """

    def _check_request_limit(
        self, request: Request, endpoint_func: Callable[..., Any] | None, in_middleware: bool = True
    ) -> None:
        try:
            super()._check_request_limit(request, endpoint_func, in_middleware)
        except RateLimitExceeded:
            raise
        except Exception:
            logger.warning("Rate limit storage unavailable; allowing request", exc_info=True)
            request.state.view_rate_limit = None


def build_limiter(storage_uri: str) -> Limiter:
    return FailOpenLimiter(
        key_func=get_remote_address,
        storage_uri=storage_uri or None,
        storage_options=_REDIS_OPTIONS if storage_uri else {},
    )


limiter = build_limiter(get_settings().REDIS_URL)
