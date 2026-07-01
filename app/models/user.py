import uuid

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AppBase


class User(AppBase):
    __tablename__ = "users"

    supabase_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(
        Enum("user", "organizer", "admin", name="user_role_enum"), nullable=False, default="user"
    )
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    socials_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    telegram: Mapped[str | None] = mapped_column(String(100), nullable=True)
    instagram: Mapped[str | None] = mapped_column(String(100), nullable=True)
    twitter: Mapped[str | None] = mapped_column(String(100), nullable=True)
    linkedin: Mapped[str | None] = mapped_column(String(100), nullable=True)
    github: Mapped[str | None] = mapped_column(String(100), nullable=True)
    goodreads: Mapped[str | None] = mapped_column(String(100), nullable=True)
