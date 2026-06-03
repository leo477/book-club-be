import asyncio
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
import sentry_sdk
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.limiter import limiter
from app.routers import clubs, health, members
from app.routers.auth import router as auth_router
from app.routers.books import router as books_router
from app.routers.chat import router as chat_router
from app.routers.config import router as config_router
from app.routers.events import router as events_router
from app.routers.geocode import router as geocode_router
from app.routers.quizzes import router as quizzes_router
from app.routers.randomizer import router as randomizer_router
from app.routers.upload import router as upload_router
from app.routers.users import router as users_router

logger = structlog.get_logger(__name__)

# Type alias for FastAPI middleware call_next parameter — avoids repeated inline annotation.
_CallNext = Callable[[Request], Awaitable[Response]]


async def _cleanup_inactive_chat_rooms() -> None:
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


_API_DESCRIPTION = (
    "## Book Club REST API\n\n"
    "A REST API for the Book Club platform.\n\n"
    "### Authentication\n"
    "Most endpoints require a **Bearer JWT token** in the `Authorization` header:\n\n"
    "```\n"
    "Authorization: Bearer <access_token>\n"
    "```\n\n"
    "Obtain a token via `POST /api/v1/auth/login` or `POST /api/v1/auth/register`."
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENV,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            traces_sample_rate=0.1,
        )
        logger.info("Sentry initialized", env=settings.ENV)

    redis_pool = aioredis.ConnectionPool.from_url(
        settings.REDIS_URL,
        max_connections=10,
        decode_responses=False,
    )
    _app.state.redis_pool = redis_pool
    logger.info("Redis pool created", url=settings.REDIS_URL)

    proc = await asyncio.create_subprocess_exec(
        "/app/.venv/bin/alembic",
        "upgrade",
        "head",
        cwd="/app",
    )
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed with exit code {proc.returncode}")
    logger.info("Database migrations applied")

    logger.info("Application starting", env=settings.ENV, version="1.0.0")

    # Feature 5: start daily cleanup of inactive (non-event) chat rooms.
    cleanup_task = asyncio.create_task(_cleanup_inactive_chat_rooms())

    yield

    cleanup_task.cancel()
    await asyncio.gather(cleanup_task, return_exceptions=True)

    await redis_pool.aclose()
    logger.info("Redis pool closed")
    logger.info("Application shutting down")


# noinspection PyShadowingNames
def _build_openapi_schema(app: FastAPI) -> dict:  # type: ignore[type-arg]
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
    )
    schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
    for _path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method in ("get", "post", "put", "patch", "delete"):
                tags = operation.get("tags", [])
                if "auth" not in tags and "health" not in tags:
                    operation["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return app.openapi_schema


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    return JSONResponse(status_code=429, content={"detail": str(exc)})


# noinspection PyShadowingNames
def create_app() -> FastAPI:
    settings = get_settings()
    limiter.storage_uri = settings.REDIS_URL  # type: ignore[attr-defined]

    logger.info("CORS allowed origins", origins=settings.ALLOWED_ORIGINS)

    app = FastAPI(
        title="Book Club API",
        version="1.0.0",
        description=_API_DESCRIPTION,
        openapi_tags=[
            {"name": "auth", "description": "Registration, login, logout, current user"},
            {"name": "users", "description": "User profile management and stats"},
            {"name": "clubs", "description": "Book clubs — create, join, manage"},
            {"name": "members", "description": "Club membership and bans"},
            {"name": "events", "description": "Club events — schedule, attend, manage"},
            {"name": "quizzes", "description": "Book quizzes — create, answer, score"},
            {"name": "randomizer", "description": "Random member picker sessions"},
            {"name": "chat", "description": "Club chat rooms and messages"},
            {"name": "geocode", "description": "Photon/OSM geocoding autocomplete"},
            {"name": "health", "description": "Health check"},
        ],
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    @app.get("/docs", include_in_schema=False)
    async def scalar_docs() -> HTMLResponse:
        return HTMLResponse(
            """<!doctype html>
<html>
  <head>
    <title>Book Club API</title>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
  </head>
  <body>
    <script
      id="api-reference"
      data-url="/openapi.json"
      data-configuration='{
        "theme": "purple",
        "defaultHttpClient": {"targetKey": "python", "clientKey": "requests"},
        "hideModels": true
      }'
    ></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
  </body>
</html>"""
        )

    def custom_openapi() -> dict:  # type: ignore[type-arg]
        return _build_openapi_schema(app)

    app.openapi = custom_openapi  # type: ignore[method-assign]

    @app.middleware("http")
    async def logging_middleware(request: Request, call_next: _CallNext) -> Response:
        bound_logger = logger.bind(
            method=request.method,
            url=str(request.url),
            client=request.client.host if request.client else "unknown",
        )
        bound_logger.info("Request received")
        try:
            response = await call_next(request)
        except Exception as exc:
            bound_logger.exception("Unhandled exception", exc_info=exc)
            return JSONResponse(status_code=500, content={"detail": "Internal server error"})
        bound_logger.info("Request completed", status_code=response.status_code)
        return response

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next: _CallNext) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if settings.ENV == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_origin_regex=settings.CORS_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Total-Count"],
    )

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next: _CallNext) -> Response:
        correlation_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        structlog.contextvars.bind_contextvars(request_id=correlation_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-ID"] = correlation_id
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        path_param_uuids_invalid = all(
            e.get("type") in {"uuid_parsing", "uuid_type"} and e.get("loc", (None,))[0] == "path" for e in errors
        )
        if path_param_uuids_invalid:
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        if settings.ENV == "production":
            return JSONResponse(status_code=422, content={"detail": "Invalid request data"})
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(ValidationError)
    async def validation_exception_handler(_request: Request, exc: ValidationError) -> JSONResponse:
        if settings.ENV == "production":
            return JSONResponse(status_code=422, content={"detail": "Invalid request data"})
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @app.exception_handler(Exception)
    async def generic_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    app.include_router(health.router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(clubs.router)
    app.include_router(members.router)
    app.include_router(events_router)
    app.include_router(quizzes_router)
    app.include_router(randomizer_router)
    app.include_router(chat_router)
    app.include_router(geocode_router)
    app.include_router(config_router)
    app.include_router(upload_router)
    app.include_router(books_router)

    return app


app = create_app()
