from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from school_face_id.config.security import SESSION_COOKIE, create_session, delete_session
from school_face_id.main import app
from school_face_id.repositories import attendance as attendance_repository
from school_face_id.repositories import students
from school_face_id.services import attendance as attendance_service
from school_face_id.services import event_photos
from school_face_id.services import parents as parent_service


def make_student(name, class_name="7A"):
    return students.save_student(name, class_name, "", None)


def session_client(role, **claims):
    csrf = f"{role}-attendance-csrf"
    token = create_session(role, login=f"{role}-attendance", csrf_token=csrf, **claims)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    client.headers["X-CSRF-Token"] = csrf
    return client, token


def test_events_are_append_only_and_allow_multiple_passes_per_day():
    student = make_student("Four Event Student")
    times = [
        ("2026-09-28T08:00:00+06:00", "enter"),
        ("2026-09-28T12:00:00+06:00", "exit"),
        ("2026-09-28T12:30:00+06:00", "enter"),
        ("2026-09-28T16:00:00+06:00", "exit"),
    ]
    ids = []
    for occurred_at, event_type in times:
        result = attendance_service.register_event(student["id"], event_type, occurred_at)
        assert result["status"] == "created"
        ids.append(result["event"]["id"])

    events = attendance_repository.get_student_events_for_day(student["id"], "2026-09-28")
    assert len(events) == 4
    assert len(set(ids)) == 4
    assert {event["event_type"] for event in events} == {"enter", "exit"}
    assert all(event["occurred_at"].endswith("+06:00") for event in events)

    summary = next(
        row for row in attendance_service.class_daily_report("7A")
        if row["student_id"] == student["id"] and row["date"] == "2026-09-28"
    )
    assert summary["arrived_at"] == "08:00:00"
    assert summary["left_at"] == "16:00:00"


def test_same_event_is_debounced_but_allowed_after_window():
    student = make_student("Debounce Event Student")
    start = datetime.fromisoformat("2026-09-29T08:00:00+06:00")
    first = attendance_service.register_event(student["id"], "enter", start)
    suppressed = attendance_service.register_event(student["id"], "enter", start + timedelta(seconds=2))
    later = attendance_service.register_event(student["id"], "enter", start + timedelta(seconds=61))

    assert first["status"] == "created"
    assert suppressed == {"status": "duplicate_suppressed", "event": None}
    assert later["status"] == "created"
    assert len(attendance_repository.get_student_events_for_day(student["id"], "2026-09-29")) == 2


def test_concurrent_same_event_is_debounced_within_single_api_process():
    student = make_student("Concurrent Debounce Student")
    occurred_at = "2026-09-29T08:00:00+06:00"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _index: attendance_service.register_event(
                student["id"], "enter", occurred_at, debounce_seconds=60
            ),
            range(16),
        ))
    assert sum(result["status"] == "created" for result in results) == 1
    assert sum(result["status"] == "duplicate_suppressed" for result in results) == 15


def test_class_summary_counts_distinct_students_not_events():
    first = make_student("Distinct Count Student A", "Count Class")
    second = make_student("Distinct Count Student B", "Count Class")
    today = attendance_service.local_now().date().isoformat()
    attendance_service.register_event(first["id"], "enter", f"{today}T08:00:00+06:00")
    attendance_service.register_event(first["id"], "enter", f"{today}T09:00:01+06:00")
    attendance_service.register_event(second["id"], "enter", f"{today}T08:30:00+06:00")

    row = next(item for item in attendance_service.classes_summary() if item[0] == "Count Class")
    assert row[2] == 2


def test_event_history_access_is_class_scoped_and_clear_is_admin_only():
    student_7a = make_student("Scoped Student 7A", "Scoped 7A")
    student_8b = make_student("Scoped Student 8B", "Scoped 8B")
    attendance_service.register_event(student_7a["id"], "enter", debounce_seconds=0)
    attendance_service.register_event(student_8b["id"], "enter", debounce_seconds=0)
    teacher, teacher_token = session_client("teacher", classes=["Scoped 7A"])
    admin, admin_token = session_client("admin")
    try:
        assert teacher.get(f"/api/v1/attendance/students/{student_7a['id']}/events").status_code == 200
        assert teacher.get(f"/api/v1/attendance/students/{student_8b['id']}/events").status_code == 403
        assert teacher.get("/api/v1/attendance/events").status_code == 403
        assert teacher.delete("/api/v1/attendance?class_name=Scoped%207A").status_code == 403
        response = admin.get("/api/v1/attendance/events?class_name=Scoped%208B")
        assert response.status_code == 200
        assert any(event["student_id"] == student_8b["id"] for event in response.json())
    finally:
        delete_session(teacher_token)
        delete_session(admin_token)


def test_event_photo_is_protected_and_scoped(tmp_path, monkeypatch):
    student = make_student("Photo Event Student", "Photo Class")
    other_parent = parent_service.create_parent(
        "photo-other-parent", "Photo Other Parent", None, "photo-secret"
    )
    own_parent = parent_service.create_parent(
        "photo-own-parent", "Photo Own Parent", None, "photo-secret"
    )
    parent_service.link_parent_to_student(own_parent["id"], student["id"], "guardian")
    photo = tmp_path / "event.jpg"
    photo.write_bytes(b"event-photo")
    monkeypatch.setattr(event_photos, "SCREENSHOTS_DIR", str(tmp_path))
    monkeypatch.setattr(event_photos, "PROJECT_ROOT", str(tmp_path))
    result = attendance_service.register_event(
        student["id"], "enter", photo_path="event.jpg", debounce_seconds=0
    )
    path = f"/api/v1/attendance/events/{result['event']['id']}/photo"

    anonymous = TestClient(app)
    admin, admin_token = session_client("admin")
    teacher, teacher_token = session_client("teacher", classes=["Photo Class"])
    wrong_teacher, wrong_teacher_token = session_client("teacher", classes=["Other Class"])
    own_client, own_token = session_client("parent", parent_id=own_parent["id"])
    other_client, other_token = session_client("parent", parent_id=other_parent["id"])
    try:
        assert anonymous.get(path).status_code == 401
        assert admin.get(path).status_code == 200
        assert teacher.get(path).status_code == 200
        assert wrong_teacher.get(path).status_code == 403
        assert own_client.get(path).status_code == 200
        assert other_client.get(path).status_code == 403
    finally:
        for token in (admin_token, teacher_token, wrong_teacher_token, own_token, other_token):
            delete_session(token)


def test_parent_events_only_contain_linked_children():
    parent_a = parent_service.create_parent("event-parent-a", "Event Parent A", None, "secret-a-1234")
    parent_b = parent_service.create_parent("event-parent-b", "Event Parent B", None, "secret-b-1234")
    child_a = make_student("Event Child A")
    child_b = make_student("Event Child B")
    parent_service.link_parent_to_student(parent_a["id"], child_a["id"], "mother")
    parent_service.link_parent_to_student(parent_b["id"], child_b["id"], "father")
    attendance_service.register_event(child_a["id"], "enter", debounce_seconds=0)
    attendance_service.register_event(child_b["id"], "enter", debounce_seconds=0)

    events = attendance_service.parent_events(parent_a["id"])
    assert {event["student_id"] for event in events} == {child_a["id"]}
