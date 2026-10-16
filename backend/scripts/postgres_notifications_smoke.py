"""Notification smoke test guarded to run only in a disposable PostgreSQL DB."""

from urllib.parse import urlparse

from school_face_id.config.settings import DATABASE_URL
from school_face_id.data.database import delete_teacher, save_teacher
from school_face_id.repositories import notifications, students, teachers
from school_face_id.services import attendance
from school_face_id.services import notifications as notification_service
from school_face_id.services import parents as parent_service


database_name = urlparse(DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)).path.lstrip("/")
if not database_name.startswith("amanat_notification_smoke_"):
    raise RuntimeError("Refusing to run outside an amanat_notification_smoke_* database")

student = students.save_student("PostgreSQL Notification Smoke", "Smoke Notify", "", None)
parent = parent_service.create_parent("smoke-notify-parent", "Smoke Parent", None, "smoke-secret")
parent_service.link_parent_to_student(parent["id"], student["id"], "guardian")
assert save_teacher("smoke-notify-teacher", "smoke-secret", ["Smoke Notify"])
teacher = teachers.get_teacher_by_login("smoke-notify-teacher")

try:
    entered = attendance.register_event(
        student["id"], "enter", "2026-09-28T08:00:00+06:00", debounce_seconds=0
    )["event"]
    exited = attendance.register_event(
        student["id"], "exit", "2026-09-28T16:00:00+06:00", debounce_seconds=0
    )["event"]
    parent_rows = notifications.list_notifications_for_parent(parent["id"])
    teacher_rows = notifications.list_notifications_for_teacher(teacher["id"])
    assert len(parent_rows) == len(teacher_rows) == 2
    assert notification_service.create_for_attendance_event(entered["id"]) == []
    assert notification_service.create_for_attendance_event(exited["id"]) == []
    parent_session = {"role": "parent", "parent_id": parent["id"]}
    notification_service.mark_read_for_session(parent_rows[0]["id"], parent_session)
    assert notification_service.unread_count_for_session(parent_session) == 1
    print("PostgreSQL notification smoke test: OK")
finally:
    students.delete_student_by_id(student["id"])
    parent_service.delete_parent(parent["id"])
    delete_teacher("smoke-notify-teacher")
