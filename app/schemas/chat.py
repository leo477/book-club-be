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
