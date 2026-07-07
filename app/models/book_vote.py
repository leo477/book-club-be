import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AppBase

_ROUNDS_FK = "book_vote_rounds.id"


class BookVoteRound(AppBase):
    __tablename__ = "book_vote_rounds"

    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clubs.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        Enum("open", "closed", name="book_vote_round_status_enum"),
        default="open",
        nullable=False,
    )
    winner_option_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BookVoteOption(AppBase):
    __tablename__ = "book_vote_options"

    round_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(_ROUNDS_FK), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    author: Mapped[str | None] = mapped_column(String(300), nullable=True)


class BookVoteVote(AppBase):
    __tablename__ = "book_vote_votes"
    __table_args__ = (UniqueConstraint("round_id", "user_id", name="uq_book_vote_round_user"),)

    round_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(_ROUNDS_FK), nullable=False, index=True)
    option_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("book_vote_options.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
