import base64
import hashlib
import json
import os
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
from app.dependencies import get_db_dep, get_redis, get_settings_dep
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

POSTGRES_TEST_URL = os.environ.get("TEST_POSTGRES_URL")


def pytest_collection_modifyitems(config, items):
    """Skip `postgres`-marked tests unless TEST_POSTGRES_URL points at a real database."""
    if POSTGRES_TEST_URL:
        return
    skip = pytest.mark.skip(reason="TEST_POSTGRES_URL not set")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
TEST_JWT_SECRET = "test-supabase-jwt-secret-32characters!!"


def _make_token(supabase_user_id: uuid.UUID) -> str:
    return pyjwt.encode(
        {"sub": str(supabase_user_id), "exp": int(time.time()) + 3600},
        TEST_JWT_SECRET,
        algorithm="HS256",
    )


def make_flow_redis() -> AsyncMock:
    """AsyncMock Redis backed by a dict, honouring set/get/getdel (enough for the OAuth flow + handoff keys)."""
    store: dict[str, str | bytes] = {}

    async def _set(key, value, ex=None, nx=False):
        store[key] = value
        return True

    async def _get(key):
        return store.get(key)

    async def _getdel(key):
        return store.pop(key, None)

    mock = AsyncMock()
    mock.store = store
    mock.set = AsyncMock(side_effect=_set)
    mock.get = AsyncMock(side_effect=_get)
    mock.getdel = AsyncMock(side_effect=_getdel)
    return mock


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class _FakeSupabaseAuth:
    """In-memory Supabase auth that validates credentials like the real API."""

    def __init__(self) -> None:
        self._users: dict[str, tuple[str, uuid.UUID]] = {}
        self.exchange_calls: list[dict] = []
        self._code_challenges: dict[str, str] = {}
        self.admin = MagicMock()
        self.admin.sign_out = AsyncMock()

    async def sign_up(self, credentials: dict) -> MagicMock:
        email = credentials["email"]
        password = credentials["password"]
        uid = uuid.uuid4()
        self._users[email] = (password, uid)
        return self._build_response(uid, email=email)

    async def sign_in_with_password(self, credentials: dict) -> MagicMock:
        email = credentials["email"]
        password = credentials["password"]
        entry = self._users.get(email)
        if entry is None or entry[0] != password:
            raise AuthApiError("Invalid login credentials", 400, "invalid_grant")
        return self._build_response(entry[1], email=email)

    async def refresh_session(self, refresh_token: str) -> MagicMock:
        if refresh_token != "fake-refresh-token":
            raise AuthApiError("Invalid refresh token", 400, "invalid_token")
        uid = next(iter(self._users.values()))[1] if self._users else uuid.uuid4()
        return self._build_response(uid)

    def issue_code(self, challenge: str) -> str:
        """Model Google/Supabase issuing an auth code bound to the PKCE challenge of the authorize request."""
        code = f"code-{len(self._code_challenges)}"
        self._code_challenges[code] = challenge
        return code

    async def exchange_code_for_session(self, params: dict) -> MagicMock:
        self.exchange_calls.append(dict(params))
        code, verifier = params.get("auth_code"), params.get("code_verifier")
        if code in self._code_challenges:
            valid = bool(verifier) and _s256(verifier) == self._code_challenges.pop(code)
        else:
            # "good-code" is the legacy unbound code: valid for any non-empty verifier.
            valid = code == "good-code" and bool(verifier)
        if not valid:
            raise AuthApiError("Invalid authorization code", 400, "invalid_grant")
        email = "oauth@example.com"
        uid = uuid.uuid4()
        self._users[email] = ("", uid)
        return self._build_response(uid, email=email, metadata={"full_name": "OAuth User"})

    def _build_response(
        self, uid: uuid.UUID, email: str = "test@example.com", metadata: dict | None = None
    ) -> MagicMock:
        mock_user = MagicMock()
        mock_user.id = uid
        mock_user.email = email
        mock_user.user_metadata = metadata or {}
        mock_session = MagicMock()
        mock_session.access_token = _make_token(uid)
        mock_session.refresh_token = "fake-refresh-token"
        resp = MagicMock()
        resp.user = mock_user
        resp.session = mock_session
        return resp


def _fake_decode_access_token(token: str, settings: Settings) -> dict:
    from fastapi import HTTPException, status

    try:
        return pyjwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"], options={"verify_aud": False})
    except pyjwt.exceptions.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid or expired token", "code": "INVALID_TOKEN"},
        ) from exc


