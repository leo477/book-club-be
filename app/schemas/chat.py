from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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

    text: str


class CreateChatRoomRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=40)


class BanFromRoomRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    duration_seconds: int


# ── Feature 5: read/unread tracking ──────────────────────────────────────────


class MarkReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_read_message_id: str


class UnreadCountResponse(BaseModel):
    room_id: str
    unread_count: int
    last_read_message_id: str | None = None
