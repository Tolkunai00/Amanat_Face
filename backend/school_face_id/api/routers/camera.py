from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..dependencies import require_admin
from ...config.settings import CAMERA_INDEXES

router = APIRouter(prefix="/camera", tags=["camera"])

STATE_LABELS = {
    "working": "работает",
    "disconnected": "нет подключения",
    "error": "ошибка",
}


@router.get("/status")
def camera_status(request: Request):
    require_admin(request)
    from ...recognition.camera_service import camera_statuses
    return [
        {**item, "label": STATE_LABELS.get(item["state"], item["state"])}
        for item in camera_statuses(CAMERA_INDEXES)
    ]


@router.get("/feed")
def camera_feed(request: Request, status: str = "keldi"):
    require_admin(request)
    from ...recognition.camera_service import camera_frames
    normalized_status = "ketti" if status == "ketti" else "keldi"
    source = CAMERA_INDEXES[normalized_status]
    return StreamingResponse(
        camera_frames(normalized_status, source, []),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
