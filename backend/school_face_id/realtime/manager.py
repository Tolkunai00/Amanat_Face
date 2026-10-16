"""Identity-aware registry of ephemeral WebSocket connections."""

import asyncio
import logging

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, dict[str, set[WebSocket]]] = {
            "parent": {}, "teacher": {}, "admin": {},
        }
        self._lock = asyncio.Lock()
        self._loop = None

    @property
    def loop(self):
        return self._loop

    async def connect(self, recipient_type: str, recipient_id: str, websocket: WebSocket):
        await websocket.accept()
        self._loop = asyncio.get_running_loop()
        async with self._lock:
            recipients = self._connections.setdefault(recipient_type, {})
            recipients.setdefault(recipient_id, set()).add(websocket)
        logging.info("Notification WebSocket connected for role %s", recipient_type)

    async def disconnect(self, recipient_type: str, recipient_id: str, websocket: WebSocket):
        async with self._lock:
            recipients = self._connections.get(recipient_type, {})
            sockets = recipients.get(recipient_id)
            if sockets is not None:
                sockets.discard(websocket)
                if not sockets:
                    recipients.pop(recipient_id, None)
        logging.info("Notification WebSocket disconnected for role %s", recipient_type)

    async def send_to_recipient(self, recipient_type: str, recipient_id: str, payload: dict):
        async with self._lock:
            sockets = list(
                self._connections.get(recipient_type, {}).get(recipient_id, set())
            )
        dead = []
        for websocket in sockets:
            try:
                await websocket.send_json(payload)
            except Exception:
                dead.append(websocket)
                logging.exception(
                    "Notification WebSocket send failed for role %s", recipient_type
                )
        for websocket in dead:
            await self.disconnect(recipient_type, recipient_id, websocket)
        return len(sockets) - len(dead)

    async def connection_count(self, recipient_type=None, recipient_id=None):
        async with self._lock:
            if recipient_type is None:
                return sum(
                    len(sockets)
                    for recipients in self._connections.values()
                    for sockets in recipients.values()
                )
            recipients = self._connections.get(recipient_type, {})
            if recipient_id is None:
                return sum(len(sockets) for sockets in recipients.values())
            return len(recipients.get(recipient_id, set()))


notification_connections = ConnectionManager()
