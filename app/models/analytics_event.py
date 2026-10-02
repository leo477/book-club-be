from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import AppBase


class AnalyticsEvent(AppBase):
    __tablename__ = "analytics_events"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    app: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    bucket: Mapped[str | None] = mapped_column(String(8))
    kind: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str | None] = mapped_column(String(120))
