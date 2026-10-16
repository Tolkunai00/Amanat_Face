"""Calibrate the shadow-only FS-VFM diagnostic path on labeled local data.

No result from this command changes production configuration or enables FS-VFM.
Frames from one video/capture session are aggregated into one conservative
evidence group before APCER/BPCER are calculated.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import sys

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.biometric_calibration import (  # noqa: E402
    approximate_eer, filter_duplicates, pad_rates, pad_threshold_sweep,
)
from scripts.evaluate_fsvfm_fas import (  # noqa: E402
    ATTACK_TYPES, LIVE_CONDITIONS, build_vit_small_fas, detect_bbox, infer_one,
    load_fas_checkpoint, load_profile, preprocess_face,
)
from school_face_id.config.settings import FSVFM_THRESHOLD  # noqa: E402


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv"}
CSV_FIELDS = (
    "file", "label", "person_id", "condition", "attack_type",
    "live_score", "predicted_live", "error", "inference_ms",
)


def source_metadata(root: Path, path: Path):
    parts = path.relative_to(root).parts
    label = parts[0] if parts and parts[0] in {"live", "spoof"} else ""
    person_id = parts[1] if label == "live" and len(parts) > 1 else ""
    condition = parts[2] if label == "live" and len(parts) > 2 else ""
    attack_type = parts[1] if label == "spoof" and len(parts) > 1 else ""
    stem = re.sub(r"(?i)(?:[-_ ](?:frame|img|photo))?[-_ ]?\d+$", "", path.stem).casefold()
    if path.suffix.lower() in VIDEO_SUFFIXES:
        group_id = path.relative_to(root).as_posix()
    elif label == "live":
        group_id = f"live/{person_id}/{condition}/{stem}"
    else:
        group_id = f"spoof/{attack_type}/{stem}"
    return {
        "label": label, "person_id": person_id, "condition": condition,
        "attack_type": attack_type, "group_id": group_id,
    }


def iter_frames(path: Path, sample_seconds=1.0, max_frames=30):
    if path.suffix.lower() in IMAGE_SUFFIXES:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("image unreadable")
        yield path.name, image
        return
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError("video unreadable")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    stride = max(1, round(max(sample_seconds, 0.1) * fps)) if fps > 0 else 25
    index = emitted = 0
    try:
        while emitted < max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            if index % stride == 0:
                yield f"{path.name}#frame={index}", frame
                emitted += 1
            index += 1
    finally:
        capture.release()
    if emitted == 0:
        raise ValueError("video contains no readable frames")


def aggregate_groups(records):
    """Use worst-case group score so correlated frames count once."""
    groups = {}
    for record in records:
        groups.setdefault(record["group_id"], []).append(record)
    aggregated = []
    for group_id, rows in sorted(groups.items()):
        valid = [row for row in rows if not row["error"] and row["live_score"] is not None]
        base = rows[0]
        if not valid:
            aggregated.append({**base, "file": group_id, "live_score": None, "error": "no evaluable frame", "frame_count": len(rows)})
            continue
        # Worst security evidence: a spoof's highest accepted score and a
        # bona-fide group's lowest score.
        selected = (max if base["label"] == "spoof" else min)(valid, key=lambda row: row["live_score"])
        aggregated.append({**selected, "file": group_id, "frame_count": len(rows), "error": ""})
    return aggregated


def _breakdown(rows, field, expected, threshold, label):
    result = {}
    for value in expected:
        subset = [row for row in rows if row[field] == value]
        valid = [row for row in subset if not row["error"] and row["live_score"] is not None]
        if not subset:
            result[value] = {"status": "NOT_TESTED", "count": 0, "evaluated": 0}
            continue
        if not valid:
            result[value] = {"status": "EVALUATION_FAILED", "count": len(subset), "evaluated": 0}
            continue
        scores = [row["live_score"] for row in valid]
        if label == "spoof":
            rate = sum(score >= threshold for score in scores) / len(scores)
            result[value] = {"status": "EVALUATED", "count": len(subset), "evaluated": len(valid), "APCER": rate, "worst_live_score": max(scores)}
        else:
            rate = sum(score < threshold for score in scores) / len(scores)
            result[value] = {"status": "EVALUATED", "count": len(subset), "evaluated": len(valid), "BPCER": rate, "worst_live_score": min(scores)}
    return result


def summarize(records, duplicates, threshold, criteria=None):
    groups = aggregate_groups(records)
    valid = [row for row in groups if not row["error"] and row["live_score"] is not None]
    live_scores = [row["live_score"] for row in valid if row["label"] == "live"]
    spoof_scores = [row["live_score"] for row in valid if row["label"] == "spoof"]
    apcer, bpcer, acer = pad_rates(live_scores, spoof_scores, threshold)
    sweep = pad_threshold_sweep(live_scores, spoof_scores)
    eer, eer_threshold = approximate_eer(sweep, "APCER", "BPCER")
    attacks = _breakdown(groups, "attack_type", ATTACK_TYPES, threshold, "spoof")
    conditions = _breakdown(groups, "condition", LIVE_CONDITIONS, threshold, "live")
    failures = sum(bool(row["error"]) for row in records)
    failed_groups = sum(bool(row["error"]) for row in groups)
    failed_rate = failed_groups / len(groups) if groups else None
    required_attacks = ("phone_video", "laptop_video", "print")
    required_present = all(attacks[name]["status"] == "EVALUATED" for name in required_attacks)
    criteria = criteria or {}
    criteria_supplied = all(criteria.get(name) is not None for name in ("max_apcer", "max_bpcer", "max_failed_rate"))
    worst_apcer = max((value["APCER"] for value in attacks.values() if "APCER" in value), default=None)
    overlap = max(spoof_scores) >= min(live_scores) if spoof_scores and live_scores else None
    pad_validated = bool(
        criteria_supplied and required_present and worst_apcer is not None and bpcer is not None
        and worst_apcer <= criteria["max_apcer"] and bpcer <= criteria["max_bpcer"]
        and failed_rate <= criteria["max_failed_rate"]
    )
    photo_apcers = [attacks[name].get("APCER") for name in ("phone_photo", "laptop_photo")]
    enforcement_allowed = bool(
        pad_validated and all(value is not None and value <= criteria["max_apcer"] for value in photo_apcers)
    )
    return {
        "dataset_status": "EVALUATED" if records else "BLOCKED_DATASET",
        "raw_sample_count": duplicates["raw"], "unique_sample_count": duplicates["unique"],
        "duplicates_removed": duplicates["removed"], "duplicates": duplicates,
        "frames_processed": len(records), "evidence_groups": len(groups),
        "live_count": len(live_scores), "spoof_count": len(spoof_scores),
        "images_failed": failures, "failed_evidence_groups": failed_groups,
        "failed_detection_rate": failed_rate,
        "configured_diagnostic_threshold": threshold,
        "APCER": apcer, "BPCER": bpcer, "ACER": acer,
        "threshold_sweep": sweep, "eer_like": eer, "eer_like_threshold": eer_threshold,
        "score_overlap": overlap,
        "per_attack_type": attacks, "per_live_condition": conditions,
        "acceptance_criteria": {
            **criteria, "criteria_supplied": criteria_supplied,
            "required_attack_categories": list(required_attacks),
            "required_attacks_evaluated": required_present,
            "worst_attack_APCER": worst_apcer,
        },
        "PAD_VALIDATED": pad_validated,
        "FSVFM_ENFORCEMENT_ALLOWED": enforcement_allowed,
        "FSVFM_SHADOW_ONLY": True,
        "warning": "Measured candidates are not written to production configuration and do not enable enforcement.",
    }


def calibrate(dataset: Path, checkpoint: Path, threshold=FSVFM_THRESHOLD, device="cpu", sample_seconds=1.0, max_frames=30, criteria=None):
    sources = sorted(
        path for path in dataset.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES
    )
    unique_sources, duplicates = filter_duplicates(sources)
    import torch
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    model = build_vit_small_fas()
    load_fas_checkpoint(model, checkpoint, device)
    profile = load_profile(None)
    records = []
    for path in unique_sources:
        metadata = source_metadata(dataset, path)
        relative = path.relative_to(dataset).as_posix()
        try:
            for frame_name, frame in iter_frames(path, sample_seconds, max_frames):
                base = {**metadata, "file": f"{relative}#{frame_name}" if path.suffix.lower() in VIDEO_SUFFIXES else relative}
                try:
                    bbox = detect_bbox(frame)
                    tensor, _ = preprocess_face(frame, bbox, profile)
                    result = infer_one(model, tensor, device)
                    score = result["live_score"]
                    records.append({
                        **base, "live_score": score,
                        "predicted_live": score >= threshold,
                        "error": "", "inference_ms": result["inference_ms"],
                    })
                except Exception as exc:
                    records.append({**base, "live_score": None, "predicted_live": False, "error": f"{type(exc).__name__}: {exc}", "inference_ms": None})
        except Exception as exc:
            records.append({**metadata, "file": relative, "live_score": None, "predicted_live": False, "error": f"{type(exc).__name__}: {exc}", "inference_ms": None})
    return records, summarize(records, duplicates, threshold, criteria)


def write_csv(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main(argv=None):
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=project_root / "backend" / "pad_debug")
    parser.add_argument("--checkpoint", type=Path, default=project_root / "deploy" / "models" / "fsvfm_vit_s_oci2m_run0.pth.tar")
    parser.add_argument("--csv", type=Path, default=Path("pad_calibration.csv"))
    parser.add_argument("--json", type=Path, default=Path("pad_calibration.json"))
    parser.add_argument("--threshold", type=float, default=FSVFM_THRESHOLD)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--video-sample-seconds", type=float, default=1.0)
    parser.add_argument("--max-frames-per-video", type=int, default=30)
    parser.add_argument("--max-apcer", type=float)
    parser.add_argument("--max-bpcer", type=float)
    parser.add_argument("--max-failed-rate", type=float)
    args = parser.parse_args(argv)
    if not args.dataset.is_dir():
        print(json.dumps({"dataset_status": "BLOCKED_DATASET", "PAD_VALIDATED": False, "FSVFM_ENFORCEMENT_ALLOWED": False}, indent=2))
        return 2
    if not args.checkpoint.is_file():
        print(json.dumps({"dataset_status": "BLOCKED_MODEL", "PAD_VALIDATED": False, "FSVFM_ENFORCEMENT_ALLOWED": False}, indent=2))
        return 2
    if not 0 <= args.threshold <= 1 or args.video_sample_seconds <= 0 or args.max_frames_per_video <= 0:
        parser.error("threshold/frame sampling values are out of range")
    criteria = {"max_apcer": args.max_apcer, "max_bpcer": args.max_bpcer, "max_failed_rate": args.max_failed_rate}
    records, summary = calibrate(
        args.dataset, args.checkpoint, args.threshold, args.device,
        args.video_sample_seconds, args.max_frames_per_video, criteria,
    )
    write_csv(args.csv, records)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["frames_processed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
