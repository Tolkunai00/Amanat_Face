import time

import numpy as np
from fastapi.testclient import TestClient

from school_face_id.api.routers import health
from school_face_id.main import app
from school_face_id.recognition import camera_service


class FakeCapture:
    def __init__(self, opened=True):
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened and not self.released

    def release(self):
        self.released = True

    def get(self, _property):
        return 0.0


def test_camera_health_transitions_healthy_degraded_offline_healthy(monkeypatch):
    monkeypatch.setattr(camera_service, "CAMERA_OFFLINE_TIMEOUT_SECONDS", 10.0)
    worker = camera_service.CameraWorker("entrance", "rtsp://secret@camera/stream")
    worker.cap = FakeCapture()
    worker.last_frame_at = 100.0
    assert worker._health_status(101.0) == camera_service.CAMERA_HEALTHY
    worker.consecutive_failures = 1
    assert worker._health_status(101.0) == camera_service.CAMERA_DEGRADED
    assert worker._health_status(111.0) == camera_service.CAMERA_OFFLINE
    worker.consecutive_failures = 0
    worker.last_frame_at = 112.0
    assert worker._health_status(112.1) == camera_service.CAMERA_HEALTHY


def test_repeated_identical_frames_are_stale(monkeypatch):
    monkeypatch.setattr(camera_service, "CAMERA_STALE_ENABLED", True)
    monkeypatch.setattr(camera_service, "CAMERA_STALE_CONSECUTIVE_FRAMES", 2)
    monkeypatch.setattr(camera_service, "CAMERA_STALE_TIMEOUT_SECONDS", 1.0)
    worker = camera_service.CameraWorker("entrance", 0)
    capture = FakeCapture()
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    assert worker._frame_is_stale(frame, capture, 1.0) is False
    assert worker._frame_is_stale(frame, capture, 2.0) is False
    assert worker._frame_is_stale(frame, capture, 3.1) is True


def test_live_frames_with_fixed_capture_timestamp_do_not_reconnect(monkeypatch):
    monkeypatch.setattr(camera_service, "CAMERA_STALE_ENABLED", True)
    monkeypatch.setattr(camera_service, "CAMERA_STALE_CONSECUTIVE_FRAMES", 2)
    monkeypatch.setattr(camera_service, "CAMERA_STALE_TIMEOUT_SECONDS", 1.0)
    worker = camera_service.CameraWorker("entrance", 0)
    capture = FakeCapture()
    capture.get = lambda _property: 1000.0
    for value in range(20):
        frame = np.full((64, 64, 3), value, dtype=np.uint8)
        assert not worker._frame_is_stale(frame, capture, float(value))


def test_watchdog_reports_timeout_without_releasing_capture_during_read(monkeypatch):
    monkeypatch.setattr(camera_service, "CAMERA_READ_MAX_SECONDS", 2.0)
    worker = camera_service.CameraWorker("entrance", "rtsp://camera/stream")
    capture = FakeCapture()
    worker.cap = capture
    worker._read_started_at = 10.0
    original_thread = worker.thread
    assert worker._watchdog_check(now=13.0) is True
    assert capture.released is False
    assert worker.cap is capture
    assert worker.state == "error"
    assert worker._watchdog_check(now=14.0) is False
    assert worker.reconnects_total == 0
    assert worker.thread is original_thread


def test_new_capture_resets_stale_history_without_resetting_backoff(monkeypatch):
    worker = camera_service.CameraWorker("entrance", 0)
    worker._last_fingerprint = 12
    worker._identical_frames = 100
    worker._identical_since = 1.0
    worker._last_capture_position = 500.0
    worker._position_streak = 100
    worker.reconnect_delay = 16.0
    monkeypatch.setattr(worker, "_open_camera", FakeCapture)
    assert worker._connect()
    assert worker._last_fingerprint is None
    assert worker._identical_frames == 0
    assert worker._last_capture_position is None
    assert worker._position_streak == 0
    assert worker.reconnect_delay == 16.0


def test_slow_recognition_does_not_block_preview_or_accumulate_frames(monkeypatch):
    import threading

    entered = threading.Event()
    finish = threading.Event()
    calls = []

    class LiveCapture(FakeCapture):
        def read(self):
            return True, np.random.default_rng().integers(0, 255, (64, 64, 3), dtype=np.uint8)

    def recognize(*args):
        calls.append(args[0])
        entered.set()
        assert finish.wait(3)
        return []

    monkeypatch.setattr(camera_service, "recognize_frame", recognize)
    monkeypatch.setattr(camera_service, "RECOGNITION_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(camera_service, "CAMERA_STALE_ENABLED", False)
    worker = camera_service.CameraWorker("entrance", 0)
    capture = LiveCapture()
    monkeypatch.setattr(worker, "_open_camera", lambda: capture)
    worker.start()
    try:
        assert entered.wait(2)
        version, _ = worker.wait_for_frame(-1, timeout=2)
        newer, jpeg = worker.wait_for_frame(version, timeout=2)
        assert newer > version
        newest, jpeg = worker.wait_for_frame(newer, timeout=2)
        assert newest > newer
        assert jpeg.startswith(b"\xff\xd8")
        assert len(calls) == 1
        assert worker._pending_recognition is not None
        assert worker.public_status()["state"] == "working"
    finally:
        worker.running = False
        finish.set()
        worker.stop()
    assert capture.released
    assert not worker.thread.is_alive()
    assert not worker.recognition_thread.is_alive()


def test_stop_does_not_release_capture_while_reader_is_using_it(monkeypatch):
    worker = camera_service.CameraWorker("entrance", 0)
    capture = FakeCapture()
    worker.cap = capture
    worker.running = True
    worker.stop()
    assert not capture.released
    # The reader owns cleanup, including after a delayed read returns.
    worker._run()
    assert capture.released


def test_camera_open_failure_is_counted(monkeypatch):
    worker = camera_service.CameraWorker("entrance", "rtsp://camera/stream")
    monkeypatch.setattr(worker, "_open_camera", lambda: FakeCapture(opened=False))
    monkeypatch.setattr(worker, "_publish_unavailable", lambda _message: None)
    assert worker._connect() is False
    assert worker.frames_failed_total == 1
    assert worker.reconnects_total == 1
    assert worker._health_status() == camera_service.CAMERA_OFFLINE


def test_reconnect_recovery_returns_to_healthy(monkeypatch):
    worker = camera_service.CameraWorker("entrance", "rtsp://camera/stream")
    monkeypatch.setattr(worker, "_publish_unavailable", lambda _message: None)
    worker.cap = FakeCapture()
    worker._schedule_reconnect("intermittent failure", expected_capture=worker.cap)
    assert worker.reconnects_total == 1
    worker.cap = FakeCapture()
    worker.last_frame_at = time.time()
    worker.consecutive_failures = 0
    assert worker._health_status() == camera_service.CAMERA_HEALTHY


def test_camera_health_endpoint_never_exposes_rtsp_credentials(monkeypatch):
    monkeypatch.setattr(
        health,
        "CAMERA_INDEXES",
        {"entrance": "rtsp://admin:top-secret@192.0.2.1/stream"},
    )
    monkeypatch.setattr(camera_service, "camera_workers", {})
    response = TestClient(app).get("/api/v1/health/cameras")
    assert response.status_code == 200
    assert response.json()["cameras"][0]["camera_id"] == "entrance"
    assert "top-secret" not in response.text
    assert "192.0.2.1" not in response.text
