"""Offline recognition, PAD and pipeline performance evaluation.

The script is read-only: it never changes thresholds, embeddings or attendance.
Sections without sufficient local data return an ``available: false`` result.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from school_face_id.config.settings import (  # noqa: E402
    ANTI_SPOOFING_THRESHOLD, RECOGNITION_MARGIN, RECOGNITION_THRESHOLD,
)
from school_face_id.recognition.anti_spoof import evaluate_anti_spoof  # noqa: E402
from school_face_id.recognition.camera_service import detect_faces  # noqa: E402
from school_face_id.recognition.embedding import embedding_from_face  # noqa: E402
from school_face_id.recognition.matcher import match_identity_details  # noqa: E402
from school_face_id.recognition.tracker import FaceTracker  # noqa: E402
from school_face_id.repositories.recognition import load_recognition_embeddings  # noqa: E402


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "data" / "biometric_eval"


def _distribution(values):
    if not values:
        return None
    array = np.asarray(values, dtype=np.float64)
    return {
        "min": float(np.min(array)), "max": float(np.max(array)),
        "mean": float(np.mean(array)), "median": float(np.median(array)),
        "p05": float(np.percentile(array, 5)), "p95": float(np.percentile(array, 95)),
    }


def recognition_metrics(threshold=RECOGNITION_THRESHOLD, margin=RECOGNITION_MARGIN, target_far=None):
    try:
        student_ids, _, _, embeddings, _ = load_recognition_embeddings()
    except Exception as exc:
        return {"available": False, "reason": f"Cannot load embeddings: {type(exc).__name__}"}
    if len(embeddings) < 2:
        return {"available": False, "reason": "At least two embeddings are required"}
    matrix = np.asarray(embeddings, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    valid = np.isfinite(matrix).all(axis=1) & (norms[:, 0] > 1e-6)
    matrix, ids = matrix[valid] / norms[valid], np.asarray(student_ids)[valid]
    genuine, impostor = [], []
    for left in range(len(matrix)):
        for right in range(left + 1, len(matrix)):
            score = float(matrix[left] @ matrix[right])
            (genuine if ids[left] == ids[right] else impostor).append(score)
    if not genuine or not impostor:
        return {
            "available": False,
            "reason": "Need repeated samples and at least two distinct people",
            "genuine_comparisons": len(genuine), "impostor_comparisons": len(impostor),
        }
    far = sum(score >= threshold for score in impostor) / len(impostor)
    frr = sum(score < threshold for score in genuine) / len(genuine)
    result = {
        "available": True, "threshold": threshold, "margin": margin,
        "genuine_comparisons": len(genuine), "impostor_comparisons": len(impostor),
        "FAR": far, "FRR": frr, "TPR": 1.0 - frr, "FPR": far,
        "genuine_scores": _distribution(genuine), "impostor_scores": _distribution(impostor),
        "warning": "Pair metrics are not a substitute for a separate camera validation set.",
    }
    if target_far is not None:
        candidates = sorted(set(genuine + impostor))
        eligible = [
            value for value in candidates
            if sum(score >= value for score in impostor) / len(impostor) <= target_far
        ]
        result["target_far"] = target_far
        result["threshold_at_target_far"] = min(
            eligible,
            key=lambda value: sum(score < value for score in genuine),
        ) if eligible else None
    return result


def _images(root: Path):
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*") if path.suffix.lower() in IMAGE_SUFFIXES)


def evaluate_pad_records(records, threshold=ANTI_SPOOFING_THRESHOLD, sweep_step=None):
    live = [record for record in records if record["label"] == "live"]
    spoof = [record for record in records if record["label"] == "spoof"]
    if not live or not spoof:
        return {
            "available": False,
            "reason": "Both evaluable live and spoof samples are required",
            "live_total": len(live), "spoof_total": len(spoof),
        }

    def accepted(record, value=threshold):
        return record["live_score"] >= value

    live_accepted = sum(accepted(record) for record in live)
    spoof_accepted = sum(accepted(record) for record in spoof)
    live_rejected = len(live) - live_accepted
    spoof_rejected = len(spoof) - spoof_accepted
    apcer = spoof_accepted / len(spoof)
    bpcer = live_rejected / len(live)
    timings = [record["inference_ms"] for record in records if record["inference_ms"] is not None]
    attack_breakdown = {}
    for attack_type in sorted({record["attack_type"] for record in spoof}):
        attack_records = [record for record in spoof if record["attack_type"] == attack_type]
        attack_accepted = sum(accepted(record) for record in attack_records)
        attack_apcer = attack_accepted / len(attack_records)
        attack_breakdown[attack_type] = {
            "total": len(attack_records),
            "spoof_accepted": attack_accepted,
            "spoof_rejected": len(attack_records) - attack_accepted,
            "APCER": attack_apcer,
            "ACER_with_overall_BPCER": (attack_apcer + bpcer) / 2.0,
        }
    result = {
        "available": True, "threshold": threshold,
        "live_total": len(live), "spoof_total": len(spoof),
        "true_live_accepted": live_accepted,
        "live_accepted": live_accepted, "live_rejected": live_rejected,
        "spoof_accepted": spoof_accepted, "spoof_rejected": spoof_rejected,
        "confusion_matrix": {
            "actual_live": {"accepted": live_accepted, "rejected": live_rejected},
            "actual_spoof": {"accepted": spoof_accepted, "rejected": spoof_rejected},
        },
        "APCER": apcer, "BPCER": bpcer, "ACER": (apcer + bpcer) / 2.0,
        "attack_breakdown": attack_breakdown,
        "pad_inference_ms": {
            "average": float(np.mean(timings)) if timings else None,
            "p50": float(np.percentile(timings, 50)) if timings else None,
            "p95": float(np.percentile(timings, 95)) if timings else None,
        },
    }
    if sweep_step is not None:
        if not 0 < sweep_step <= 1:
            raise ValueError("PAD threshold sweep step must be in the (0, 1] range")
        sweep = []
        for candidate in np.arange(0.0, 1.0 + sweep_step / 2.0, sweep_step):
            candidate = min(1.0, float(candidate))
            candidate_apcer = sum(accepted(record, candidate) for record in spoof) / len(spoof)
            candidate_bpcer = sum(not accepted(record, candidate) for record in live) / len(live)
            sweep.append({
                "threshold": candidate, "APCER": candidate_apcer,
                "BPCER": candidate_bpcer,
                "ACER": (candidate_apcer + candidate_bpcer) / 2.0,
            })
        optimum = min(sweep, key=lambda row: (row["ACER"], row["APCER"], row["BPCER"]))
        result["threshold_sweep"] = sweep
        result["evaluation_minimum_ACER"] = optimum
        result["threshold_warning"] = (
            "Evaluation optimum is not a guaranteed production optimum and is not written to config."
        )
    return result


def _write_pad_scores(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "file", "label", "attack_type", "live_score",
        "predicted_class", "passed", "inference_ms",
    ]
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: record.get(field, "") for field in fields} for record in records)


def pad_metrics(
    dataset: Path, threshold=ANTI_SPOOFING_THRESHOLD, csv_path=None, sweep_step=None,
):
    live_paths, spoof_paths = _images(dataset / "live"), _images(dataset / "spoof")
    if not live_paths or not spoof_paths:
        return {
            "available": False,
            "reason": "Both live/ and spoof/ labeled image sets are required",
            "live_total": len(live_paths), "spoof_total": len(spoof_paths),
        }
    records = []
    skipped = 0
    for label, paths in (("live", live_paths), ("spoof", spoof_paths)):
        for path in paths:
            image = cv2.imread(str(path))
            if image is None:
                skipped += 1
                continue
            detection = detect_faces(image)
            if len(detection.faces) != 1:
                skipped += 1
                continue
            box = tuple(detection.faces[0].bbox.astype(int))
            pad = evaluate_anti_spoof(image, box)
            if pad.bypassed:
                return {"available": False, "reason": "PAD model is not configured; bypass is not a prediction"}
            if pad.live_score is None:
                skipped += 1
                continue
            relative = path.relative_to(dataset).as_posix()
            attack_type = path.relative_to(dataset / "spoof").parts[0] if label == "spoof" else ""
            records.append({
                "file": relative, "label": label, "attack_type": attack_type,
                "live_score": pad.live_score,
                "predicted_class": pad.predicted_class,
                "passed": pad.live_score >= threshold,
                "inference_ms": pad.inference_ms,
            })
    if csv_path is not None:
        _write_pad_scores(Path(csv_path), records)
    result = evaluate_pad_records(records, threshold, sweep_step)
    result["skipped"] = skipped
    if csv_path is not None:
        result["scores_csv"] = str(csv_path)
    return result


def performance_metrics(dataset: Path, limit=50):
    paths = (_images(dataset / "live") + _images(dataset / "spoof"))[: max(1, limit)]
    if not paths:
        return {"available": False, "reason": "No local evaluation images found"}
    timings = {name: [] for name in ("detection", "tracking", "PAD", "embedding", "matching", "total")}
    tracker = FaceTracker("benchmark")
    try:
        student_ids, names, parent_ids, matrix, classes = load_recognition_embeddings()
        matrix = np.asarray(matrix, dtype=np.float32)
        if len(matrix):
            matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-6)
    except Exception:
        student_ids, names, parent_ids, classes, matrix = [], [], [], [], np.empty((0, 512), dtype=np.float32)
    skipped = 0
    for path in paths:
        frame = cv2.imread(str(path))
        if frame is None:
            skipped += 1
            continue
        total_started = time.perf_counter()
        started = time.perf_counter(); detection = detect_faces(frame); timings["detection"].append(time.perf_counter() - started)
        if not detection.faces:
            skipped += 1
            continue
        face = detection.faces[0]; box = tuple(face.bbox.astype(int))
        started = time.perf_counter(); tracker.update([box], time.monotonic()); timings["tracking"].append(time.perf_counter() - started)
        started = time.perf_counter(); evaluate_anti_spoof(frame, box); timings["PAD"].append(time.perf_counter() - started)
        started = time.perf_counter(); embedding = embedding_from_face(face); timings["embedding"].append(time.perf_counter() - started)
        started = time.perf_counter()
        match_identity_details(embedding, student_ids, names, parent_ids, classes, matrix, RECOGNITION_THRESHOLD, RECOGNITION_MARGIN)
        timings["matching"].append(time.perf_counter() - started)
        timings["total"].append(time.perf_counter() - total_started)
    if not timings["total"]:
        return {"available": False, "reason": "No face could be evaluated", "skipped": skipped}
    latency_ms = {key: float(np.mean(values) * 1000.0) if values else None for key, values in timings.items()}
    return {
        "available": True, "evaluated": len(timings["total"]), "skipped": skipped,
        "latency_ms_mean": latency_ms,
        "approximate_FPS": 1.0 / float(np.mean(timings["total"])),
        "warning": "Offline sequential FPS is not multi-camera capacity.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--threshold", type=float, default=RECOGNITION_THRESHOLD)
    parser.add_argument("--margin", type=float, default=RECOGNITION_MARGIN)
    parser.add_argument("--target-far", type=float)
    parser.add_argument("--performance-limit", type=int, default=50)
    parser.add_argument("--pad-threshold", type=float, default=ANTI_SPOOFING_THRESHOLD)
    parser.add_argument("--pad-scores-csv", type=Path)
    parser.add_argument("--pad-threshold-sweep-step", type=float)
    args = parser.parse_args()
    report = {
        "recognition": recognition_metrics(args.threshold, args.margin, args.target_far),
        "anti_spoof": pad_metrics(
            args.dataset, args.pad_threshold, args.pad_scores_csv,
            args.pad_threshold_sweep_step,
        ),
        "performance": performance_metrics(args.dataset, args.performance_limit),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
