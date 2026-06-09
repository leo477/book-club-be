import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AppBase


class ClubJoinRequest(AppBase):
    __tablename__ = "club_join_requests"
    __table_args__ = (UniqueConstraint("club_id", "user_id", name="uq_club_join_request"),)

    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clubs.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        Enum("pending", "approved", "rejected", name="club_join_request_status_enum"),
        default="pending",
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        Enum("manual", "event", name="club_join_request_source_enum"),
        default="manual",
        nullable=False,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
