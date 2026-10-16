from fastapi import APIRouter, HTTPException, Request

from ..dependencies import require_any_user
from ...services import notifications as notification_service


router = APIRouter(prefix="/notifications", tags=["notifications"])


def _service_error(exc):
    if isinstance(exc, notification_service.NotificationAccessError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("")
def list_notifications(request: Request, limit: int = 50, unread_only: bool = False):
    session = require_any_user(request)
    try:
        return notification_service.list_for_session(
            session, unread_only, min(max(limit, 1), 200)
        )
    except notification_service.NotificationAccessError as exc:
        _service_error(exc)


@router.get("/unread-count")
def unread_count(request: Request):
    session = require_any_user(request)
    try:
        return {"count": notification_service.unread_count_for_session(session)}
    except notification_service.NotificationAccessError as exc:
        _service_error(exc)


@router.post("/read-all")
def mark_all_read(request: Request):
    session = require_any_user(request)
    try:
        count = notification_service.mark_all_read_for_session(session)
        return {"status": "read", "count": count}
    except notification_service.NotificationAccessError as exc:
        _service_error(exc)


@router.post("/{notification_id}/read")
def mark_read(notification_id: str, request: Request):
    session = require_any_user(request)
    try:
        return notification_service.mark_read_for_session(notification_id, session)
    except (
        notification_service.NotificationAccessError,
        notification_service.NotificationNotFoundError,
    ) as exc:
        _service_error(exc)
