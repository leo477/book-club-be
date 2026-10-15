import uuid
from collections import defaultdict
from typing import Any, TypedDict

import structlog
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatRoom
from app.repositories import ChatRepository

logger = structlog.get_logger(__name__)

WS_POLICY_VIOLATION = 1008


class MessagePayload(TypedDict):
    id: str
    senderId: str
    senderName: str
    text: str
    timestamp: str
    isSystem: bool


class BroadcastMessage(TypedDict):
    type: str
    payload: MessagePayload


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[str, list[WebSocket]] = defaultdict(list)
        # room_id → set of user_id strings currently connected (Feature 4: presence)
        self.room_presence: dict[str, set[str]] = defaultdict(set)

    def connect(self, room_id: str, websocket: WebSocket, user_id: str) -> None:
        websocket._user_id = user_id  # type: ignore[attr-defined]
        self.active_connections[room_id].append(websocket)
        self.room_presence[room_id].add(user_id)

    def disconnect(self, room_id: str, websocket: WebSocket) -> None:
        if websocket in self.active_connections[room_id]:
            self.active_connections[room_id].remove(websocket)
        # Only remove from presence if this user has no remaining connections in the room.
        uid: str | None = getattr(websocket, "_user_id", None)
        if uid:
            still_connected = any(getattr(ws, "_user_id", None) == uid for ws in self.active_connections[room_id])
            if not still_connected:
                self.room_presence[room_id].discard(uid)
        # Drop empty room keys so they don't accumulate after everyone leaves.
        if not self.active_connections[room_id]:
            del self.active_connections[room_id]
        if not self.room_presence[room_id]:
            del self.room_presence[room_id]

    def get_presence_snapshot(self, room_id: str) -> list[dict[str, str]]:
        """Return current online users for the room as a list of presence payloads."""
        return [{"userId": uid, "status": "online"} for uid in self.room_presence.get(room_id, set())]

    async def broadcast(self, room_id: str, message: dict[str, Any]) -> None:
        for connection in self.active_connections.get(room_id, []).copy():
            try:
                await connection.send_json(message)
            except WebSocketDisconnect:
                self.disconnect(room_id, connection)
                logger.debug("WebSocket disconnected during broadcast")
            except RuntimeError:
                self.disconnect(room_id, connection)
                logger.warning("Runtime error while broadcasting to a room")

    async def _close_all(self, room_id: str, user_id: str | None) -> None:
        for ws in self.active_connections.get(room_id, []).copy():
            if user_id is not None and getattr(ws, "_user_id", None) != user_id:
                continue
            self.disconnect(room_id, ws)
            try:
                await ws.close(code=WS_POLICY_VIOLATION)
            except Exception:  # intentional: a half-dead socket must not block kicking the others
                logger.debug("WebSocket close failed during kick", exc_info=True)

    async def kick(self, user_id: str, room_id: str) -> None:
        await self._close_all(room_id, user_id)

    async def close_room(self, room_id: str) -> None:
        await self._close_all(room_id, None)

    async def broadcast_presence(self, room_id: str, user_id: str, status: str) -> None:
        """Broadcast an online/offline event to everyone in the room."""
        await self.broadcast(room_id, {"type": "presence", "payload": {"userId": user_id, "status": status}})


manager = ConnectionManager()


async def kick_user_from_club_rooms(club_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> None:
    """Best-effort: the DB change is already committed, so socket failures must not fail the request."""
    try:
        for room in await ChatRepository(db).list_rooms(club_id):
            await manager.kick(str(user_id), str(room.id))
    except Exception:  # intentional: see docstring
        logger.warning("Failed to kick user sockets", club_id=str(club_id), user_id=str(user_id), exc_info=True)


async def close_rooms_best_effort(room_ids: list[uuid.UUID]) -> None:
    for room_id in room_ids:
        try:
            await manager.close_room(str(room_id))
        except Exception:  # intentional: deletion is already committed
            logger.warning("Failed to close room sockets", room_id=str(room_id), exc_info=True)


async def kick_user_from_event_room(event_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> None:
    """Best-effort: the un-attend is already committed, so socket failures must not fail the request."""
    try:
        room_id = await db.scalar(select(ChatRoom.id).where(ChatRoom.event_id == event_id))
        if room_id is not None:
            await manager.kick(str(user_id), str(room_id))
    except Exception:  # intentional: see docstring
        logger.warning("Failed to kick user from event room", event_id=str(event_id), exc_info=True)
