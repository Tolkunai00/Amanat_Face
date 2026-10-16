from types import SimpleNamespace

import numpy as np

from scripts import camera_soak_test, recognition_load_test


class FakeCapture:
    def __init__(self):
        self.opened = True
        self.value = 0

    def isOpened(self):
        return self.opened

    def read(self):
        self.value = (self.value + 1) % 255
        return True, np.full((32, 32, 3), self.value, dtype=np.uint8)

    def release(self):
        self.opened = False


def test_soak_duration_units():
    assert camera_soak_test.duration_seconds("5m") == 300
    assert camera_soak_test.duration_seconds("1h") == 3600
    assert camera_soak_test.duration_seconds("12.5") == 12.5


def test_soak_report_requires_success_and_meets_configured_criteria(monkeypatch, tmp_path):
    monkeypatch.setattr(camera_soak_test, "open_capture", lambda _source: FakeCapture())
    report = tmp_path / "soak.json"
    args = SimpleNamespace(
        camera="0", duration=0.02, report=str(report), csv=None, sample_interval=0.01,
        device_info=False, pipeline=False, stale_frames=30, max_failure_rate=0.0,
        max_reconnects=0, max_offline_seconds=1.0, max_p95_latency=1000.0,
    )
    assert camera_soak_test.run(args) == 0
    assert '"passed": true' in report.read_text(encoding="utf-8").lower()


def test_synthetic_load_test_is_dry_run(monkeypatch, tmp_path):
    report = tmp_path / "load.json"
    args = SimpleNamespace(
        mode="synthetic", input=None, streams=2, iterations=8,
        synthetic_faces=2, gallery_size=8, report=str(report),
    )
    assert recognition_load_test.run(args) == 0
    content = report.read_text(encoding="utf-8")
    assert '"attendance_writes": false' in content.lower()
    assert '"faces_processed": 16' in content
