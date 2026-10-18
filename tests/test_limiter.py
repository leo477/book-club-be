import inspect

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from limits.storage import MemoryStorage, RedisStorage
from limits.strategies import FixedWindowRateLimiter
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.limiter import _BREAKER_COOLDOWN_SECONDS, _REDIS_OPTIONS, ResilientLimiter, build_limiter

UNREACHABLE_REDIS = "redis://127.0.0.1:1"


class FlakyStrategy(FixedWindowRateLimiter):
    """Counts in memory but raises like a dead Redis while ``down`` is set, recording each call."""

    def __init__(self) -> None:
        super().__init__(MemoryStorage())
        self.down = True
        self.calls = 0

    def hit(self, item, *identifiers, cost=1):
        self.calls += 1
        if self.down:
            raise ConnectionError("redis is down")
        return super().hit(item, *identifiers, cost=cost)


def _make_app(limiter: Limiter) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _handler(_request: Request, _exc: RateLimitExceeded):
        return JSONResponse(status_code=429, content={})

    @app.get("/ping")
    @limiter.limit("2/minute")
    async def ping(request: Request):
        return {"ok": True}

    return app


async def _statuses(app: FastAPI, n: int) -> list[int]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return [(await client.get("/ping")).status_code for _ in range(n)]


def _flaky_limiter() -> tuple[ResilientLimiter, FlakyStrategy, list[float]]:
    limiter = build_limiter("")
    assert isinstance(limiter, ResilientLimiter)
    strategy = FlakyStrategy()
    limiter._limiter = strategy
    now = [1000.0]
    limiter._clock = lambda: now[0]
    return limiter, strategy, now


def test_memory_storage_when_url_unset():
    assert isinstance(build_limiter("")._storage, MemoryStorage)


def test_redis_storage_when_url_set():
    limiter = build_limiter("redis://localhost:6379")
    assert isinstance(limiter._storage, RedisStorage)
    assert limiter._limiter.storage is limiter._storage


def test_redis_key_prefix_is_per_environment():
    prod = build_limiter("redis://localhost:6379", "production")._storage
    staging = build_limiter("redis://localhost:6379", "staging")._storage
    assert prod.prefixed_key("k") == "LIMITS:production:k"
    assert staging.prefixed_key("k") == "LIMITS:staging:k"


def test_socket_timeouts_reach_the_redis_client():
    storage = build_limiter("redis://localhost:6379")._storage
    kwargs = storage.storage.connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] == _REDIS_OPTIONS["socket_connect_timeout"] == 0.3
    assert kwargs["socket_timeout"] == _REDIS_OPTIONS["socket_timeout"] == 0.3


def test_slowapi_check_request_limit_signature():
    params = list(inspect.signature(Limiter._check_request_limit).parameters)
    assert params == ["self", "request", "endpoint_func", "in_middleware"]


async def test_limit_is_enforced_through_storage():
    app = _make_app(build_limiter(""))
    assert await _statuses(app, 3) == [200, 200, 429]


async def test_fallback_enforces_route_limit_while_redis_is_down():
    limiter, _, _ = _flaky_limiter()
    assert await _statuses(_make_app(limiter), 3) == [200, 200, 429]


async def test_unreachable_redis_still_enforces_limit():
    app = _make_app(build_limiter(UNREACHABLE_REDIS))
    assert await _statuses(app, 3) == [200, 200, 429]


async def test_breaker_skips_redis_during_cooldown():
    limiter, strategy, now = _flaky_limiter()
    app = _make_app(limiter)
    await _statuses(app, 1)
    assert strategy.calls == 1
    now[0] += _BREAKER_COOLDOWN_SECONDS - 1
    await _statuses(app, 1)
    assert strategy.calls == 1


async def test_recovers_after_cooldown():
    limiter, strategy, now = _flaky_limiter()
    app = _make_app(limiter)
    await _statuses(app, 1)
    strategy.down = False
    now[0] += _BREAKER_COOLDOWN_SECONDS + 1
    assert await _statuses(app, 1) == [200]
    assert strategy.calls == 2
    assert limiter._open_until is None
    assert limiter.limiter is strategy


async def test_failed_probe_reopens_breaker():
    limiter, strategy, now = _flaky_limiter()
    app = _make_app(limiter)
    await _statuses(app, 1)
    now[0] += _BREAKER_COOLDOWN_SECONDS + 1
    await _statuses(app, 2)
    assert strategy.calls == 2


def test_default_limiter_uses_memory_under_tests():
    from app.limiter import limiter

    assert isinstance(limiter._storage, MemoryStorage)
