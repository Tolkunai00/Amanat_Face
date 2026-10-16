import pytest

from scripts.evaluate_biometrics import _write_pad_scores, evaluate_pad_records


def record(label, score, attack_type="", inference_ms=2.0):
    return {
        "file": f"{label}.jpg", "label": label, "attack_type": attack_type,
        "live_score": score, "predicted_class": "live" if score >= 0.5 else "print",
        "passed": score >= 0.5, "inference_ms": inference_ms,
    }


def test_pad_apcer_bpcer_and_attack_breakdown():
    records = [
        record("live", 0.9, inference_ms=1.0),
        record("live", 0.4, inference_ms=2.0),
        record("spoof", 0.8, "print", 3.0),
        record("spoof", 0.2, "print", 4.0),
        record("spoof", 0.1, "phone_video", 5.0),
    ]
    result = evaluate_pad_records(records, threshold=0.5)
    assert result["APCER"] == pytest.approx(1 / 3)
    assert result["BPCER"] == pytest.approx(1 / 2)
    assert result["ACER"] == pytest.approx((1 / 3 + 1 / 2) / 2)
    assert result["attack_breakdown"]["print"]["APCER"] == pytest.approx(1 / 2)
    assert result["attack_breakdown"]["phone_video"]["APCER"] == 0.0
    assert result["pad_inference_ms"]["average"] == 3.0
    assert result["pad_inference_ms"]["p50"] == 3.0


def test_pad_threshold_sweep_does_not_change_config():
    result = evaluate_pad_records(
        [record("live", 0.8), record("spoof", 0.2, "print")],
        threshold=0.5, sweep_step=0.5,
    )
    assert [row["threshold"] for row in result["threshold_sweep"]] == [0.0, 0.5, 1.0]
    assert "not a guaranteed production optimum" in result["threshold_warning"]


def test_pad_score_csv_contains_metadata_but_no_image_binary(tmp_path):
    path = tmp_path / "pad_scores.csv"
    sample = record("spoof", 0.2, "print", 4.0)
    sample["file"] = "spoof/print/sample.jpg"
    _write_pad_scores(path, [sample])
    content = path.read_text(encoding="utf-8")
    assert content.splitlines()[0] == (
        "file,label,attack_type,live_score,predicted_class,passed,inference_ms"
    )
    assert "spoof/print/sample.jpg" in content
