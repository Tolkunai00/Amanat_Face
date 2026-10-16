"""Attendance smoke test guarded to run only in a disposable PostgreSQL DB."""

from datetime import datetime, timedelta
from urllib.parse import urlparse

from school_face_id.config.settings import DATABASE_URL
from school_face_id.repositories import attendance as attendance_repository
from school_face_id.repositories import students
from school_face_id.services import attendance as attendance_service


database_name = urlparse(DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)).path.lstrip("/")
if not database_name.startswith("amanat_attendance_smoke_"):
    raise RuntimeError("Refusing to run outside an amanat_attendance_smoke_* database")

student = students.save_student("PostgreSQL Attendance Smoke", "Smoke Class", "", None)
start = datetime.fromisoformat("2026-09-28T08:00:00+06:00")
try:
    for offset, event_type in ((0, "enter"), (4, "exit"), (5, "enter"), (8, "exit")):
        result = attendance_service.register_event(
            student["id"], event_type, start + timedelta(hours=offset)
        )
        assert result["status"] == "created"
    events = attendance_repository.get_student_events_for_day(student["id"], "2026-09-28")
    assert len(events) == 4
    summary = next(
        row for row in attendance_service.class_daily_report("Smoke Class")
        if row["date"] == "2026-09-28"
    )
    assert summary["arrived_at"] == "08:00:00"
    assert summary["left_at"] == "16:00:00"
    assert len(attendance_service.recent_events(10, student_id=student["id"])) == 4
    print("PostgreSQL attendance event smoke test: OK")
finally:
    students.delete_student_by_id(student["id"])
