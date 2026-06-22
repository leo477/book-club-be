import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/bookclub"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:4200",
    ]
    CORS_ORIGIN_REGEX: str = r"^https://book-club-[a-z0-9-]+\.vercel\.app$"
    REDIS_URL: str = "redis://localhost:6379"
    # Public origins used to build OAuth redirect targets. FRONTEND_URL is where
    # the browser lands after login; BACKEND_URL is this API's public base URL.
    FRONTEND_URL: str = "http://localhost:4200"
    BACKEND_URL: str = "http://localhost:8000"
    PHOTON_URL: str = "https://photon.komoot.io"
    PHOTON_TIMEOUT: int = 8
    SENTRY_DSN: str = ""
    LOG_LEVEL: str = "INFO"
    ENV: str = "development"
    # Start in-process background loops (e.g. chat-room cleanup). On a multi-instance
    # deploy only ONE instance should set this true, to avoid duplicating periodic work.
    RUN_BACKGROUND_TASKS: bool = True

    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""

    GOOGLE_BOOKS_API_KEY: str = ""
    GOOGLE_CSE_API_KEY: str = ""
    GOOGLE_CSE_ID: str = ""
    MAPS_API_KEY: str = ""
    MAPS_SERVER_API_KEY: str = ""
    MAPS_MAP_ID: str = ""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    @property
    def supabase_configured(self) -> bool:
        return bool(self.SUPABASE_URL and self.SUPABASE_ANON_KEY and self.SUPABASE_JWT_SECRET)


@lru_cache
def get_settings() -> Settings:
    return Settings()
