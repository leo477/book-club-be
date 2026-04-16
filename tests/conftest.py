import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.dependencies import get_db_dep
from app.main import app

# SQLite doesn't support PostgreSQL-specific types; map them to JSON for testing
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "JSON"  # type: ignore[method-assign]
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "JSON"  # type: ignore[method-assign]

# Patch ARRAY bind/result processors so SQLite serialises Python lists as JSON
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

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_db_dep] = _override_get_db
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
