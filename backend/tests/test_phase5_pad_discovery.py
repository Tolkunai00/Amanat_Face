from pathlib import Path

import numpy as np
import pytest

from scripts import benchmark_pad_models
from scripts.fetch_pad_candidate import candidate, validate_download
from school_face_id.diagnostics.pad_providers import (
    ANTI_SPOOF_MN3_SHA256,
    ANTI_SPOOF_MN3_SIZE,
    AntiSpoofMN3DiagnosticProvider,
    PADProvider, PADProviderResult,
)


def test_anti_spoof_mn3_provider_contract_and_class_mapping():
    provider = AntiSpoofMN3DiagnosticProvider.__new__(AntiSpoofMN3DiagnosticProvider)
    provider.checkpoint = "pinned.onnx"
    provider.model_version = ANTI_SPOOF_MN3_SHA256
    provider.threshold = 0.5
    assert isinstance(provider, PADProvider)
    assert AntiSpoofMN3DiagnosticProvider.class_mapping == {"real": 0, "spoof": 1}


def test_provider_result_contract_is_unchanged():
    result = PADProviderResult("anti_spoof_mn3", "sha256", 0.9, True, 12.0, "")
    assert result.model_name == "anti_spoof_mn3"
    assert result.predicted_live is True


def test_anti_spoof_mn3_preprocessing_shape_and_color_mapping():
    # One BGR pixel value repeated; channel 0 after conversion is original red.
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    frame[:] = [10, 20, 30]
    tensor = AntiSpoofMN3DiagnosticProvider.preprocess(frame, (0, 0, 20, 20))
    assert tensor.shape == (1, 3, 128, 128)
    expected_red = (30.0 - 151.2405) / 63.0105
    assert tensor[0, 0, 0, 0] == pytest.approx(expected_red, abs=1e-5)


def test_unsupported_candidate_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        candidate("random-model")


def test_missing_checkpoint_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate_download(tmp_path / "missing.onnx", candidate("anti_spoof_mn3"))


def test_checkpoint_hash_validation_rejects_tampering(tmp_path):
    path = tmp_path / "candidate.onnx"
    path.write_bytes(b"not the official checkpoint")
    with pytest.raises(ValueError, match="mismatch"):
        validate_download(path, candidate("anti_spoof_mn3"))


def test_checkpoint_hash_validation_accepts_exact_metadata(tmp_path):
    import hashlib
    path = tmp_path / "candidate.onnx"
    raw = b"pinned test artifact"
    path.write_bytes(raw)
    result = validate_download(path, {
        "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "sha384": hashlib.sha384(raw).hexdigest(),
    })
    assert result["size"] == len(raw)


def test_pinned_checkpoint_metadata_is_stable():
    metadata = candidate("anti_spoof_mn3")
    assert metadata["sha256"] == ANTI_SPOOF_MN3_SHA256
    assert metadata["size"] == ANTI_SPOOF_MN3_SIZE
    assert metadata["license"] == "MIT"


def test_license_unknown_prevents_production_verdict():
    assert benchmark_pad_models.model_verdict(
        missing_attacks=[], license_status="UNKNOWN", screening_only=False,
        acceptable_attacks=True,
    ) == "RESEARCH_ONLY"


def test_historical_dataset_never_exceeds_screening_candidate():
    assert benchmark_pad_models.model_verdict(
        missing_attacks=[], license_status="COMMERCIAL_OK", screening_only=True,
        acceptable_attacks=True,
    ) == "SCREENING_CANDIDATE"


def test_calibration_sweep_does_not_write_production_threshold():
    root = Path(__file__).resolve().parents[2]
    before = (root / ".env.prod.example").read_bytes()
    sweep = benchmark_pad_models._best_calibration_point([
        {"threshold": 0.4, "APCER": 0.4, "BPCER": 0.2, "ACER": 0.3},
        {"threshold": 0.6, "APCER": 0.1, "BPCER": 0.3, "ACER": 0.2},
    ])
    assert sweep["threshold"] == 0.6
    assert (root / ".env.prod.example").read_bytes() == before


def test_same_evidence_groups_remains_a_hard_comparison_gate():
    class Provider:
        def __init__(self, name):
            self.model_name = name
            self.model_version = "v"
            self.threshold = 0.5
            self.checkpoint = "test"
            self.license_status = "UNKNOWN"
            self.input_specification = "test"

    base = {"file": "f", "split": "development", "label": "live", "person_id": "p",
            "camera_id": "c", "condition": "front", "attack_type": "", "live_score": 0.8,
            "predicted_live": True, "latency_ms": 1.0, "error": "", "model_version": "v"}
    records = [
        {**base, "model_name": "a", "evidence_group": "g1"},
        {**base, "model_name": "b", "evidence_group": "g2"},
    ]
    result = benchmark_pad_models.summarize(records, [Provider("a"), Provider("b")])
    assert result["same_evidence_groups"] is False
