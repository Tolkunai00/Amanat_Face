from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...config.security import get_session
from ...config.settings import ALLOWED_ORIGINS
from ...realtime.manager import notification_connections
from ...services.notifications import recipient_for_session


router = APIRouter(tags=["realtime"])


def _origin_allowed(websocket: WebSocket):
    origin = websocket.headers.get("origin", "")
    return bool(origin) and origin in ALLOWED_ORIGINS


@router.websocket("/ws/notifications")
async def notification_websocket(websocket: WebSocket):
    if not _origin_allowed(websocket):
        await websocket.close(code=4403)
        return
    session = get_session(websocket)
    if session is None or session.get("role") not in {"parent", "teacher", "admin"}:
        await websocket.close(code=4401)
        return
    recipient_type, recipient_id = recipient_for_session(session)
    if session["role"] == "admin":
        recipient_type, recipient_id = "admin", session.get("login") or "admin"
    if not recipient_id:
        await websocket.close(code=4403)
        return

    await notification_connections.connect(recipient_type, recipient_id, websocket)
    try:
        await websocket.send_json({"type": "connected"})
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await notification_connections.disconnect(recipient_type, recipient_id, websocket)
