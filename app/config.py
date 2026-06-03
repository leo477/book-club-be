import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/bookclub"
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:4200",
    ]
    CORS_ORIGIN_REGEX: str = r"^https://book-club-[a-z0-9-]+\.vercel\.app$"
    REDIS_URL: str = "redis://localhost:6379"
    PHOTON_URL: str = "https://photon.komoot.io"
    PHOTON_TIMEOUT: int = 8
    SENTRY_DSN: str = ""
    LOG_LEVEL: str = "INFO"
    ENV: str = "development"

    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""

    GOOGLE_BOOKS_API_KEY: str = ""
    MAPS_API_KEY: str = ""
    MAPS_MAP_ID: str = ""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    @property
    def supabase_configured(self) -> bool:
        return bool(self.SUPABASE_URL and self.SUPABASE_ANON_KEY and self.SUPABASE_JWT_SECRET)


@lru_cache
def get_settings() -> Settings:
    return Settings()
