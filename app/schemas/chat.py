from pydantic import BaseModel


class ChatRoomResponse(BaseModel):
    id: str
    name: str


class ChatMessageResponse(BaseModel):
    id: str
    senderId: str
    senderName: str
    text: str
    timestamp: str  # ISO


class SendMessageRequest(BaseModel):
    text: str


class CreateChatRoomRequest(BaseModel):
    name: str


class BanFromRoomRequest(BaseModel):
    user_id: str
    duration_seconds: int
