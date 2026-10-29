from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, Response

from ..dependencies import require_admin
from ...config.settings import CAMERA_INDEXES
from ...services import camera_settings

router = APIRouter(prefix="/camera", tags=["camera"])


@router.get("/config")
def camera_config(request: Request):
    require_admin(request)
    return [camera_settings.public_config(direction) for direction in list(CAMERA_INDEXES)
            if direction != "classroom" or CAMERA_INDEXES[direction] != ""]


def require_camera(direction):
    if direction not in CAMERA_INDEXES:
        raise HTTPException(status_code=404, detail="Камера не найдена")


@router.post("/classrooms", status_code=201)
def create_classroom(config: camera_settings.CameraConfig, request: Request):
    require_admin(request)
    return camera_settings.create_classroom(config)


@router.delete("/classrooms/{direction}")
def delete_classroom(direction: str, request: Request):
    require_admin(request)
    try:
        camera_settings.delete_classroom(direction)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Камера не найдена") from exc
    return {"status": "deleted"}


@router.put("/config/{direction}")
def update_camera_config(direction: str, config: camera_settings.CameraConfig, request: Request):
    require_admin(request)
    require_camera(direction)
    try:
        return camera_settings.save_config(direction, config)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Не удалось сохранить настройки камеры") from exc


@router.post("/config/{direction}/test")
def test_camera_config(direction: str, config: camera_settings.CameraConfig, request: Request):
    require_admin(request)
    if direction != "new":
        require_camera(direction)
    return camera_settings.test_connection(direction, config)

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
    require_camera(status)
    from ...recognition.camera_service import camera_frames
    normalized_status = status
    source = CAMERA_INDEXES[normalized_status]
    return StreamingResponse(
        camera_frames(normalized_status, source, []),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/snapshot")
def camera_snapshot(request: Request, status: str):
    require_admin(request)
    require_camera(status)
    from ...recognition.camera_service import get_camera_worker
    worker = get_camera_worker(status, CAMERA_INDEXES[status])
    with worker.condition:
        jpeg = worker.jpeg
    if jpeg is None:
        _, jpeg = worker.wait_for_frame(-1, timeout=1)
    if jpeg is None:
        raise HTTPException(status_code=503, detail="Камера подключается")
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
