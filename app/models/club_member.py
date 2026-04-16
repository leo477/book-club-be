import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class ClubMember(Base):
    __tablename__ = "club_members"
    __table_args__ = (UniqueConstraint("club_id", "user_id", name="uq_club_member"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clubs.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(
        Enum("member", "organizer", name="club_member_role_enum"),
        default="member",
        nullable=False,
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    club = relationship("Club", back_populates="members")
    user = relationship("User")
