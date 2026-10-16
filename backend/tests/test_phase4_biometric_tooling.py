import json
from pathlib import Path

import numpy as np
import pytest

from scripts import benchmark_pad_models, calibrate_recognition
from scripts.create_recognition_manifest import build_manifest
from scripts.pad_dataset_audit import audit_dataset
from school_face_id.diagnostics.pad_providers import PADProvider, PADProviderResult


def image(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_gallery_probe_session_leakage_detected(tmp_path):
    root = tmp_path / "recognition"
    image(root / "person001/gallery/session01/front/g.jpg", b"gallery")
    image(root / "person001/probe/session01/front/p.jpg", b"probe")
    _, report = build_manifest(root)
    assert "GALLERY_PROBE_SESSION_LEAKAGE" in {row["code"] for row in report["protocol_violations"]}


def test_gallery_probe_sha_duplicate_leakage_detected(tmp_path):
    root = tmp_path / "recognition"
    image(root / "person001/gallery/session01/front/g.jpg", b"same")
    image(root / "person001/probe/session02/front/p.jpg", b"same")
    _, report = build_manifest(root)
    assert "GALLERY_PROBE_SHA_LEAKAGE" in {row["code"] for row in report["protocol_violations"]}


def test_recognition_session_split_counts_are_operational_not_sufficiency(tmp_path):
    root = tmp_path / "recognition"
    image(root / "person001/gallery/session01/front/g.jpg", b"g")
    image(root / "person001/probe/session02/left/p.jpg", b"p")
    _, report = build_manifest(root)
    assert report["gallery_sessions"] == 1
    assert report["probe_sessions"] == 1
    assert report["IDENTITY_CALIBRATED"] is False
    assert report["per_person"]["person001"]["conditions"] == ["front", "left"]


def test_pad_video_frames_and_attack_session_aggregate_once():
    rows = [
        {"model_name": "fake", "model_version": "v", "file": f"video#{i}",
         "evidence_group": "spoof/phone_video/session1", "split": "development",
         "label": "spoof", "person_id": "p", "camera_id": "c", "condition": "",
         "attack_type": "phone_video", "live_score": score,
         "predicted_live": score >= 0.5, "latency_ms": 1.0, "error": ""}
        for i, score in enumerate((0.1, 0.8, 0.3))
    ]
    grouped = benchmark_pad_models.aggregate_model_groups(rows)
    assert len(grouped) == 1
    assert grouped[0]["live_score"] == 0.8  # worst spoof frame


def write_attack_session(root, attack, session, metadata, filename="sample.jpg"):
    folder = root / "development/spoof" / attack / session
    image(folder / filename, f"{attack}-{session}".encode())
    (folder / "session.json").write_text(json.dumps(metadata), encoding="utf-8")


def session_metadata(session="s1"):
    return {
        "attack_session_id": session, "source_identity": "person1",
        "display_or_print_device": "device1", "camera_id": "camera1",
        "capture_session": "capture1",
    }


def test_pad_attack_session_audit_and_missing_category(tmp_path):
    root = tmp_path / "pad"
    write_attack_session(root, "phone_photo", "s1", session_metadata("s1"))
    report = audit_dataset(root)
    assert report["attack_coverage"]["phone_photo"]["sessions"] == 1
    assert report["attack_coverage"]["phone_photo"]["unique_devices"] == 1
    assert report["attack_coverage"]["print"]["status"] == "NOT_TESTED"
    assert report["PAD_DATASET_COMPLETE"] is False


def test_generic_pad_provider_contract():
    class Provider:
        model_name = "third_party_test"
        model_version = "sha256"
        threshold = 0.5
        checkpoint = "/private/model"
        license_status = "test only"
        input_specification = "synthetic"

        def predict(self, frame, bbox):
            return PADProviderResult(self.model_name, self.model_version, 0.2, False, 1.0, "")

    provider = Provider()
    assert isinstance(provider, PADProvider)
    assert provider.predict(None, None).model_name == "third_party_test"


def test_common_benchmark_compares_identical_groups():
    class Provider:
        def __init__(self, name):
            self.model_name = name; self.model_version = "v"; self.threshold = 0.5
            self.checkpoint = "test"; self.license_status = "test"; self.input_specification = "test"

    base = {
        "file": "x", "evidence_group": "group1", "split": "development",
        "label": "live", "person_id": "p", "camera_id": "c", "condition": "front",
        "attack_type": "", "live_score": 0.9, "predicted_live": True,
        "latency_ms": 1.0, "error": "", "model_version": "v",
    }
    records = [{**base, "model_name": name} for name in ("a", "b")]
    report = benchmark_pad_models.summarize(records, [Provider("a"), Provider("b")])
    assert report["same_evidence_groups"] is True
    assert report["BEST_PAD_CANDIDATE"] == "NONE"


def test_held_out_test_requires_preselected_threshold(tmp_path):
    with pytest.raises(SystemExit):
        benchmark_pad_models.main([
            "--images", str(tmp_path), "--models", "minifas", "--phase", "test",
            "--report", str(tmp_path / "r.json"), "--csv", str(tmp_path / "r.csv"),
        ])


def test_test_phase_does_not_select_recognition_threshold(tmp_path, monkeypatch):
    root = tmp_path / "recognition"
    files = [
        image(root / "p1/gallery/s1/front/g1.jpg", b"g1"),
        image(root / "p1/probe/s2/front/p1.jpg", b"p1"),
        image(root / "p2/gallery/s1/front/g2.jpg", b"g2"),
        image(root / "p2/probe/s2/front/p2.jpg", b"p2"),
    ]
    vectors = {"g1.jpg": [1, 0], "p1.jpg": [1, 0], "g2.jpg": [0, 1], "p2.jpg": [0, 1]}
    monkeypatch.setattr(calibrate_recognition, "_embed", lambda path: np.asarray(vectors[path.name], dtype=np.float32))
    _, summary = calibrate_recognition.calibrate(root, 0.5, 0.1, phase="test")
    assert summary["threshold_selection_allowed"] is False
    assert summary["recommended_candidates"] == []


def test_phase4_does_not_change_production_thresholds():
    root = Path(__file__).resolve().parents[2]
    env = (root / ".env.prod.example").read_text(encoding="utf-8")
    assert "RECOGNITION_THRESHOLD=0.52" in env
    assert "RECOGNITION_MARGIN=0.06" in env
    assert "FSVFM_SHADOW_MODE=true" in env
    assert "FSVFM_THRESHOLD=0.50" in env
