import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(Enum("user", "organizer", name="user_role_enum"), nullable=False, default="user")
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    socials_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    telegram: Mapped[str | None] = mapped_column(String(100), nullable=True)
    instagram: Mapped[str | None] = mapped_column(String(100), nullable=True)
    twitter: Mapped[str | None] = mapped_column(String(100), nullable=True)
    linkedin: Mapped[str | None] = mapped_column(String(100), nullable=True)
    github: Mapped[str | None] = mapped_column(String(100), nullable=True)
    goodreads: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
