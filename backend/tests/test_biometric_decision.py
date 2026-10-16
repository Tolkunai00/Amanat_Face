from types import SimpleNamespace

import numpy as np
import pytest

from school_face_id.recognition import camera_service
from school_face_id.recognition.anti_spoof import PadResult
from school_face_id.recognition.models import FaceDecision, RejectionReason
from school_face_id.recognition.tracker import FaceTracker


def decision(**changes):
    values = {
        "track_id": "track-000001",
        "bbox": (10, 10, 100, 100),
        "student_id": "student-1",
        "quality_passed": True,
        "anti_spoof_passed": True,
        "liveness_passed": True,
        "recognition_passed": True,
    }
    values.update(changes)
    return FaceDecision(**values).finalize()


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"anti_spoof_passed": False}, RejectionReason.PAD_FAILED.value),
        ({"liveness_passed": False}, RejectionReason.LIVENESS_PENDING.value),
        ({"recognition_passed": False}, RejectionReason.UNKNOWN_FACE.value),
    ],
)
def test_failed_gate_forbids_attendance(change, reason):
    result = decision(**change)
    assert result.attendance_allowed is False
    assert result.rejection_reason == reason


class LivenessStub:
    def __init__(self, passed):
        self.passed = passed

    def is_live(self, *_args):
        return self.passed

    def remove(self, _track_id):
        pass


@pytest.mark.parametrize("failed_stage", ["pad", "liveness", "recognition"])
def test_pipeline_never_registers_attendance_when_security_stage_fails(monkeypatch, failed_stage):
    face = SimpleNamespace(
        bbox=np.asarray([50, 50, 200, 220], dtype=np.float32),
        det_score=0.95,
        embedding=np.ones(512, dtype=np.float32),
        kps=np.asarray([[80, 90], [160, 90], [120, 125], [90, 170], [150, 170]]),
    )
    monkeypatch.setattr(
        camera_service, "detect_faces",
        lambda _frame: camera_service.DetectionBatch((face,), "fast", 1.0, False),
    )
    monkeypatch.setattr(camera_service, "load_known_faces", lambda: {
        "student_ids": ["student-1"], "names": ["Student"], "parent_ids": [""],
        "classes": ["7A"], "embeddings": np.ones((1, 512), dtype=np.float32) / np.sqrt(512),
    })
    monkeypatch.setattr(
        camera_service, "evaluate_anti_spoof",
        lambda *_args: PadResult(passed=failed_stage != "pad"),
    )
    monkeypatch.setattr(
        camera_service, "match_identity_details",
        lambda *_args: ({"matched": False, "reason": "LOW_SIMILARITY", "score": 0.2, "margin": 0.1}
                       if failed_stage == "recognition" else
                       {"matched": True, "reason": "", "score": 0.9, "margin": 0.4,
                        "student_id": "student-1", "name": "Student", "class_name": "7A"}),
    )
    registered = []
    monkeypatch.setattr(
        camera_service.attendance_service, "register_camera_event",
        lambda *_args, **_kwargs: registered.append(True),
    )

    camera_service.recognize_frame(
        np.zeros((300, 300, 3), dtype=np.uint8), "keldi",
        LivenessStub(failed_stage != "liveness"), [], FaceTracker("keldi"),
    )
    assert registered == []


def test_pad_pass_cache_expires_and_is_rechecked(monkeypatch):
    face = SimpleNamespace(
        bbox=np.asarray([50, 50, 200, 220], dtype=np.float32), det_score=0.95,
        embedding=np.ones(512, dtype=np.float32),
        kps=np.asarray([[80, 90], [160, 90], [120, 125], [90, 170], [150, 170]]),
    )
    monkeypatch.setattr(
        camera_service, "detect_faces",
        lambda _frame: camera_service.DetectionBatch((face,), "fast", 1.0, False),
    )
    monkeypatch.setattr(camera_service, "load_known_faces", lambda: {
        "student_ids": ["student-1"], "names": ["Student"], "parent_ids": [""],
        "classes": ["7A"], "embeddings": np.ones((1, 512), dtype=np.float32) / np.sqrt(512),
    })
    monkeypatch.setattr(camera_service, "match_identity_details", lambda *_args: {
        "matched": True, "reason": "", "score": 0.9, "margin": 0.4,
        "student_id": "student-1", "name": "Student", "class_name": "7A",
    })
    pad_calls = []
    monkeypatch.setattr(
        camera_service, "evaluate_anti_spoof",
        lambda *_args: pad_calls.append(True) or PadResult(
            passed=True, score=0.9, predicted_class="live", inference_ms=1.0
        ),
    )
    monkeypatch.setattr(
        camera_service.attendance_service, "register_camera_event",
        lambda *_args, **_kwargs: {"status": "duplicate_suppressed", "event": None},
    )
    times = iter((1.0, 2.0, 4.1))
    monkeypatch.setattr(camera_service.time, "monotonic", lambda: next(times))
    tracker = FaceTracker("keldi", ttl_seconds=10.0)
    frame = np.zeros((300, 300, 3), dtype=np.uint8)

    for _ in range(3):
        camera_service.recognize_frame(frame, "keldi", LivenessStub(True), [], tracker)

    assert len(pad_calls) == 2


def test_pad_cache_is_isolated_between_tracks(monkeypatch):
    tracker = FaceTracker("keldi")
    tracks = tracker.update(
        [(10, 10, 100, 100), (150, 10, 240, 100)], 1.0
    ).tracks
    tracks[0].anti_spoof_passed = True
    tracks[0].anti_spoof_checked_at = 1.0
    assert tracks[1].anti_spoof_passed is False
    assert tracks[1].anti_spoof_checked_at is None
