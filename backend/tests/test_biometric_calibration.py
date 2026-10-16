from pathlib import Path

import numpy as np
import pytest

from scripts.biometric_calibration import (
    approximate_eer, error_rates, filter_duplicates, identity_threshold_sweep,
    pad_rates,
)
from scripts import calibrate_pad, calibrate_recognition


def test_genuine_and_impostor_labeling(tmp_path, monkeypatch):
    dataset = tmp_path / "recognition_validation"
    files = {
        "person1/gallery/g1.jpg": b"g1",
        "person1/probe/p1.jpg": b"p1",
        "person2/gallery/g2.jpg": b"g2",
        "person2/probe/p2.jpg": b"p2",
    }
    for name, value in files.items():
        path = dataset / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    vectors = {
        "g1.jpg": np.array([1.0, 0.0], dtype=np.float32),
        "p1.jpg": np.array([0.9, 0.1], dtype=np.float32) / np.linalg.norm([0.9, 0.1]),
        "g2.jpg": np.array([0.0, 1.0], dtype=np.float32),
        # Deliberately resembles person1, producing an impostor top-1 label.
        "p2.jpg": np.array([0.8, 0.2], dtype=np.float32) / np.linalg.norm([0.8, 0.2]),
    }
    monkeypatch.setattr(calibrate_recognition, "_embed", lambda path: vectors[path.name])
    records, summary = calibrate_recognition.calibrate(dataset, threshold=0.5, margin=0.0)
    labels = {row["expected_person_id"]: row["label"] for row in records}
    assert labels == {"person1": "genuine", "person2": "impostor"}
    assert summary["genuine"]["count"] == 2
    assert summary["impostor"]["count"] == 2


def test_threshold_sweep_far_frr_and_eer():
    genuine = [0.9, 0.7]
    impostor = [0.8, 0.1]
    far, frr = error_rates(genuine, impostor, 0.75)
    assert far == pytest.approx(0.5)
    assert frr == pytest.approx(0.5)
    rows = identity_threshold_sweep(genuine, impostor, thresholds=[0.0, 0.75, 1.0])
    assert rows[0]["FAR"] == 1.0 and rows[0]["FRR"] == 0.0
    assert rows[1]["FAR"] == pytest.approx(0.5)
    assert rows[1]["FRR"] == pytest.approx(0.5)
    eer, threshold = approximate_eer(rows)
    assert eer == pytest.approx(0.5)
    assert threshold == pytest.approx(0.75)


def test_apcer_bpcer_and_acer():
    apcer, bpcer, acer = pad_rates([0.9, 0.4], [0.8, 0.2, 0.1], 0.5)
    assert apcer == pytest.approx(1 / 3)
    assert bpcer == pytest.approx(1 / 2)
    assert acer == pytest.approx((1 / 3 + 1 / 2) / 2)


def test_duplicate_filtering_exact_filename_and_copy_suffix(tmp_path):
    paths = []
    for name, content in (
        ("a/original.jpg", b"same"),
        ("b/exact.jpg", b"same"),
        ("c/photo.jpg", b"first"),
        ("d/photo (1).jpg", b"different-copy"),
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        paths.append(path)
    unique, report = filter_duplicates(paths)
    assert len(unique) == 2
    assert report["raw"] == 4
    assert report["removed"] == 2
    assert {row["reason"] for row in report["removed_files"]} == {"exact_sha256", "filename_copy"}


def test_gallery_probe_split_never_self_matches(tmp_path):
    dataset = tmp_path / "recognition_validation"
    gallery = dataset / "person1/gallery/a.jpg"
    probe = dataset / "person1/probe/a2.jpg"
    for path in (gallery, probe):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    galleries, probes, _, _ = calibrate_recognition.split_gallery_probe(dataset, [gallery, probe])
    gallery_resolved = {path.resolve() for values in galleries.values() for path in values}
    probe_resolved = {path.resolve() for _, path, _ in probes}
    assert gallery_resolved.isdisjoint(probe_resolved)


def test_missing_attack_category_is_not_tested():
    records = [
        {"file": "live", "group_id": "live/1", "label": "live", "person_id": "p1", "condition": "front", "attack_type": "", "live_score": 0.9, "error": ""},
        {"file": "phone", "group_id": "spoof/phone", "label": "spoof", "person_id": "", "condition": "", "attack_type": "phone_photo", "live_score": 0.2, "error": ""},
    ]
    duplicates = {"raw": 2, "unique": 2, "removed": 0, "removed_files": []}
    summary = calibrate_pad.summarize(records, duplicates, 0.5)
    assert summary["per_attack_type"]["print"]["status"] == "NOT_TESTED"
    assert summary["per_attack_type"]["laptop_video"]["status"] == "NOT_TESTED"
    assert summary["PAD_VALIDATED"] is False
    assert summary["FSVFM_ENFORCEMENT_ALLOWED"] is False


def test_video_frames_are_one_failure_evidence_group():
    records = [
        {"file": f"video#{index}", "group_id": "spoof/video-session", "label": "spoof", "person_id": "", "condition": "", "attack_type": "phone_video", "live_score": None, "error": "no face"}
        for index in range(5)
    ]
    duplicates = {"raw": 1, "unique": 1, "removed": 0, "removed_files": []}
    summary = calibrate_pad.summarize(records, duplicates, 0.5)
    assert summary["evidence_groups"] == 1
    assert summary["failed_evidence_groups"] == 1
    assert summary["failed_detection_rate"] == 1.0


def test_calibration_does_not_write_production_threshold(tmp_path, monkeypatch):
    production = Path(__file__).resolve().parents[2] / ".env.prod.example"
    before = production.read_bytes()
    dataset = tmp_path / "recognition_validation"
    for name, value in (("p/gallery/g.jpg", b"g"), ("p/probe/q.jpg", b"q")):
        path = dataset / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    monkeypatch.setattr(calibrate_recognition, "_embed", lambda _path: np.array([1.0, 0.0], dtype=np.float32))
    calibrate_recognition.calibrate(dataset, threshold=0.123, margin=0.045)
    assert production.read_bytes() == before
