import uuid
from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class TimestampMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppBase(Base, TimestampMixin):
    """Base class for all application models.

    Provides:
      - ``id``         — UUID primary key (auto-generated)
      - ``created_at`` — timezone-aware creation timestamp (server default)
    """

    __abstract__ = True
