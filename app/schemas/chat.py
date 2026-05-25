from datetime import datetime

from pydantic import BaseModel, Field


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


class SendMessageRequest(BaseModel):
    text: str


class CreateChatRoomRequest(BaseModel):
    name: str = Field(min_length=3, max_length=40)


class BanFromRoomRequest(BaseModel):
    user_id: str
    duration_seconds: int
