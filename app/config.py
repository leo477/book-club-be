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

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    @model_validator(mode="after")
    def validate_secret_key(self) -> "Settings":
        if self.ENV == "test":
            return self
        if (
            not self.SECRET_KEY
            or self.SECRET_KEY == "change-me-in-production"  # noqa: S105
            or len(self.SECRET_KEY) < 32
        ):
            raise ValueError(
                "SECRET_KEY must be set to a secure random string of at least 32 characters. "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
