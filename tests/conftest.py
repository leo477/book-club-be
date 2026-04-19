import json
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from supabase_auth.errors import AuthApiError

from app.config import Settings
from app.database import Base, get_db
from app.dependencies import get_db_dep, get_settings_dep
from app.main import app

# SQLite doesn't support PostgreSQL-specific types; map them to JSON for testing
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "JSON"  # type: ignore[method-assign]
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "JSON"  # type: ignore[method-assign]

_orig_array_bind = ARRAY.bind_processor
_orig_array_result = ARRAY.result_processor


def _array_bind(self, dialect):
    if dialect.name != "postgresql":

        def process(value):
            return json.dumps(value) if value is not None else None

        return process
    return _orig_array_bind(self, dialect)


def _array_result(self, dialect, coltype):
    if dialect.name != "postgresql":

        def process(value):
            if value is None:
                return None
            return json.loads(value) if isinstance(value, str) else value

        return process
    return _orig_array_result(self, dialect, coltype)


ARRAY.bind_processor = _array_bind  # type: ignore[method-assign]
ARRAY.result_processor = _array_result  # type: ignore[method-assign]

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
TEST_JWT_SECRET = "test-supabase-jwt-secret-32characters!!"


def _make_token(supabase_user_id: uuid.UUID) -> str:
    return pyjwt.encode(
        {"sub": str(supabase_user_id), "exp": int(time.time()) + 3600},
        TEST_JWT_SECRET,
        algorithm="HS256",
    )


class _FakeSupabaseAuth:
    """In-memory Supabase auth that validates credentials like the real API."""

    def __init__(self) -> None:
        self._users: dict[str, tuple[str, uuid.UUID]] = {}

    async def sign_up(self, credentials: dict) -> MagicMock:
        email = credentials["email"]
        password = credentials["password"]
        uid = uuid.uuid4()
        self._users[email] = (password, uid)
        return self._build_response(uid)

    async def sign_in_with_password(self, credentials: dict) -> MagicMock:
        email = credentials["email"]
        password = credentials["password"]
        entry = self._users.get(email)
        if entry is None or entry[0] != password:
            raise AuthApiError("Invalid login credentials", 400, "invalid_grant")
        return self._build_response(entry[1])

    def _build_response(self, uid: uuid.UUID) -> MagicMock:
        mock_user = MagicMock()
        mock_user.id = uid
        mock_session = MagicMock()
        mock_session.access_token = _make_token(uid)
        mock_session.refresh_token = "fake-refresh-token"
        resp = MagicMock()
        resp.user = mock_user
        resp.session = mock_session
        return resp


def _make_test_settings() -> Settings:
    return Settings.model_construct(
        ENV="test",
        DATABASE_URL=TEST_DATABASE_URL,
        SECRET_KEY="",
        ALGORITHM="HS256",
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        REFRESH_TOKEN_EXPIRE_DAYS=7,
        ALLOWED_ORIGINS=["http://localhost:4200"],
        REDIS_URL="redis://localhost:6379",
        SENTRY_DSN="",
        LOG_LEVEL="INFO",
        SUPABASE_URL="https://test.supabase.co",
        SUPABASE_ANON_KEY="test-anon-key",
        SUPABASE_JWT_SECRET=TEST_JWT_SECRET,
    )


@pytest.fixture(scope="session")
def event_loop_policy():
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(test_engine):
    yield
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest_asyncio.fixture
async def override_get_db(test_engine):
    TestSessionLocal = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    async def _override_get_db():
        async with TestSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    fake_auth = _FakeSupabaseAuth()
    fake_client = MagicMock()
    fake_client.auth = fake_auth

    test_settings = _make_test_settings()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_db_dep] = _override_get_db
    app.dependency_overrides[get_settings_dep] = lambda: test_settings

    with patch("app.routers.auth.get_supabase_client", new=AsyncMock(return_value=fake_client)):
        yield

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def async_client(override_get_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True) as ac:
        yield ac


@pytest_asyncio.fixture
async def register_user(async_client):
    async def _register(email="test@example.com", password="password123", displayName="Test User", role="user"):
        return await async_client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "displayName": displayName, "role": role},
        )

    return _register


@pytest_asyncio.fixture
async def auth_headers(async_client):
    async def _get_headers(
        email="test@example.com",
        password="password123",
        displayName="Test User",
        role="user",
    ):
        await async_client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "displayName": displayName, "role": role},
        )
        resp = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
        token = resp.json()["accessToken"]
        return {"Authorization": f"Bearer {token}"}

    return _get_headers
