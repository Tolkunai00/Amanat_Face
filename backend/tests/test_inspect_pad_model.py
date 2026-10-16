import numpy as np
import pytest

from scripts.inspect_pad_model import (
    InspectionError, analyze_candidate_indexes, interpret_three_output,
    make_csv_row, stable_softmax,
)


def record(label, probabilities, predicted_index=None):
    probabilities = list(probabilities)
    return {
        "file": f"{label}.jpg",
        "label": label,
        "raw": probabilities,
        "probabilities": probabilities,
        "predicted_index": (
            int(np.argmax(probabilities)) if predicted_index is None else predicted_index
        ),
        "inference_ms": 1.5,
    }


def test_auto_detects_probabilities_without_second_softmax():
    source = np.asarray([0.1, 0.7, 0.2])
    probabilities, interpretation = interpret_three_output(source, "auto")
    assert interpretation == "probabilities"
    assert probabilities == pytest.approx(source)


def test_auto_detects_logits_and_stable_softmax_handles_large_values():
    probabilities, interpretation = interpret_three_output([1000.0, 1001.0, 999.0], "auto")
    assert interpretation == "logits"
    assert np.isfinite(probabilities).all()
    assert sum(probabilities) == pytest.approx(1.0)
    assert int(np.argmax(probabilities)) == 1
    assert stable_softmax([1000.0, 1001.0, 999.0]) == pytest.approx(probabilities)


def test_output_must_contain_exactly_three_values():
    with pytest.raises(InspectionError, match="exactly 3"):
        interpret_three_output([0.1, 0.9], "auto")


def test_candidate_analysis_and_apcer_bpcer():
    records = [
        record("live", [0.1, 0.8, 0.1]),
        record("live", [0.2, 0.4, 0.4], predicted_index=2),
        record("spoof", [0.7, 0.2, 0.1]),
        record("spoof", [0.2, 0.6, 0.2]),
    ]
    analysis = analyze_candidate_indexes(records, threshold=0.5)
    candidate = analysis[1]
    assert candidate["live_samples"] == 2
    assert candidate["spoof_samples"] == 2
    assert candidate["live_correctly_dominant"] == 1
    assert candidate["spoof_rejected_by_dominance"] == 1
    assert candidate["live_accepted"] == 1
    assert candidate["live_rejected"] == 1
    assert candidate["spoof_accepted"] == 1
    assert candidate["spoof_rejected"] == 1
    assert candidate["APCER"] == 0.5
    assert candidate["BPCER"] == 0.5
    assert candidate["ACER"] == 0.5


def test_csv_row_generation_has_exact_columns_and_candidate_decision():
    source = record("live", [0.1, 0.85, 0.05])
    source["raw"] = [-1.0, 2.0, 0.0]
    row = make_csv_row(source, live_index=1, threshold=0.8)
    assert list(row) == [
        "file", "label", "raw_0", "raw_1", "raw_2",
        "prob_0", "prob_1", "prob_2", "predicted_index",
        "live_index", "live_score", "passed", "inference_ms",
    ]
    assert row["raw_1"] == 2.0
    assert row["prob_1"] == 0.85
    assert row["live_index"] == 1
    assert row["passed"] is True