def _make_test_settings() -> Settings:
    return Settings.model_construct(
        ENV="test",
        DATABASE_URL=TEST_DATABASE_URL,
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        REFRESH_TOKEN_EXPIRE_DAYS=7,
        ALLOWED_ORIGINS=["http://localhost:4200"],
        REDIS_URL="redis://localhost:6379",
        SENTRY_DSN="",
        LOG_LEVEL="INFO",
        SUPABASE_URL="https://test.supabase.co",
        SUPABASE_ANON_KEY="test-anon-key",
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
    from app.limiter import limiter

    limiter._limiter.storage.reset()


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

    # Provide a dict-backed fake Redis (set/get/getdel work; the OAuth flow binding needs a real round trip)
    mock_redis = make_flow_redis()
    mock_redis.aclose = AsyncMock()

    async def _override_get_redis():
        return mock_redis

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_db_dep] = _override_get_db
    app.dependency_overrides[get_settings_dep] = lambda: test_settings
    app.dependency_overrides[get_redis] = _override_get_redis

    with (
        patch("app.routers.auth.get_supabase_client", new=AsyncMock(return_value=fake_client)),
        patch("app.services.auth_service.get_supabase_client", new=AsyncMock(return_value=fake_client)),
        patch("app.services.auth_service.decode_access_token", side_effect=_fake_decode_access_token),
        # chat.py and dependencies.py bind decode_access_token at import time; patch the local
        # names so the WS handshake and REST auth use the same fake decoder.
        patch("app.dependencies.decode_access_token", side_effect=_fake_decode_access_token),
    ):
        yield fake_auth

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def async_client(override_get_db):
    # Origin mirrors a real same-origin frontend request; the CSRF middleware requires
    # this on cookie-authenticated state-changing requests.
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=True,
        headers={"Origin": "http://localhost:4200"},
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session(test_engine):
    TestSessionLocal = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def register_user(async_client):
    async def _register(email="test@example.com", password="password123", displayName="Test User", role="user"):
        return await async_client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "displayName": displayName, "role": role},
        )

    return _register


@pytest_asyncio.fixture
async def make_member(async_client, test_engine):
    """Insert a ClubMember row directly so a user is a real member.

    The approval workflow means POST /join only creates a pending request; tests
    that need an actual member (chat access, stats, leave, listing) use this to
    bypass the organizer-approval step deterministically.
    """
    import uuid as _uuid

    from app.models.club_member import ClubMember

    TestSessionLocal = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    async def _make_member(club_id, headers, role="member"):
        me = await async_client.get("/api/v1/users/me", headers=headers)
        user_id = _uuid.UUID(me.json()["id"])
        async with TestSessionLocal() as session:
            session.add(ClubMember(id=_uuid.uuid4(), club_id=_uuid.UUID(str(club_id)), user_id=user_id, role=role))
            await session.commit()
        return str(user_id)

    return _make_member


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


class _Store(dict):
    """Key/value store with a parallel ``ttls`` map (ms remaining; absent = no TTL)."""

    def __init__(self) -> None:
        super().__init__()
        self.ttls: dict[str, int] = {}


class _InMemoryRedis:
    """Tiny in-memory Redis covering what the chat WS handler uses (tickets + flood limiter)."""

    def __init__(self, store: _Store) -> None:
        self.store = store

    async def getdel(self, key: str):
        self.store.ttls.pop(key, None)
        return self.store.pop(key, None)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.store.ttls[key] = int(ex) * 1000
        return True

    async def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def pttl(self, key):
        if key not in self.store:
            return -2
        return self.store.ttls.get(key, -1)

    async def expire(self, key, seconds):
        if key not in self.store:
            return 0
        self.store.ttls[key] = int(seconds) * 1000
        return 1

    async def eval(self, script, numkeys, *keys_and_args):
        """Emulates the flood limiter's INCR/PTTL/EXPIRE Lua script atomically."""
        key, ttl = keys_and_args[0], keys_and_args[numkeys]
        count = await self.incr(key)
        if count == 1 or await self.pttl(key) == -1:
            await self.expire(key, ttl)
        return count


@pytest.fixture(autouse=True)
def _fake_ws_ticket_redis():
    """Back the chat WS handshake and flood limiter with an in-memory Redis; yields its key store."""
    store = _Store()
    app.state.redis_pool = None
    with patch("app.routers.chat.aioredis.Redis", return_value=_InMemoryRedis(store)):
        yield store


@pytest.fixture
def ws_ticket(async_client, _fake_ws_ticket_redis):
    """Return an async callable minting a one-time ws ticket for the user behind the given auth headers."""

    async def _mint(headers: dict) -> str:
        me = await async_client.get("/api/v1/users/me", headers=headers)
        ticket = f"tk-{uuid.uuid4().hex}"
        _fake_ws_ticket_redis[f"ws:ticket:{ticket}"] = me.json()["id"]
        return ticket

    return _mint
