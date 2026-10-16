from fastapi.testclient import TestClient

from school_face_id.config.security import SESSION_COOKIE, create_session, delete_session
from school_face_id.data.database import save_teacher
from school_face_id.main import app
from school_face_id.repositories import notifications as notification_repository
from school_face_id.repositories import students, teachers
from school_face_id.services import attendance as attendance_service
from school_face_id.services import notifications as notification_service
from school_face_id.services import parents as parent_service


def make_student(name, class_name):
    return students.save_student(name, class_name, "", None)


def make_parent(login, child, relationship="guardian"):
    parent = parent_service.create_parent(login, login, None, f"{login}-secret")
    parent_service.link_parent_to_student(parent["id"], child["id"], relationship)
    return parent


def make_teacher(login, class_name):
    assert save_teacher(login, f"{login}-secret", [class_name])
    return teachers.get_teacher_by_login(login)


def client_for(role, **claims):
    csrf = f"notification-{role}-csrf"
    token = create_session(role, login=claims.pop("login", role), csrf_token=csrf, **claims)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    client.headers["X-CSRF-Token"] = csrf
    return client, token


def test_parent_receives_enter_and_exit_notifications():
    child = make_student("Notification Parent Child", "Notify 1A")
    parent = make_parent("notify-parent-enter-exit", child)
    entered = attendance_service.register_event(
        child["id"], "enter", "2026-10-01T08:03:00+06:00", debounce_seconds=0
    )["event"]
    exited = attendance_service.register_event(
        child["id"], "exit", "2026-10-01T15:42:00+06:00", debounce_seconds=0
    )["event"]

    rows = notification_repository.list_notifications_for_parent(parent["id"], limit=10)
    assert {row["notification_type"] for row in rows} == {
        "student_entered", "student_exited"
    }
    assert {row["event"]["id"] for row in rows} == {entered["id"], exited["id"]}
    assert any("08:03" in row["message"] for row in rows)
    assert any("15:42" in row["message"] for row in rows)


def test_all_parents_and_only_assigned_teachers_receive_notification():
    child = make_student("Multiple Recipient Child", "Notify 2A")
    parent_a = make_parent("notify-multi-parent-a", child, "mother")
    parent_b = make_parent("notify-multi-parent-b", child, "father")
    teacher_a = make_teacher("notify-teacher-a", "Notify 2A")
    teacher_b = make_teacher("notify-teacher-b", "Notify 2B")
    event = attendance_service.register_event(
        child["id"], "enter", "2026-10-02T08:00:00+06:00", debounce_seconds=0
    )["event"]

    assert len(notification_repository.list_notifications_for_parent(parent_a["id"])) == 1
    assert len(notification_repository.list_notifications_for_parent(parent_b["id"])) == 1
    teacher_a_rows = notification_repository.list_notifications_for_teacher(teacher_a["id"])
    assert [row["event"]["id"] for row in teacher_a_rows] == [event["id"]]
    assert notification_repository.list_notifications_for_teacher(teacher_b["id"]) == []
    assert "(Notify 2A)" in teacher_a_rows[0]["message"]


def test_creation_is_idempotent_and_debounce_creates_no_notification():
    child = make_student("Idempotent Notification Child", "Notify 3A")
    parent = make_parent("notify-idempotent-parent", child)
    first = attendance_service.register_event(
        child["id"], "enter", "2026-10-03T08:00:00+06:00"
    )
    duplicate = attendance_service.register_event(
        child["id"], "enter", "2026-10-03T08:00:02+06:00"
    )
    before = notification_repository.list_notifications_for_parent(parent["id"])
    created_again = notification_service.create_for_attendance_event(first["event"]["id"])
    after = notification_repository.list_notifications_for_parent(parent["id"])

    assert duplicate["status"] == "duplicate_suppressed"
    assert len(before) == len(after) == 1
    assert created_again == []


def test_parent_api_scope_read_state_and_privacy():
    child_a = make_student("Notification API Child A", "Notify 4A")
    child_b = make_student("Notification API Child B", "Notify 4B")
    parent_a = make_parent("notify-api-parent-a", child_a)
    parent_b = make_parent("notify-api-parent-b", child_b)
    attendance_service.register_event(child_a["id"], "enter", debounce_seconds=0)
    attendance_service.register_event(child_b["id"], "enter", debounce_seconds=0)
    own = notification_repository.list_notifications_for_parent(parent_a["id"])[0]
    other = notification_repository.list_notifications_for_parent(parent_b["id"])[0]
    client, token = client_for("parent", parent_id=parent_a["id"])
    try:
        listed = client.get("/api/v1/notifications")
        assert listed.status_code == 200
        assert {row["id"] for row in listed.json()} == {own["id"]}
        assert "password_hash" not in listed.text
        assert "photo_path" not in listed.text
        assert client.get("/api/v1/notifications/unread-count").json() == {"count": 1}
        assert client.post(f"/api/v1/notifications/{other['id']}/read").status_code == 404
        marked = client.post(f"/api/v1/notifications/{own['id']}/read")
        assert marked.status_code == 200
        assert marked.json()["is_read"] is True
        assert marked.json()["read_at"] is not None
        assert client.get("/api/v1/notifications/unread-count").json() == {"count": 0}
    finally:
        delete_session(token)


