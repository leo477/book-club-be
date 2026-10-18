from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from limits.storage import MemoryStorage, RedisStorage
from slowapi.errors import RateLimitExceeded

from app.limiter import build_limiter

UNREACHABLE_REDIS = "redis://127.0.0.1:1"


def test_memory_storage_when_url_unset():
    assert isinstance(build_limiter("")._storage, MemoryStorage)


def test_redis_storage_when_url_set():
    limiter = build_limiter("redis://localhost:6379")
    assert isinstance(limiter._storage, RedisStorage)
    assert limiter._limiter.storage is limiter._storage


def _make_app(storage_uri: str) -> FastAPI:
    limiter = build_limiter(storage_uri)
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


async def test_limit_is_enforced_through_storage():
    app = _make_app("")
    assert await _statuses(app, 3) == [200, 200, 429]


async def test_unreachable_redis_does_not_break_requests():
    app = _make_app(UNREACHABLE_REDIS)
    assert await _statuses(app, 3) == [200, 200, 200]


def test_default_limiter_uses_memory_under_tests():
    from app.limiter import limiter

    assert isinstance(limiter._storage, MemoryStorage)
