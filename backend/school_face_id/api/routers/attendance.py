from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..dependencies import ensure_class_access, require_admin, require_any_user, require_staff
from ...config.security import require_session
from ...repositories import students as student_repository
from ...services import attendance as attendance_service


router = APIRouter(prefix="/attendance", tags=["attendance"])

# Named seams retained for compatibility with existing router-level tests.
get_recent_attendance = attendance_service.recent_events
get_class_daily_report = attendance_service.class_daily_report
get_parent_daily_report_by_id = attendance_service.parent_daily_report


@router.get("/events")
def events(
    request: Request, student_id: str | None = None, class_name: str | None = None,
    date: str | None = None, event_type: str | None = None, limit: int = 100,
):
    require_admin(request)
    if event_type and event_type not in attendance_service.EVENT_TYPES:
        raise HTTPException(status_code=400, detail="event_type must be enter or exit")
    return attendance_service.recent_events(
        min(max(limit, 1), 1000), student_id=student_id, class_name=class_name,
        event_date=date, event_type=event_type,
    )


@router.get("/events/{event_id}/photo")
def event_photo(event_id: str, request: Request):
    session = require_any_user(request)
    try:
        path, event = attendance_service.event_photo_path(event_id)
    except attendance_service.AttendanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not attendance_service.can_access_event(session, event):
        raise HTTPException(status_code=403, detail="Access denied")
    return FileResponse(path)


@router.get("/students/{student_id}/events")
def student_events(student_id: str, request: Request, limit: int = 500):
    session = require_session(request, "admin", "teacher")
    student = student_repository.get_student_by_id(student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    ensure_class_access(session, student[3])
    return attendance_service.student_events(student_id, min(max(limit, 1), 1000))


@router.get("/parent/events")
def parent_events(request: Request, date: str | None = None, limit: int = 500):
    session = require_session(request, "parent")
    return attendance_service.parent_events(
        session["parent_id"], min(max(limit, 1), 1000), date
    )


@router.get("/classes")
def classes(request: Request):
    session = require_staff(request)
    allowed = set(session.get("classes", [])) if session["role"] == "teacher" else None
    return [
        {"class_name": name, "students": students, "arrived": arrived, "left": left}
        for name, students, arrived, left in attendance_service.classes_summary()
        if allowed is None or name in allowed
    ]


@router.get("/classes/{class_name}")
def class_report(class_name: str, request: Request):
    session = require_staff(request)
    ensure_class_access(session, class_name)
    rows = get_class_daily_report(class_name)
    if session["role"] != "admin":
        rows = [{**row, "parent_name": ""} for row in rows]
    return rows


@router.get("/recent")
def recent(request: Request, limit: int = 50):
    require_admin(request)
    return get_recent_attendance(min(max(limit, 1), 500))


@router.delete("")
def clear(request: Request, class_name: str = ""):
    require_admin(request)
    deleted = attendance_service.clear_events(class_name.strip() or None)
    return {"status": "cleared", "deleted": deleted}


@router.get("/parent")
def parent_report(request: Request):
    session = require_session(request, "parent")
    return get_parent_daily_report_by_id(session["parent_id"])
