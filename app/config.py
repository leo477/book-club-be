from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/bookclub"
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALLOWED_ORIGINS: list[str] = ["http://localhost:4200"]
    REDIS_URL: str = "redis://localhost:6379"
    SENTRY_DSN: str = ""
    LOG_LEVEL: str = "INFO"
    ENV: str = "development"

    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        if self.ENV == "production":
            if not self.SUPABASE_URL or not self.SUPABASE_ANON_KEY or not self.SUPABASE_JWT_SECRET:
                raise ValueError(
                    "SUPABASE_URL, SUPABASE_ANON_KEY, and SUPABASE_JWT_SECRET must be set. "
                    "Find these in your Supabase project Settings > API."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
