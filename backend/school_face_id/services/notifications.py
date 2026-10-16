"""Recipient resolution and business rules for in-app notifications."""

from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from ..config.settings import ATTENDANCE_TIMEZONE
from ..repositories import attendance as attendance_repository
from ..repositories import notifications as notification_repository
from ..repositories import parents as parent_repository
from ..repositories import teachers as teacher_repository


LOCAL_TIMEZONE = ZoneInfo(ATTENDANCE_TIMEZONE)
NOTIFICATION_TYPES = {"enter": "student_entered", "exit": "student_exited"}


class NotificationNotFoundError(LookupError):
    pass


class NotificationAccessError(PermissionError):
    pass


def _now():
    return datetime.now(LOCAL_TIMEZONE).isoformat(timespec="seconds")


def _event_time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TIMEZONE)
    return parsed.astimezone(LOCAL_TIMEZONE).strftime("%H:%M")


def _message(event, recipient_type):
    action = "пришёл в школу" if event["event_type"] == "enter" else "вышел из школы"
    student = event["name"]
    if recipient_type == "teacher":
        student = f"{student} ({event['class_name']})"
    return f"{student} {action} в {_event_time(event['occurred_at'])}."


def create_for_attendance_event(event_id):
    event = attendance_repository.get_event_by_id(event_id)
    if event is None:
        raise NotificationNotFoundError("Attendance event not found")
    notification_type = NOTIFICATION_TYPES.get(event["event_type"])
    if notification_type is None:
        raise ValueError("Unsupported attendance event type")

    recipients = [
        ("parent", parent["id"])
        for parent in parent_repository.get_parents_for_student(event["student_id"])
    ]
    recipients.extend(
        ("teacher", teacher["id"])
        for teacher in teacher_repository.get_teachers_for_class(event["class_name"])
    )
    created = []
    created_at = _now()
    for recipient_type, recipient_id in recipients:
        notification = notification_repository.create_notification(
            recipient_type, recipient_id, event["student_id"], event["id"],
            notification_type, _message(event, recipient_type), created_at,
        )
        if notification is not None:
            created.append(notification)
            try:
                from ..realtime.publisher import publish_notification

                publish_notification(notification)
            except Exception:
                logging.exception(
                    "Realtime publish scheduling failed for notification %s",
                    notification["id"],
                )
    return created


def ensure_notifications_for_event(event_id):
    return create_for_attendance_event(event_id)


def _teacher_id(session):
    teacher_id = session.get("teacher_id")
    if teacher_id:
        return teacher_id
    teacher = teacher_repository.get_teacher_by_login(session.get("login", ""))
    return teacher["id"] if teacher else None


def recipient_for_session(session):
    if session["role"] == "parent":
        return "parent", session.get("parent_id")
    if session["role"] == "teacher":
        return "teacher", _teacher_id(session)
    if session["role"] == "admin":
        return None, None
    raise NotificationAccessError("Notifications are not available")


def list_for_session(session, unread_only=False, limit=50):
    recipient_type, recipient_id = recipient_for_session(session)
    if session["role"] == "admin":
        return notification_repository.list_all_notifications(unread_only, limit)
    if not recipient_id:
        raise NotificationAccessError("Notification recipient identity is missing")
    if recipient_type == "parent":
        return notification_repository.list_notifications_for_parent(
            recipient_id, unread_only, limit
        )
    return notification_repository.list_notifications_for_teacher(
        recipient_id, unread_only, limit
    )


def mark_read_for_session(notification_id, session):
    recipient_type, recipient_id = recipient_for_session(session)
    if not recipient_type or not recipient_id:
        raise NotificationAccessError("Admin has no personal notification recipient")
    if not notification_repository.mark_notification_read(
        notification_id, recipient_type, recipient_id, _now()
    ):
        raise NotificationNotFoundError("Notification not found")
    return notification_repository.get_notification_by_id(notification_id)


def mark_all_read_for_session(session):
    recipient_type, recipient_id = recipient_for_session(session)
    if not recipient_type or not recipient_id:
        raise NotificationAccessError("Admin has no personal notification recipient")
    return notification_repository.mark_all_read(recipient_type, recipient_id, _now())


def unread_count_for_session(session):
    recipient_type, recipient_id = recipient_for_session(session)
    if session["role"] == "admin":
        return notification_repository.count_unread()
    if not recipient_id:
        raise NotificationAccessError("Notification recipient identity is missing")
    return notification_repository.count_unread(recipient_type, recipient_id)


def backfill_date_range(date_from, date_to=None, limit=100000):
    event_ids = attendance_repository.list_event_ids_for_range(date_from, date_to, limit)
    created = 0
    for event_id in event_ids:
        created += len(ensure_notifications_for_event(event_id))
    return {"events_checked": len(event_ids), "notifications_created": created}
