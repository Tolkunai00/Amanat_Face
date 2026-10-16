import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np

from school_face_id.recognition import camera_service


class FakeFaceAnalysis:
    instances = 0

    def __init__(self, **kwargs):
        type(self).instances += 1
        self.prepared = None

    def prepare(self, **kwargs):
        self.prepared = kwargs


def test_face_model_is_cached_per_profile_with_real_detection_sizes(monkeypatch):
    monkeypatch.setattr(camera_service, "FaceAnalysis", FakeFaceAnalysis)
    monkeypatch.setattr(camera_service, "_face_apps", {})
    monkeypatch.setattr(camera_service, "_face_inference_locks", {})
    FakeFaceAnalysis.instances = 0

    first = camera_service.get_face_app("fast")
    second = camera_service.get_face_app("accurate")
    first_again = camera_service.get_face_app("fast")

    assert first is first_again
    assert first is not second
    assert first.prepared["det_size"] == (320, 320)
    assert second.prepared["det_size"] == (480, 480)
    assert FakeFaceAnalysis.instances == 2


def test_face_profile_initialization_is_thread_safe(monkeypatch):
    monkeypatch.setattr(camera_service, "FaceAnalysis", FakeFaceAnalysis)
    monkeypatch.setattr(camera_service, "_face_apps", {})
    monkeypatch.setattr(camera_service, "_face_inference_locks", {})
    FakeFaceAnalysis.instances = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        apps = list(pool.map(lambda _: camera_service.get_face_app("fallback"), range(24)))
    assert len({id(app) for app in apps}) == 1
    assert apps[0].prepared["det_size"] == (640, 640)
    assert FakeFaceAnalysis.instances == 1


def test_detector_fallback_runs_only_until_usable_face(monkeypatch):
    calls = []
    usable = SimpleNamespace(
        bbox=np.asarray([50, 50, 180, 200], dtype=np.float32), det_score=0.9
    )

    def run(profile, _frame):
        calls.append(profile)
        return [] if profile == "fast" else [usable]

    monkeypatch.setattr(camera_service, "_run_face_app", run)
    result = camera_service.detect_faces(np.zeros((300, 300, 3), dtype=np.uint8))

    assert calls == ["fast", "accurate"]
    assert result.profile_name == "accurate"
    assert result.fallback_used is True


def test_detector_does_not_run_fallback_when_fast_is_usable(monkeypatch):
    calls = []
    usable = SimpleNamespace(
        bbox=np.asarray([50, 50, 180, 200], dtype=np.float32), det_score=0.9
    )
    monkeypatch.setattr(
        camera_service, "_run_face_app",
        lambda profile, _frame: calls.append(profile) or [usable],
    )

    result = camera_service.detect_faces(np.zeros((300, 300, 3), dtype=np.uint8))

    assert calls == ["fast"]
    assert result.profile_name == "fast"
    assert result.fallback_used is False


def test_all_viewers_receive_same_shared_encoded_frame(monkeypatch):
    class SharedWorker:
        running = True

        def wait_for_frame(self, version):
            return version + 1, b"one-recognized-jpeg"

    worker = SharedWorker()
    monkeypatch.setattr(camera_service, "get_camera_worker", lambda status, source: worker)
    first_viewer = camera_service.camera_frames("keldi", "source")
    second_viewer = camera_service.camera_frames("keldi", "source")

    assert next(first_viewer) == next(second_viewer)


def test_reconnect_delay_increases_and_is_capped(monkeypatch):
    worker = camera_service.CameraWorker("keldi", "rtsp://camera/stream")
    monkeypatch.setattr(worker, "_publish_unavailable", lambda message: None)
    delays = []
    for _ in range(8):
        started = time.monotonic()
        worker._schedule_reconnect("offline")
        delays.append(round(worker.next_reconnect_at - started))

    assert delays[:4] == [1, 2, 4, 8]
    assert delays[-1] == 60
    assert worker.reconnect_delay == 60
