import json
from pathlib import Path

import numpy as np
import pytest

from scripts import benchmark_pad_models, calibrate_recognition
from scripts.capture_pad_dataset import (
    ATTACK_TYPES, attack_capture_path, attack_metadata, live_capture_path,
    write_session_metadata,
)
from scripts.capture_recognition_dataset import capture_path
from scripts.create_recognition_manifest import build_manifest
from scripts.dataset_capture_common import save_unique_jpeg
from scripts.pad_dataset_audit import audit_dataset


def put(path, content=b"image"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_capture_paths_follow_protocol(tmp_path):
    assert capture_path(tmp_path, "person1", "gallery", "session1", "front") == tmp_path / "person1/gallery/session1/front"
    assert live_capture_path(tmp_path, "person1", "camera_1", "session2", "far") == tmp_path / "live/person1/camera_1/session2/far"
    assert attack_capture_path(tmp_path, "phone_video", "attack_1") == tmp_path / "spoof/phone_video/attack_1"
    with pytest.raises(ValueError):
        capture_path(tmp_path, "../escape", "probe", "session", "front")


def test_attack_session_metadata_preserves_unknowns_as_null(tmp_path):
    metadata = attack_metadata(
        attack_session_id="attack_1", attack_type="print", source_identity="person1",
        camera_id="camera_1", display_or_print_device="printer_1",
    )
    path = write_session_metadata(tmp_path / "attack_1", metadata)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["attack_session_id"] == "attack_1"
    assert saved["paper_type"] is None
    assert saved["print_size"] is None


def test_capture_rejects_exact_duplicate_without_deleting_source(tmp_path):
    frame = np.full((40, 40, 3), 127, dtype=np.uint8)
    known = {}
    first, digest, duplicate = save_unique_jpeg(tmp_path, frame, known, 1)
    second, second_digest, duplicate = save_unique_jpeg(tmp_path, frame, known, 2)
    assert first and first.exists()
    assert second is None and duplicate == first
    assert digest == second_digest
    assert len(list(tmp_path.glob("*.jpg"))) == 1


def test_gallery_probe_leakage_by_sha_session_and_burst(tmp_path):
    root = tmp_path / "recognition"
    put(root / "person1/gallery/same/front/a.jpg", b"same")
    put(root / "person1/probe/same/front/a (1).jpg", b"same")
    (root / "capture_metadata.csv").write_text(
        "file,camera_id,capture_time,burst_sequence_id\n"
        "person1/gallery/same/front/a.jpg,camera_1,2026-10-08T10:00:00Z,burst-x\n"
        "person1/probe/same/front/a (1).jpg,camera_2,2026-10-08T11:00:00Z,burst-x\n",
        encoding="utf-8",
    )
    _, report = build_manifest(root)
    codes = {row["code"] for row in report["protocol_violations"]}
    assert {"GALLERY_PROBE_SHA_LEAKAGE", "GALLERY_PROBE_SESSION_LEAKAGE", "GALLERY_PROBE_BURST_LEAKAGE"} <= codes
    assert report["status"] == "DATASET_PROTOCOL_FAIL"
    assert report["duplicates"] == 1


def _write_pad_session(root, attack, session, camera, device, source="person1"):
    folder = root / "spoof" / attack / session
    put(folder / "sample.jpg", f"{attack}-{session}-{camera}".encode())
    metadata = {
        "attack_session_id": session, "attack_type": attack,
        "source_identity": source, "camera_id": camera,
        "capture_time": "2026-10-08T10:00:00Z",
        "display_or_print_device": device, "notes": None,
    }
    (folder / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")


def test_attack_grouping_video_samples_share_one_evidence_group(tmp_path):
    folder = tmp_path / "spoof/phone_video/attack_1"
    put(folder / "one.jpg", b"one")
    put(folder / "two.jpg", b"two")
    (folder / "metadata.json").write_text(json.dumps({
        "attack_session_id": "attack_1", "attack_type": "phone_video",
        "source_identity": "person1", "camera_id": "camera_1",
        "capture_time": "2026-10-08T10:00:00Z",
        "display_or_print_device": "phone_1",
    }), encoding="utf-8")
    groups = {
        benchmark_pad_models.evidence_metadata(tmp_path, path)["evidence_group"]
        for path in folder.glob("*.jpg")
    }
    assert groups == {"unspecified/spoof/phone_video/attack_1"}
    assert audit_dataset(tmp_path)["attack_sessions"] == 1


def test_pad_audit_counts_devices_and_cross_camera_coverage(tmp_path):
    root = tmp_path / "pad"
    for camera in ("camera_1", "camera_2"):
        put(root / f"live/person1/{camera}/live_{camera}/front/sample.jpg", f"live-{camera}".encode())
        for attack in ATTACK_TYPES:
            device = "phone_1" if attack.startswith("phone_") else "laptop_1" if attack.startswith("laptop_") else "printer_1"
            _write_pad_session(root, attack, f"{attack}_{camera}", camera, device)
    report = audit_dataset(root)
    assert report["attack_sessions"] == 10
    assert report["devices"] == 3
    assert report["device_coverage"] == {"phones": 1, "laptops": 1, "printers": 1, "cameras": 2}
    assert report["CROSS_CAMERA_VALIDATED"] is True


def test_held_out_identity_refuses_missing_threshold_and_margin(tmp_path):
    with pytest.raises(SystemExit):
        calibrate_recognition.main([
            "--dataset", str(tmp_path), "--phase", "test",
            "--csv", str(tmp_path / "result.csv"), "--json", str(tmp_path / "result.json"),
        ])


def test_capture_helpers_leave_production_config_and_attendance_untouched(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    production = root / ".env.prod.example"
    before = production.read_bytes()
    called = []
    from school_face_id.services import attendance
    monkeypatch.setattr(attendance, "register_camera_event", lambda *a, **k: called.append(True))
    frame = np.full((20, 20, 3), 80, dtype=np.uint8)
    save_unique_jpeg(tmp_path, frame, {}, 1)
    assert called == []
    assert production.read_bytes() == before
