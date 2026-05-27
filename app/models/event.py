import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, PrimaryKeyConstraint, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import AppBase


class Event(AppBase):
    __tablename__ = "events"

    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clubs.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("scheduled", "active", "held", "cancelled", "rescheduled", name="event_status_enum"),
        default="scheduled",
        nullable=False,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    book_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    theme: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), server_default=text("'{}'"))
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    after_meeting_venue: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    has_winner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
    winner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    winner_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    google_book_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    club = relationship("Club", back_populates="events")
    attendees = relationship("EventAttendee", back_populates="event")


class EventAttendee(Base):
    __tablename__ = "event_attendees"
    __table_args__ = (PrimaryKeyConstraint("event_id", "user_id"),)

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    event = relationship("Event", back_populates="attendees")
