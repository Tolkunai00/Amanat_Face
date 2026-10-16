import numpy as np
import pytest
from types import SimpleNamespace

from school_face_id.recognition import camera_service
from school_face_id.recognition.anti_spoof import PadResult
from school_face_id.recognition.fsvfm_pad import FSVFMResult
from school_face_id.recognition.matcher import match_identity_details
from school_face_id.recognition.models import FaceDecision, RejectionReason
from school_face_id.recognition.tracker import FaceTracker
from school_face_id.repositories import students as student_repository
from school_face_id.services import students as student_service
from school_face_id.db.connection import get_connection


def normalized(values):
    vector = np.asarray(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def gallery_match(query, matrix, threshold=0.5, margin=0.05):
    count = len(matrix)
    return match_identity_details(
        query,
        [f"student-{index + 1}" for index in range(count)],
        [f"Student {index + 1}" for index in range(count)],
        ["" for _ in range(count)],
        ["7A" for _ in range(count)],
        np.asarray(matrix, dtype=np.float32),
        threshold,
        margin,
    )


def valid_decision(**changes):
    values = {
        "track_id": "track-000007",
        "bbox": (10, 10, 100, 100),
        "student_id": "student-1",
        "quality_passed": True,
        "anti_spoof_passed": True,
        "temporal_liveness_passed": True,
        "identity_matched": True,
    }
    values.update(changes)
    return FaceDecision(**values).finalize()


def test_good_cosine_similarity_selects_correct_student_id():
    result = gallery_match(
        normalized([1.0, 0.0, 0.0]),
        [normalized([0.0, 1.0, 0.0]), normalized([1.0, 0.0, 0.0])],
    )
    assert result["matched"] is True
    assert result["student_id"] == "student-2"
    assert result["score"] == 1.0


def test_enrollment_stores_multiple_normalized_embeddings(monkeypatch, tmp_path):
    student = student_repository.save_student("FSVFM Enrollment", "7A", "", None)
    vectors = iter(
        normalized(np.roll(np.arange(1, 513, dtype=np.float32), shift))
        for shift in range(3)
    )
    monkeypatch.setattr(student_service, "FACE_DIR", str(tmp_path))
    monkeypatch.setattr(student_service, "embedding_for", lambda _image: next(vectors))
    monkeypatch.setattr(student_service, "refresh_known_faces", lambda: None)
    monkeypatch.setattr("cv2.imwrite", lambda *_args, **_kwargs: True)

    result = student_service.add_face_samples(
        student["id"], [np.zeros((8, 8, 3), dtype=np.uint8) for _ in range(3)]
    )

    conn = get_connection()
    blobs = conn.execute(
        "SELECT embedding FROM face_embeddings WHERE student_id = ? ORDER BY id",
        (student["id"],),
    ).fetchall()
    conn.close()
    stored = [np.frombuffer(bytes(row[0]), dtype=np.float32) for row in blobs]
    assert result["face_samples"] == 3
    assert result["recognition_ready"] is True
    assert len(stored) == 3
    assert all(np.linalg.norm(vector) == pytest.approx(1.0) for vector in stored)


def test_identity_is_unknown_below_recognition_threshold():
    result = gallery_match(
        normalized([1.0, 0.0]),
        [normalized([0.0, 1.0])],
        threshold=0.6,
    )
    assert result["matched"] is False
    assert result["reason"] == RejectionReason.LOW_SIMILARITY.value


def test_identity_is_unknown_when_top1_top2_margin_is_too_small():
    result = gallery_match(
        normalized([1.0, 0.0]),
        [normalized([1.0, 0.0]), normalized([0.999, 0.02])],
        threshold=0.5,
        margin=0.05,
    )
    assert result["matched"] is False
    assert result["reason"] == RejectionReason.LOW_MARGIN.value
    assert result["margin"] < 0.05


def test_fsvfm_result_never_changes_arcface_student_id():
    match = gallery_match(
        normalized([1.0, 0.0]),
        [normalized([1.0, 0.0]), normalized([0.0, 1.0])],
    )
    fsvfm = FSVFMResult(0.01, False, 0.5, "fsvfm", 4.0)
    decision = valid_decision(
        student_id=match["student_id"],
        recognition_score=match["score"],
        recognition_margin=match["margin"],
        pad_live_score=fsvfm.live_score,
        pad_passed=fsvfm.predicted_live,
        pad_enforced=False,
    )
    assert decision.student_id == "student-1"
    assert decision.identity_matched is True


def test_shadow_mode_does_not_block_attendance_for_fsvfm_spoof():
    decision = valid_decision(pad_passed=False, pad_enforced=False)
    assert decision.attendance_allowed is True


def test_enforcement_mode_blocks_fsvfm_spoof():
    decision = valid_decision(pad_passed=False, pad_enforced=True)
    assert decision.attendance_allowed is False
    assert decision.rejection_reason == RejectionReason.PAD_FAILED.value


def test_track_id_and_student_id_are_distinct_entities():
    decision = valid_decision(track_id="track-000001", student_id="student-stable-id")
    assert decision.track_id != decision.student_id
    assert decision.student_id == "student-stable-id"


class PassingTemporalLiveness:
    def is_live(self, *_args):
        return True

    def remove(self, _track_id):
        pass


@pytest.mark.parametrize(
    ("shadow_mode", "attendance_expected"),
    [(True, True), (False, False)],
)
def test_camera_pipeline_keeps_identity_independent_from_fsvfm(
    monkeypatch, shadow_mode, attendance_expected
):
    face = SimpleNamespace(
        bbox=np.asarray([50, 50, 200, 220], dtype=np.float32),
        det_score=0.99,
        embedding=np.ones(512, dtype=np.float32),
        kps=np.asarray([[80, 90], [160, 90], [120, 125], [90, 170], [150, 170]]),
    )
    monkeypatch.setattr(
        camera_service,
        "detect_faces",
        lambda _frame: camera_service.DetectionBatch((face,), "fast", 1.0, False),
    )
    monkeypatch.setattr(camera_service, "load_known_faces", lambda: {
        "student_ids": ["student-identity"],
        "names": ["Student"],
        "parent_ids": [""],
        "classes": ["7A"],
        "embeddings": np.ones((1, 512), dtype=np.float32) / np.sqrt(512),
    })
    monkeypatch.setattr(
        camera_service,
        "evaluate_anti_spoof",
        lambda *_args: PadResult(passed=True, score=0.99),
    )
    monkeypatch.setattr(
        camera_service,
        "evaluate_fsvfm",
        lambda *_args: FSVFMResult(0.01, False, 0.5, "fsvfm", 2.0),
    )
    monkeypatch.setattr(camera_service, "FSVFM_ENABLED", True)
    monkeypatch.setattr(camera_service, "FSVFM_SHADOW_MODE", shadow_mode)
    registered = []
    monkeypatch.setattr(
        camera_service.attendance_service,
        "register_camera_event",
        lambda student_id, *_args, **_kwargs: (
            registered.append(student_id) or {"status": "created", "event": {}}
        ),
    )

    results = camera_service.recognize_frame(
        np.zeros((300, 300, 3), dtype=np.uint8),
        "keldi",
        PassingTemporalLiveness(),
        face_tracker=FaceTracker("keldi"),
    )

    decision = results[0].decision
    assert decision.pad_passed is False
    assert decision.track_id != "student-identity"
    assert decision.attendance_allowed is attendance_expected
    if shadow_mode:
        assert decision.student_id == "student-identity"
        assert decision.identity_matched is True
        assert registered == ["student-identity"]
    else:
        assert decision.student_id is None
        assert decision.identity_matched is False
        assert registered == []
