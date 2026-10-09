import pytest

from app.config import Settings
from app.main import _redis_log_fields, lifespan


def _make_settings(**overrides: object) -> Settings:
    base = {
        "ENV": "production",
        "DATABASE_URL": "postgresql+asyncpg://user:password@localhost:5432/bookclub",
        "ACCESS_TOKEN_EXPIRE_MINUTES": 30,
        "REFRESH_TOKEN_EXPIRE_DAYS": 7,
        "ALLOWED_ORIGINS": ["https://book-club.example.com"],
        "REDIS_URL": "redis://localhost:6379",
        "SENTRY_DSN": "",
        "LOG_LEVEL": "INFO",
        "BACKEND_URL": "https://api.book-club.example.com",
        "FRONTEND_URL": "https://book-club.example.com",
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_ANON_KEY": "test-anon-key",
        "SUPABASE_JWT_SECRET": "test-jwt-secret",
    }
    base.update(overrides)
    return Settings.model_construct(**base)


@pytest.mark.asyncio
async def test_lifespan_raises_when_backend_url_is_localhost(monkeypatch):
    settings = _make_settings(BACKEND_URL="http://localhost:8000")
    monkeypatch.setattr("app.main.get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="BACKEND_URL must be a public URL"):
        async with lifespan(None):
            pass


@pytest.mark.asyncio
async def test_lifespan_raises_when_supabase_not_configured_in_production(monkeypatch):
    settings = _make_settings(SUPABASE_URL="", SUPABASE_ANON_KEY="", SUPABASE_JWT_SECRET="")
    monkeypatch.setattr("app.main.get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="Supabase must be configured in production"):
        async with lifespan(None):
            pass


def test_redis_log_fields_exclude_credentials():
    fields = _redis_log_fields("rediss://default:s3cret@redis.example.com:6380/0")
    assert fields == {"host": "redis.example.com", "port": 6380}
    assert "s3cret" not in str(fields)
