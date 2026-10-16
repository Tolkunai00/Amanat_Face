import csv
import json

import numpy as np
import pytest

from scripts.evaluate_fsvfm_fas import (
    calculate_summary, class_mapping, csv_row, stable_softmax, write_csv,
)
from scripts.fsvfm_fas_upstream import (
    CheckpointError, OFFICIAL_PROFILE, build_vit_small_fas, load_fas_checkpoint,
    preprocess_face,
)


def test_preprocessing_shape_rgb_and_normalization():
    # OpenCV BGR chosen so the RGB channels become mean*255 approximately.
    rgb = np.asarray(OFFICIAL_PROFILE.mean) * 255
    bgr = np.rint(rgb[::-1]).astype(np.uint8)
    image = np.broadcast_to(bgr, (100, 100, 3)).copy()
    tensor, crop = preprocess_face(image, (25, 25, 75, 75))
    assert tensor.shape == (1, 3, 224, 224)
    assert tensor.dtype == np.float32
    assert crop == (6, 6, 94, 94)
    assert np.max(np.abs(tensor)) < 0.01


def test_class_mapping_is_proven_by_official_training_and_evaluation_code():
    mapping = class_mapping()
    assert mapping["0"] == "spoof"
    assert mapping["1"] == "live"
    assert mapping["live_index"] == 1
    assert len(mapping["evidence"]) == 2


def test_probability_calculation():
    probabilities = stable_softmax([0.0, 1.0])
    assert probabilities.sum() == pytest.approx(1.0)
    assert probabilities[1] == pytest.approx(0.7310585786)


def test_metrics_without_and_with_explicit_threshold():
    records = [
        {"label": "live", "live_score": 0.9, "inference_ms": 10.0},
        {"label": "live", "live_score": 0.4, "inference_ms": 20.0},
        {"label": "spoof", "live_score": 0.6, "inference_ms": 30.0},
        {"label": "spoof", "live_score": 0.1, "inference_ms": 40.0},
    ]
    summary = calculate_summary(records)
    assert summary["mean_live_score_on_live"] == pytest.approx(0.65)
    assert summary["mean_live_score_on_spoof"] == pytest.approx(0.35)
    assert summary["score_separation"] == pytest.approx(0.30)
    assert "threshold_metrics" not in summary
    threshold = calculate_summary(records, 0.5)["threshold_metrics"]
    assert threshold == {
        "threshold": 0.5, "live_accepted": 1, "live_rejected": 1,
        "spoof_accepted": 1, "spoof_rejected": 1,
        "APCER": 0.5, "BPCER": 0.5, "ACER": 0.5,
    }


def test_csv_generation(tmp_path):
    record = {
        "file": "live/a.jpg", "label": "live", "live_score": 0.8,
        "predicted_class": "live", "probabilities": [0.2, 0.8],
        "inference_ms": 12.5,
    }
    assert list(csv_row(record)) == [
        "file", "label", "live_score", "predicted_class", "probabilities", "inference_ms",
    ]
    path = tmp_path / "report.csv"
    write_csv(path, [record])
    with path.open(encoding="utf-8") as source:
        row = next(csv.DictReader(source))
    assert row["file"] == "live/a.jpg"
    assert json.loads(row["probabilities"]) == [0.2, 0.8]


def test_checkpoint_loading_helpers_accept_exact_state_dict(tmp_path):
    import torch

    model = build_vit_small_fas()
    path = tmp_path / "fas.pth.tar"
    torch.save({"state_dict": model.state_dict(), "epoch": 1}, path)
    target = build_vit_small_fas()
    metadata = load_fas_checkpoint(target, path)
    assert metadata["strict_loading_result"] == "success"
    assert metadata["missing_keys"] == []
    assert metadata["unexpected_keys"] == []
    assert metadata["state_dict_summary"]["key_count"] > 100


def test_checkpoint_loading_rejects_generic_pretraining_checkpoint(tmp_path):
    import torch

    path = tmp_path / "generic.pth"
    torch.save({"model": {"pos_embed": torch.zeros(1)}}, path)
    with pytest.raises(CheckpointError, match="generic pre-training"):
        load_fas_checkpoint(build_vit_small_fas(), path)


def test_real_checkpoint_forward_if_configured():
    import os
    from pathlib import Path
    import torch

    value = os.environ.get("FSVFM_FAS_CHECKPOINT")
    if not value or not Path(value).is_file():
        pytest.skip("Set FSVFM_FAS_CHECKPOINT to an official fine-tuned checkpoint")
    model = build_vit_small_fas()
    load_fas_checkpoint(model, Path(value))
    with torch.inference_mode():
        logits = model.cls_logits(torch.zeros((1, 3, 224, 224)))
    assert logits.shape == (1, 2)
