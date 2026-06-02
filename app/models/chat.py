import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import AppBase

_USERS_FK = "users.id"
_CHAT_ROOMS_FK = "chat_rooms.id"
_COL_ROOM_ID = "room_id"
_COL_USER_ID = "user_id"


class ChatRoom(AppBase):
    __tablename__ = "chat_rooms"
    __table_args__ = (UniqueConstraint("club_id", "name", name="uq_chat_rooms_club_name"),)

    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clubs.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True
    )


class ChatMessage(AppBase):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_room_timestamp", _COL_ROOM_ID, "timestamp"),)

    room_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(_CHAT_ROOMS_FK), nullable=False)
    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(_USERS_FK), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatRoomBan(AppBase):
    __tablename__ = "chat_room_bans"
    __table_args__ = (Index("ix_chat_room_bans_room_user", _COL_ROOM_ID, _COL_USER_ID),)

    room_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(_CHAT_ROOMS_FK), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(_USERS_FK), nullable=False)
    banned_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(_USERS_FK), nullable=False)
    banned_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessageRead(AppBase):
    """Feature 5: tracks the last message each user has read in a chat room."""

    __tablename__ = "message_reads"
    __table_args__ = (
        UniqueConstraint(_COL_USER_ID, _COL_ROOM_ID, name="uq_message_reads_user_room"),
        Index("ix_message_reads_user_room", _COL_USER_ID, _COL_ROOM_ID),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(_USERS_FK, ondelete="CASCADE"), nullable=False
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(_CHAT_ROOMS_FK, ondelete="CASCADE"), nullable=False
    )
    last_read_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
