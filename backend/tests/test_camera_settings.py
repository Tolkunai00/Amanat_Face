import json
from urllib.parse import unquote, urlsplit

import pytest
from pydantic import ValidationError

from school_face_id.services import camera_settings as settings


@pytest.fixture
def private_settings(monkeypatch, tmp_path):
    sources = {"keldi": "rtsp://admin:old%40secret@192.168.0.100:554/V_ENC_000", "ketti": 0, "classroom": ""}
    monkeypatch.setattr(settings, "CAMERA_INDEXES", sources)
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "camera-settings.json")
    monkeypatch.setattr(settings, "camera_names", {})
    return sources


def test_config_never_returns_password_or_credential_url(private_settings):
    public = settings.public_config("keldi")
    assert public["host"] == "192.168.0.100"
    assert public["has_password"] is True
    assert "secret" not in json.dumps(public)
    assert "password" not in public


def test_password_is_retained_and_special_characters_are_encoded(private_settings):
    config = settings.CameraConfig(host="192.168.0.101", username="a@b", password="p:@/#")
    parsed = urlsplit(settings.build_source("keldi", config))
    assert unquote(parsed.username) == "a@b"
    assert unquote(parsed.password) == "p:@/#"
    retained = settings.build_source("keldi", settings.CameraConfig(host="192.168.0.101"))
    assert unquote(urlsplit(retained).password) == "old@secret"


def test_save_applies_worker_and_survives_reload(private_settings, monkeypatch):
    from school_face_id.recognition import camera_service
    calls = []
    monkeypatch.setattr(camera_service, "get_camera_worker", lambda *args: calls.append(args))
    result = settings.save_config("keldi", settings.CameraConfig(host="192.168.0.101"))
    assert result["host"] == "192.168.0.101"
    assert calls == [("keldi", private_settings["keldi"])]
    private_settings["keldi"] = ""
    settings.load_camera_settings()
    assert urlsplit(private_settings["keldi"]).hostname == "192.168.0.101"
    assert private_settings["ketti"] == 0


@pytest.mark.parametrize("values", [{"host": "rtsp://admin:secret@host"}, {"host": "192.168.0.100", "port": 0}, {"host": "192.168.0.100", "path": "bad"}])
def test_invalid_settings_are_rejected(values):
    with pytest.raises(ValidationError):
        settings.CameraConfig(**values)


def test_video_check_releases_capture_without_changing_settings(private_settings, monkeypatch):
    import cv2
    released = []
    class Capture:
        def isOpened(self): return True
        def read(self): return True, object()
        def release(self): released.append(True)
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: Capture())
    original = dict(private_settings)
    assert settings.test_connection("keldi", settings.CameraConfig(host="192.168.0.102")) == {"connected": True}
    assert private_settings == original
    assert released == [True]


def test_config_api_requires_authentication(private_settings):
    from fastapi.testclient import TestClient
    from school_face_id.main import app
    client = TestClient(app)
    assert client.get("/api/v1/camera/config").status_code == 401


def test_classroom_config_is_independent_and_persistent(private_settings, monkeypatch):
    from school_face_id.recognition import camera_service
    monkeypatch.setattr(camera_service, "get_camera_worker", lambda *args: None)
    entrance = private_settings["keldi"]
    settings.save_config("classroom", settings.CameraConfig(host="192.168.0.110", password="class-secret"))
    private_settings["classroom"] = ""
    settings.load_camera_settings()
    assert settings.public_config("classroom")["host"] == "192.168.0.110"
    assert private_settings["keldi"] == entrance
    assert private_settings["ketti"] == 0


def test_classroom_camera_starts_no_recognition_thread(monkeypatch):
    from school_face_id.recognition import camera_service
    started = []
    class FakeThread:
        def __init__(self, *, target, name, daemon): self.name = name
        def start(self): started.append(self.name)
        def is_alive(self): return False
    monkeypatch.setattr(camera_service.threading, "Thread", FakeThread)
    worker = camera_service.CameraWorker("classroom", "rtsp://camera/stream")
    worker.start()
    assert worker.recognition_thread is None
    assert started == ["camera-classroom", "camera-watchdog-classroom"]


def test_unconfigured_camera_starts_no_threads():
    from school_face_id.recognition import camera_service
    worker = camera_service.CameraWorker("classroom", "")
    worker.start()
    assert worker.running is False
    assert worker.thread is None


def test_multiple_classrooms_create_rename_delete_and_reload(private_settings, monkeypatch):
    from school_face_id.recognition import camera_service
    monkeypatch.setattr(camera_service, "get_camera_worker", lambda *args: None)
    monkeypatch.setattr(camera_service, "camera_workers", {})
    first = settings.create_classroom(settings.CameraConfig(host="192.168.0.111", name="Room 201", password="first-secret"))
    second = settings.create_classroom(settings.CameraConfig(host="192.168.0.112", name="Room 202", password="second-secret"))
    assert first["direction"] != second["direction"]
    assert first["name"] == "Room 201"
    assert "secret" not in json.dumps([first, second])
    settings.save_config(first["direction"], settings.CameraConfig(host="192.168.0.113", name="Renamed"))
    assert unquote(urlsplit(private_settings[first["direction"]]).password) == "first-secret"
    settings.delete_classroom(second["direction"])
    assert second["direction"] not in private_settings
    private_settings.pop(first["direction"])
    settings.camera_names.clear()
    settings.load_camera_settings()
    assert settings.public_config(first["direction"])["name"] == "Renamed"
    assert settings.public_config(first["direction"])["host"] == "192.168.0.113"
    assert second["direction"] not in private_settings
    assert private_settings["ketti"] == 0


def test_legacy_classroom_survives_new_camera_creation(private_settings, monkeypatch):
    from school_face_id.recognition import camera_service
    monkeypatch.setattr(camera_service, "get_camera_worker", lambda *args: None)
    private_settings["classroom"] = "rtsp://admin:legacy@192.168.0.110:554/V_ENC_000"
    settings.create_classroom(settings.CameraConfig(host="192.168.0.111", name="New room"))
    private_settings["classroom"] = ""
    settings.load_camera_settings()
    assert settings.public_config("classroom")["host"] == "192.168.0.110"


def test_entrance_cameras_cannot_be_deleted(private_settings):
    with pytest.raises(KeyError):
        settings.delete_classroom("keldi")
    assert "keldi" in private_settings
