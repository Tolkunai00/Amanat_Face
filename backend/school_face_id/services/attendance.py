"""Attendance event business rules and API-safe projections."""

from datetime import datetime
import logging
import threading
from zoneinfo import ZoneInfo

from ..config.settings import (
    ATTENDANCE_DEBOUNCE_SECONDS, ATTENDANCE_TIMEZONE,
)
from ..repositories import attendance as attendance_repository
from ..repositories import parents as parent_repository
from ..repositories import students as student_repository
from ..observability import metrics as runtime_metrics


EVENT_TYPES = {"enter", "exit"}
CAMERA_STATUS_TO_EVENT = {"keldi": "enter", "ketti": "exit"}
EVENT_TO_LEGACY_STATUS = {value: key for key, value in CAMERA_STATUS_TO_EVENT.items()}
LOCAL_TIMEZONE = ZoneInfo(ATTENDANCE_TIMEZONE)
registration_lock = threading.Lock()


class AttendanceValidationError(ValueError):
    pass


class AttendanceNotFoundError(LookupError):
    pass


def local_now():
    return datetime.now(LOCAL_TIMEZONE)


def normalize_occurred_at(value=None):
    if value is None:
        parsed = local_now()
    elif isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise AttendanceValidationError("Invalid attendance timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TIMEZONE)
    else:
        parsed = parsed.astimezone(LOCAL_TIMEZONE)
    return parsed


def serialize_event(event):
    public = {key: value for key, value in event.items() if key != "photo_path"}
    public["event_id"] = event["id"]
    public["timestamp"] = event["occurred_at"]
    public["status"] = EVENT_TO_LEGACY_STATUS[event["event_type"]]
    public["photo_url"] = (
        f"/api/v1/attendance/events/{event['id']}/photo" if event.get("photo_path") else ""
    )
    return public


def register_event(
    student_id, event_type, occurred_at=None, source="manual", photo_path=None,
    confidence=None, screenshot_frame=None, debounce_seconds=None,
):
    if student_repository.get_student_by_id(student_id) is None:
        raise AttendanceNotFoundError("Student not found")
    event_type = (event_type or "").strip().lower()
    if event_type not in EVENT_TYPES:
        raise AttendanceValidationError("event_type must be enter or exit")
    occurred = normalize_occurred_at(occurred_at)
    debounce = ATTENDANCE_DEBOUNCE_SECONDS if debounce_seconds is None else max(0, debounce_seconds)
    with registration_lock:
        last = attendance_repository.get_last_event_for_student(student_id)
        if last and last["event_type"] == event_type:
            previous = normalize_occurred_at(last["occurred_at"])
            elapsed = (occurred - previous).total_seconds()
            if 0 <= elapsed < debounce:
                runtime_metrics.increment("attendance_duplicate_total")
                return {"status": "duplicate_suppressed", "event": None}

        try:
            event = attendance_repository.create_event(
                student_id, event_type, occurred.isoformat(timespec="seconds"),
                (source or "manual").strip() or "manual", None, confidence,
                local_now().isoformat(timespec="seconds"),
            )
        except Exception:
            runtime_metrics.increment("attendance_errors_total")
            raise
        runtime_metrics.increment("attendance_created_total")
    if screenshot_frame is not None or photo_path:
        try:
            from . import event_photos

            if screenshot_frame is not None:
                attached = event_photos.save_event_screenshot(
                    event["id"], screenshot_frame, event["occurred_at"]
                )
            else:
                attached = event_photos.attach_existing_event_photo(
                    event["id"], photo_path, event["occurred_at"]
                )
            if attached is not None:
                event = attached
        except Exception:
            logging.exception(
                "Screenshot creation failed for attendance event %s student %s",
                event["id"], student_id,
            )
    try:
        from .notifications import create_for_attendance_event

        create_for_attendance_event(event["id"])
    except Exception:
        logging.exception(
            "Notification generation failed for attendance event %s student %s",
            event["id"], student_id,
        )
    return {"status": "created", "event": event}


def register_camera_event(student_id, camera_status, **kwargs):
    try:
        event_type = CAMERA_STATUS_TO_EVENT[camera_status]
    except KeyError as exc:
        raise AttendanceValidationError("Unknown camera attendance status") from exc
    kwargs.setdefault("source", f"camera_{camera_status}")
    return register_event(student_id, event_type, **kwargs)


def get_event(event_id):
    event = attendance_repository.get_event_by_id(event_id)
    if event is None:
        raise AttendanceNotFoundError("Attendance event not found")
    return event


def recent_events(limit=50, **filters):
    return [
        serialize_event(event)
        for event in attendance_repository.get_recent_events(limit=limit, **filters)
    ]


def student_events(student_id, limit=500):
    if student_repository.get_student_by_id(student_id) is None:
        raise AttendanceNotFoundError("Student not found")
    return [serialize_event(event) for event in attendance_repository.get_student_events(student_id, limit)]


def parent_events(parent_id, limit=500, event_date=None):
    return [
        serialize_event(event)
        for event in attendance_repository.get_parent_children_events(parent_id, limit, event_date)
    ]


def _local_time(timestamp):
    if not timestamp:
        return None
    return normalize_occurred_at(timestamp).strftime("%H:%M:%S")


def class_daily_report(class_name):
    return [
        {
            "id": row[0], "student_id": row[0], "student_number": row[1],
            "name": row[2], "class_name": row[3], "date": row[4],
            "arrived_at": _local_time(row[5]), "left_at": _local_time(row[6]),
            "parent_name": row[7],
        }
        for row in attendance_repository.get_class_daily_report(
            class_name.strip(), local_now().date().isoformat()
        )
    ]


def parent_daily_report(parent_id):
    return [
        {
            "student_id": row[0], "student_number": row[1], "student_name": row[2],
            "class_name": row[3], "date": row[4],
            "arrived_at": _local_time(row[5]), "left_at": _local_time(row[6]),
        }
        for row in attendance_repository.get_parent_daily_report(
            parent_id, local_now().date().isoformat()
        )
    ]


def classes_summary():
    return attendance_repository.get_classes_summary(local_now().date().isoformat())


def clear_events(class_name=None):
    return attendance_repository.delete_events(class_name)


def event_photo_path(event_id):
    event = get_event(event_id)
    try:
        from .event_photos import readable_event_photo

        path = readable_event_photo(event)
    except Exception as exc:
        raise AttendanceNotFoundError("Event photo not found")
    return path, event


def can_access_event(session, event):
    if session["role"] == "admin":
        return True
    if session["role"] == "teacher":
        return event["class_name"] in session.get("classes", [])
    if session["role"] == "parent":
        return any(
            parent["id"] == session.get("parent_id")
            for parent in parent_repository.get_parents_for_student(event["student_id"])
        )
    return False


# Compatibility projections for callers that still use the former names.
def get_recent_attendance(limit=50):
    return recent_events(limit)


def get_class_daily_report(class_name):
    return class_daily_report(class_name)


def get_parent_daily_report_by_id(parent_id):
    return parent_daily_report(parent_id)


def get_classes_summary():
    return classes_summary()


def clear_attendance(class_name=None):
    return clear_events(class_name)


def log_attendance(student_id, name=None, class_name=None, status="keldi"):
    return register_camera_event(student_id, status)["status"] == "created"
