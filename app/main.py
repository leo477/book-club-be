from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Callable

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import get_settings
from app.routers import clubs, health, members
from app.routers.auth import router as auth_router
from app.routers.chat import router as chat_router
from app.routers.meetings import router as meetings_router
from app.routers.quizzes import router as quizzes_router
from app.routers.randomizer import router as randomizer_router
from app.routers.users import router as users_router

logger = structlog.get_logger(__name__)

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
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    logger.info("Application starting", env=settings.ENV, version="1.0.0")
    yield
    logger.info("Application shutting down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Book Club API",
        version="1.0.0",
        description=_API_DESCRIPTION,
        openapi_tags=[
            {"name": "auth", "description": "Registration, login, logout, current user"},
            {"name": "users", "description": "User profile management and stats"},
            {"name": "clubs", "description": "Book clubs — create, join, manage"},
            {"name": "members", "description": "Club membership and bans"},
            {"name": "meetings", "description": "Club meeting history"},
            {"name": "quizzes", "description": "Book quizzes — create, answer, score"},
            {"name": "randomizer", "description": "Random member picker sessions"},
            {"name": "chat", "description": "Club chat rooms and messages"},
            {"name": "health", "description": "Health check"},
        ],
        swagger_ui_parameters={"persistAuthorization": True},
        lifespan=lifespan,
    )

    def custom_openapi() -> dict:  # type: ignore[type-arg]
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

    app.openapi = custom_openapi  # type: ignore[method-assign]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def logging_middleware(request: Request, call_next: Callable) -> JSONResponse:  # type: ignore[type-arg]
        bound_logger = logger.bind(
            method=request.method,
            url=str(request.url),
            client=request.client.host if request.client else "unknown",
        )
        bound_logger.info("Request received")
        response = await call_next(request)
        bound_logger.info("Request completed", status_code=response.status_code)
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(ValidationError)
    async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    app.include_router(health.router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(clubs.router)
    app.include_router(members.router)
    app.include_router(meetings_router)
    app.include_router(quizzes_router)
    app.include_router(randomizer_router)
    app.include_router(chat_router)

    return app


app = create_app()
