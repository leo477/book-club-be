import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

MAX_MESSAGE_LENGTH = 2000
MAX_BAN_SECONDS = 365 * 24 * 3600

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_INVISIBLE = frozenset("\u00ad\u115f\u1160\u180e\u200b\u200c\u200d\u200e\u200f\u2060\u2800\u3164\ufeff")


def normalize_message_text(text: object) -> str | None:
    """Single source of truth for chat text rules (REST and WS). Returns the stripped text, or None if invalid."""
    if not isinstance(text, str) or len(text) > MAX_MESSAGE_LENGTH or _CONTROL_CHARS.search(text):
        return None
    stripped = text.strip()
    if not any(not ch.isspace() and ch not in _INVISIBLE for ch in stripped):
        return None
    return stripped


class ChatRoomResponse(BaseModel):
    id: str
    name: str
    eventId: str | None = None


class ChatMessageResponse(BaseModel):
    id: str
    senderId: str
    senderName: str
    text: str
    timestamp: datetime | str
    isSystem: bool = False


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=MAX_MESSAGE_LENGTH)

    @field_validator("text")
    @classmethod
    def _valid_text(cls, v: str) -> str:
        normalized = normalize_message_text(v)
        if normalized is None:
            raise PydanticCustomError("invalid_text", "Invalid message text")
        return normalized


class CreateChatRoomRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=40)


class BanFromRoomRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    duration_seconds: int = Field(ge=0, le=MAX_BAN_SECONDS)


# ── Feature 5: read/unread tracking ──────────────────────────────────────────


class MarkReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_read_message_id: str


class UnreadCountResponse(BaseModel):
    room_id: str
    unread_count: int
    last_read_message_id: str | None = None
