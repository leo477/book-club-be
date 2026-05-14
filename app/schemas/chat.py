from datetime import datetime

from pydantic import BaseModel


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
    name: str


class BanFromRoomRequest(BaseModel):
    user_id: str
    duration_seconds: int