def test_teacher_cannot_read_or_mark_another_teacher_notification():
    child_a = make_student("Teacher Notification Child A", "Notify 5A")
    child_b = make_student("Teacher Notification Child B", "Notify 5B")
    teacher_a = make_teacher("notify-scope-teacher-a", "Notify 5A")
    teacher_b = make_teacher("notify-scope-teacher-b", "Notify 5B")
    attendance_service.register_event(child_a["id"], "enter", debounce_seconds=0)
    attendance_service.register_event(child_b["id"], "enter", debounce_seconds=0)
    own = notification_repository.list_notifications_for_teacher(teacher_a["id"])[0]
    other = notification_repository.list_notifications_for_teacher(teacher_b["id"])[0]
    client, token = client_for(
        "teacher", login=teacher_a["login"], teacher_id=teacher_a["id"], classes=["Notify 5A"]
    )
    try:
        listed = client.get("/api/v1/notifications").json()
        assert {row["id"] for row in listed} == {own["id"]}
        assert client.post(f"/api/v1/notifications/{other['id']}/read").status_code == 404
    finally:
        delete_session(token)


def test_teacher_login_session_contains_stable_teacher_id():
    teacher = make_teacher("notify-login-teacher", "Notify Login Class")
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"login": "notify-login-teacher", "password": "notify-login-teacher-secret"},
    )
    assert response.status_code == 200
    assert response.json()["teacher_id"] == teacher["id"]
    assert client.get("/api/v1/auth/me").json()["teacher_id"] == teacher["id"]


def test_read_all_only_changes_current_recipient():
    child_a = make_student("Read All Child A", "Notify 6A")
    child_b = make_student("Read All Child B", "Notify 6B")
    parent_a = make_parent("notify-read-all-a", child_a)
    parent_b = make_parent("notify-read-all-b", child_b)
    for hour in (8, 9, 10):
        attendance_service.register_event(
            child_a["id"], "enter" if hour % 2 == 0 else "exit",
            f"2026-10-06T{hour:02d}:00:00+06:00", debounce_seconds=0,
        )
    for hour in (8, 9):
        attendance_service.register_event(
            child_b["id"], "enter" if hour % 2 == 0 else "exit",
            f"2026-10-06T{hour:02d}:00:00+06:00", debounce_seconds=0,
        )
    client, token = client_for("parent", parent_id=parent_a["id"])
    try:
        response = client.post("/api/v1/notifications/read-all")
        assert response.status_code == 200
        assert response.json()["count"] == 3
        assert notification_repository.count_unread("parent", parent_a["id"]) == 0
        assert notification_repository.count_unread("parent", parent_b["id"]) == 2
    finally:
        delete_session(token)


def test_notification_survives_new_database_connection():
    child = make_student("Persistent Notification Child", "Notify 7A")
    parent = make_parent("notify-persistent-parent", child)
    attendance_service.register_event(child["id"], "enter", debounce_seconds=0)
    notification_id = notification_repository.list_notifications_for_parent(parent["id"])[0]["id"]

    loaded_from_new_connection = notification_repository.get_notification_by_id(notification_id)
    assert loaded_from_new_connection["id"] == notification_id
    assert loaded_from_new_connection["recipient_id"] == parent["id"]


def test_notification_failure_does_not_rollback_attendance(monkeypatch, caplog):
    from school_face_id.services import notifications

    caplog.set_level("ERROR")
    child = make_student("Notification Failure Child", "Notify 8A")
    monkeypatch.setattr(
        notifications, "create_for_attendance_event",
        lambda _event_id: (_ for _ in ()).throw(RuntimeError("temporary notification failure")),
    )
    result = attendance_service.register_event(child["id"], "enter", debounce_seconds=0)

    assert result["status"] == "created"
    assert attendance_service.get_event(result["event"]["id"])["id"] == result["event"]["id"]
    assert "Notification generation failed" in caplog.text
