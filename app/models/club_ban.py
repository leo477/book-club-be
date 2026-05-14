import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import AppBase


class ClubBan(AppBase):
    __tablename__ = "club_bans"
    # MN-13 & MN-15: prevent duplicate ban rows for the same (club, user) pair
    __table_args__ = (UniqueConstraint("club_id", "user_id", name="uq_club_bans_club_user"),)

    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clubs.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    banned_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    banned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    duration: Mapped[str] = mapped_column(String(20), nullable=False)
    # M-7: nullable — NULL means permanent ban
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
