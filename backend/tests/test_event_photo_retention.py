from datetime import timedelta
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from school_face_id.config import settings
from school_face_id.config.security import SESSION_COOKIE, create_session, delete_session
from school_face_id.main import app
from school_face_id.repositories import attendance as attendance_repository
from school_face_id.repositories import notifications as notification_repository
from school_face_id.repositories import students
from school_face_id.services import attendance as attendance_service
from school_face_id.services import event_photos
from school_face_id.services import parents as parent_service


def _student(label):
    return students.save_student(label, "Retention Class", "", None)


def _event_with_file(tmp_path, name, expires_at):
    student = _student(name)
    event = attendance_service.register_event(
        student["id"], "enter", debounce_seconds=0
    )["event"]
    filename = f"{event['id']}.jpg"
    (tmp_path / filename).write_bytes(b"jpeg")
    attendance_repository.set_event_photo(event["id"], filename, expires_at)
    return student, attendance_repository.get_event_by_id(event["id"]), tmp_path / filename


def test_expired_photo_is_deleted_but_event_and_notification_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(event_photos, "SCREENSHOTS_DIR", str(tmp_path))
    parent = parent_service.create_parent(
        "retention-parent", "Retention Parent", None, "secret-123456"
    )
    student = _student("Expired Photo Student")
    parent_service.link_parent_to_student(parent["id"], student["id"], "guardian")
    event = attendance_service.register_event(
        student["id"], "enter", debounce_seconds=0
    )["event"]
    filename = f"{event['id']}.jpg"
    photo = tmp_path / filename
    photo.write_bytes(b"jpeg")
    past = (attendance_service.local_now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    attendance_repository.set_event_photo(event["id"], filename, past)

    stats = event_photos.cleanup_expired_event_photos()

    assert stats["scanned"] >= 1
    assert stats["deleted"] >= 1
    assert stats["failed"] == 0
    assert not photo.exists()
    stored = attendance_repository.get_event_by_id(event["id"])
    assert stored is not None and stored["photo_path"] is None
    assert attendance_repository.get_student_events(student["id"])
    assert notification_repository.list_notifications_for_parent(parent["id"])
    assert event_photos.cleanup_expired_event_photos()["deleted"] == 0


def test_future_photo_is_untouched_and_dry_run_does_not_mutate(tmp_path, monkeypatch):
    monkeypatch.setattr(event_photos, "SCREENSHOTS_DIR", str(tmp_path))
    future = (attendance_service.local_now() + timedelta(days=1)).isoformat(timespec="seconds")
    _, event, photo = _event_with_file(tmp_path, "Future Photo Student", future)

    assert event_photos.cleanup_expired_event_photos()["scanned"] == 0
    assert photo.exists()
    assert attendance_repository.get_event_by_id(event["id"])["photo_path"]

    past = (attendance_service.local_now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    attendance_repository.set_event_photo(event["id"], event["photo_path"], past)
    stats = event_photos.cleanup_expired_event_photos(dry_run=True)
    assert stats["would_delete"] >= 1
    assert photo.exists()
    assert attendance_repository.get_event_by_id(event["id"])["photo_path"]


def test_missing_file_repairs_stale_database_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(event_photos, "SCREENSHOTS_DIR", str(tmp_path))
    student = _student("Missing Photo Student")
    event = attendance_service.register_event(student["id"], "enter", debounce_seconds=0)["event"]
    past = (attendance_service.local_now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    attendance_repository.set_event_photo(event["id"], f"{event['id']}.jpg", past)

    stats = event_photos.cleanup_expired_event_photos()

    assert stats["missing"] >= 1
    assert attendance_repository.get_event_by_id(event["id"])["photo_path"] is None


def test_delete_failure_keeps_database_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(event_photos, "SCREENSHOTS_DIR", str(tmp_path))
    past = (attendance_service.local_now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    _, event, photo = _event_with_file(tmp_path, "Delete Failure Student", past)
    original_unlink = Path.unlink

    def denied(path, *args, **kwargs):
        if path.resolve() == photo.resolve():
            raise PermissionError("simulated delete denial")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied)
    stats = event_photos.cleanup_expired_event_photos()

    assert stats["failed"] >= 1
    assert attendance_repository.get_event_by_id(event["id"])["photo_path"]


def test_retention_zero_disables_file_but_keeps_event_and_notification(tmp_path, monkeypatch):
    monkeypatch.setattr(event_photos, "SCREENSHOTS_DIR", str(tmp_path))
    monkeypatch.setattr(event_photos, "SCREENSHOT_RETENTION_DAYS", 0)
    parent = parent_service.create_parent("no-photo-parent", "No Photo", None, "secret-123456")
    student = _student("No Screenshot Student")
    parent_service.link_parent_to_student(parent["id"], student["id"], "guardian")

    result = attendance_service.register_event(
        student["id"], "enter", screenshot_frame=np.zeros((8, 8, 3), dtype=np.uint8),
        debounce_seconds=0,
    )

    assert result["status"] == "created"
    assert result["event"]["photo_path"] is None
    assert list(tmp_path.iterdir()) == []
    assert notification_repository.list_notifications_for_parent(parent["id"])


def test_path_traversal_is_never_read_or_deleted(tmp_path, monkeypatch):
    root = tmp_path / "screenshots"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(event_photos, "SCREENSHOTS_DIR", str(root))
    student = _student("Traversal Photo Student")
    event = attendance_service.register_event(student["id"], "enter", debounce_seconds=0)["event"]
    past = (attendance_service.local_now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    attendance_repository.set_event_photo(event["id"], "../secret.txt", past)

    stats = event_photos.cleanup_expired_event_photos()

    assert stats["failed"] >= 1
    assert secret.read_text(encoding="utf-8") == "keep"
    assert attendance_repository.get_event_by_id(event["id"])["photo_path"] == "../secret.txt"


def test_legacy_project_relative_screenshot_path_is_supported(tmp_path, monkeypatch):
    root = tmp_path / "screenshots"
    root.mkdir()
    photo = root / "legacy.jpg"
    photo.write_bytes(b"legacy")
    monkeypatch.setattr(event_photos, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(event_photos, "SCREENSHOTS_DIR", str(root))

    assert event_photos.resolve_event_photo_path("screenshots/legacy.jpg") == photo.resolve()


def test_new_screenshot_uses_event_id_and_expiration(tmp_path, monkeypatch):
    monkeypatch.setattr(event_photos, "SCREENSHOTS_DIR", str(tmp_path))
    monkeypatch.setattr(event_photos, "SCREENSHOT_RETENTION_DAYS", 7)
    student = _student("Safe Filename Student")
    result = attendance_service.register_event(
        student["id"], "enter", occurred_at="2026-09-28T08:00:00+06:00",
        screenshot_frame=np.zeros((8, 8, 3), dtype=np.uint8), debounce_seconds=0,
    )
    event = result["event"]

    assert event["photo_path"] == f"{event['id']}.jpg"
    assert (tmp_path / event["photo_path"]).is_file()
    assert event["photo_expires_at"] == "2026-10-05T08:00:00+06:00"


def test_retention_setting_parsing_has_clear_errors(monkeypatch):
    for value in ("0", "1", "7", "30"):
        monkeypatch.setenv("RETENTION_TEST", value)
        assert settings.env_nonnegative_int("RETENTION_TEST", 7) == int(value)
    monkeypatch.setenv("RETENTION_TEST", "-1")
    try:
        settings.env_nonnegative_int("RETENTION_TEST", 7)
        assert False, "negative retention must fail"
    except RuntimeError as exc:
        assert "zero or greater" in str(exc)
    monkeypatch.setenv("RETENTION_TEST", "not-a-number")
    try:
        settings.env_nonnegative_int("RETENTION_TEST", 7)
        assert False, "non-numeric retention must fail"
    except RuntimeError as exc:
        assert "must be an integer" in str(exc)


def test_full_photo_lifecycle_keeps_event_and_notification(tmp_path, monkeypatch):
    monkeypatch.setattr(event_photos, "SCREENSHOTS_DIR", str(tmp_path))
    parent = parent_service.create_parent(
        "lifecycle-parent", "Lifecycle Parent", None, "secret-123456"
    )
    student = _student("Lifecycle Student")
    parent_service.link_parent_to_student(parent["id"], student["id"], "guardian")
    event = attendance_service.register_event(
        student["id"], "enter", screenshot_frame=np.zeros((8, 8, 3), dtype=np.uint8),
        debounce_seconds=0,
    )["event"]
    token = create_session("admin", login="retention-smoke", csrf_token="smoke-csrf")
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    try:
        photo_url = f"/api/v1/attendance/events/{event['id']}/photo"
        assert client.get(photo_url).status_code == 200
        attendance_repository.set_event_photo(
            event["id"], event["photo_path"],
            (attendance_service.local_now() - timedelta(minutes=1)).isoformat(timespec="seconds"),
        )
        assert event_photos.cleanup_expired_event_photos()["deleted"] >= 1
        assert client.get(photo_url).status_code == 404
        response = client.get(
            f"/api/v1/attendance/events?student_id={student['id']}"
        )
        assert response.status_code == 200
        assert any(item["id"] == event["id"] for item in response.json())
        assert notification_repository.list_notifications_for_parent(parent["id"])
    finally:
        delete_session(token)
