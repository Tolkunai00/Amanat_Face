"""Diagnostic A/B benchmark for bbox and landmark-aligned FS-VFM preprocessing.

This script is isolated from the production PAD pipeline.  Its thresholds are
reporting cutoffs only and are never read from or written to production config.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import statistics
import sys

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate_fsvfm_fas import (  # noqa: E402
    EvaluationError,
    infer_one,
    iter_images,
    parse_image_metadata,
)
from fsvfm_fas_upstream import (  # noqa: E402
    build_vit_small_fas,
    file_sha256,
    load_fas_checkpoint,
    preprocess_face,
)
from fsvfm_fas_upstream.preprocess import load_profile  # noqa: E402


ALIGNMENT_PADDING = 0.37
DIAGNOSTIC_THRESHOLDS = (0.3, 0.5, 0.7)
REPORT_GROUPS = ("live", "phone_photo", "laptop_photo")
CSV_FIELDS = (
    "file",
    "label",
    "person_id",
    "condition",
    "attack_type",
    "bbox_live_score",
    "aligned_live_score",
    "delta",
)

# Standard five-point face template used by MTCNN/ArcFace-style alignment.
# InsightFace kps uses the same order: left eye, right eye, nose, left mouth,
# right mouth.  Coordinates are normalized from the canonical 112-pixel chip.
CANONICAL_FIVE_POINTS = np.asarray(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
) / np.float32(112.0)


def detect_face_geometry(image):
    """Detect exactly one face and return its bbox and five landmarks."""
    from school_face_id.recognition.camera_service import detect_faces

    detection = detect_faces(image)
    if len(detection.faces) != 1:
        raise EvaluationError(f"expected one detected face, found {len(detection.faces)}")
    face = detection.faces[0]
    bbox = np.asarray(face.bbox, dtype=np.float32).reshape(-1)
    landmarks = np.asarray(getattr(face, "kps", []), dtype=np.float32)
    if bbox.size != 4 or not np.isfinite(bbox).all():
        raise EvaluationError("detector returned an invalid bbox")
    if landmarks.shape != (5, 2) or not np.isfinite(landmarks).all():
        raise EvaluationError("detector did not return five finite landmarks")
    return tuple(float(value) for value in bbox), landmarks


def padded_template(output_size, padding=ALIGNMENT_PADDING):
    """Scale the canonical geometry inward to leave official-style padding."""
    if output_size <= 0 or padding < 0:
        raise ValueError("output_size must be positive and padding must be non-negative")
    normalized = (CANONICAL_FIVE_POINTS + padding) / (1.0 + 2.0 * padding)
    return normalized * np.float32(output_size)


def aligned_face_chip(image, landmarks, output_size=224, padding=ALIGNMENT_PADDING):
    """Create a five-landmark similarity-aligned BGR face chip."""
    source = np.asarray(landmarks, dtype=np.float32)
    if source.shape != (5, 2) or not np.isfinite(source).all():
        raise EvaluationError("expected five finite landmarks")
    target = padded_template(output_size, padding)
    matrix, _ = cv2.estimateAffinePartial2D(source, target, method=cv2.LMEDS)
    if matrix is None or matrix.shape != (2, 3) or not np.isfinite(matrix).all():
        raise EvaluationError("could not estimate landmark alignment transform")
    chip = cv2.warpAffine(
        image,
        matrix,
        (output_size, output_size),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    return chip, matrix


def preprocess_aligned_chip(chip_bgr, profile):
    """Apply the same post-crop transforms as the bbox FS-VFM branch."""
    rgb = cv2.cvtColor(chip_bgr, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb.astype(np.uint8)).resize(
        (profile.input_size, profile.input_size), resample=Image.Resampling.BICUBIC
    )
    array = np.asarray(pil, dtype=np.float32) / np.float32(255.0)
    array = (array - np.asarray(profile.mean, dtype=np.float32)) / np.asarray(
        profile.std, dtype=np.float32
    )
    return np.ascontiguousarray(array.transpose(2, 0, 1)[None, ...])


def benchmark_images(model, images_root, profile, device):
    records = []
    for _label, path in iter_images(images_root):
        record = parse_image_metadata(images_root, path)
        try:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise EvaluationError("image unreadable")
            bbox, landmarks = detect_face_geometry(image)
            bbox_tensor, bbox_crop = preprocess_face(image, bbox, profile)
            chip, transform = aligned_face_chip(
                image, landmarks, profile.input_size, ALIGNMENT_PADDING
            )
            aligned_tensor = preprocess_aligned_chip(chip, profile)
            bbox_result = infer_one(model, bbox_tensor, device)
            aligned_result = infer_one(model, aligned_tensor, device)
            record.update(
                {
                    "bbox": list(bbox),
                    "landmarks": landmarks.tolist(),
                    "bbox_crop": list(bbox_crop),
                    "alignment_transform": transform.tolist(),
                    "bbox_live_score": bbox_result["live_score"],
                    "aligned_live_score": aligned_result["live_score"],
                    "delta": aligned_result["live_score"] - bbox_result["live_score"],
                }
            )
            print(json.dumps(record, ensure_ascii=False))
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            print(json.dumps(record, ensure_ascii=False), file=sys.stderr)
        records.append(record)
    return records


def record_group(record):
    if record["label"] == "live":
        return "live"
    return record.get("attack_type")


def score_statistics(values):
    return {
        "mean": statistics.fmean(values) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def group_summary(records, group):
    rows = [record for record in records if record_group(record) == group]
    valid = [record for record in rows if "error" not in record]
    return {
        "count": len(rows),
        "evaluated": len(valid),
        "failed": len(rows) - len(valid),
        "bbox_live_score": score_statistics(
            [record["bbox_live_score"] for record in valid]
        ),
        "aligned_live_score": score_statistics(
            [record["aligned_live_score"] for record in valid]
        ),
        "delta": score_statistics([record["delta"] for record in valid]),
    }


def error_rates(records, score_field, threshold):
    live_scores = [
        record[score_field]
        for record in records
        if "error" not in record and record["label"] == "live"
    ]
    result = {
        "BPCER": (
            sum(score < threshold for score in live_scores) / len(live_scores)
            if live_scores
            else None
        ),
        "live_samples": len(live_scores),
        "APCER": {},
    }
    combined = []
    for attack_type in REPORT_GROUPS[1:]:
        scores = [
            record[score_field]
            for record in records
            if "error" not in record and record_group(record) == attack_type
        ]
        combined.extend(scores)
        result["APCER"][attack_type] = (
            sum(score >= threshold for score in scores) / len(scores) if scores else None
        )
    result["APCER"]["combined"] = (
        sum(score >= threshold for score in combined) / len(combined) if combined else None
    )
    return result


def range_overlap(live_scores, spoof_scores):
    if not live_scores or not spoof_scores:
        return None
    live_range = (min(live_scores), max(live_scores))
    spoof_range = (min(spoof_scores), max(spoof_scores))
    lower = max(live_range[0], spoof_range[0])
    upper = min(live_range[1], spoof_range[1])
    width = max(0.0, upper - lower)
    union_width = max(live_range[1], spoof_range[1]) - min(live_range[0], spoof_range[0])
    return {
        "live_range": list(live_range),
        "spoof_range": list(spoof_range),
        "intersection": [lower, upper] if lower <= upper else None,
        "intersection_width": width,
        "intersection_over_union_width": width / union_width if union_width > 0 else 0.0,
        "live_inside_spoof_range": sum(
            spoof_range[0] <= score <= spoof_range[1] for score in live_scores
        ) / len(live_scores),
        "spoof_inside_live_range": sum(
            live_range[0] <= score <= live_range[1] for score in spoof_scores
        ) / len(spoof_scores),
    }


def overlap_summary(records, score_field):
    live_scores = [
        record[score_field]
        for record in records
        if "error" not in record and record["label"] == "live"
    ]
    result = {}
    for attack_type in REPORT_GROUPS[1:]:
        spoof_scores = [
            record[score_field]
            for record in records
            if "error" not in record and record_group(record) == attack_type
        ]
        result[attack_type] = range_overlap(live_scores, spoof_scores)
    return result


def calculate_summary(records, thresholds):
    valid = [record for record in records if "error" not in record]
    summary = {
        "images_found": len(records),
        "images_evaluated": len(valid),
        "images_failed": len(records) - len(valid),
        "groups": {group: group_summary(records, group) for group in REPORT_GROUPS},
        "diagnostic_thresholds": {},
        "overlap": {},
    }
    for branch, score_field in (
        ("bbox", "bbox_live_score"),
        ("aligned", "aligned_live_score"),
    ):
        summary["diagnostic_thresholds"][branch] = {
            str(threshold): error_rates(records, score_field, threshold)
            for threshold in thresholds
        }
        summary["overlap"][branch] = overlap_summary(records, score_field)
    return summary


def csv_row(record):
    return {
        "file": record["relative_path"],
        "label": record["label"],
        "person_id": record.get("person_id") or "",
        "condition": record.get("condition") or "",
        "attack_type": record.get("attack_type") or "",
        "bbox_live_score": record.get("bbox_live_score", ""),
        "aligned_live_score": record.get("aligned_live_score", ""),
        "delta": record.get("delta", ""),
    }


def write_csv(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_row(record) for record in records)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument(
        "--thresholds",
        type=float,
        nargs="+",
        default=list(DIAGNOSTIC_THRESHOLDS),
        help="Reporting-only thresholds; they never change production configuration.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.checkpoint.is_file() or not args.images.is_dir():
        print("Checkpoint or images directory does not exist", file=sys.stderr)
        return 2
    if args.config is not None and not args.config.is_file():
        print(f"Config does not exist: {args.config}", file=sys.stderr)
        return 2
    if not args.thresholds or any(not 0 <= threshold <= 1 for threshold in args.thresholds):
        print("--thresholds values must be between 0 and 1", file=sys.stderr)
        return 2
    try:
        import torch

        if args.device == "cuda" and not torch.cuda.is_available():
            raise EvaluationError("CUDA was requested but is not available")
        profile = load_profile(args.config)
        model = build_vit_small_fas()
        checkpoint = load_fas_checkpoint(model, args.checkpoint, args.device)
        records = benchmark_images(model, args.images, profile, args.device)
        write_csv(args.csv, records)
        report = {
            "diagnostic_only": True,
            "production_pipeline_changed": False,
            "production_threshold_changed": False,
            "onnx_exported": False,
            "checkpoint": {
                "path": str(args.checkpoint.resolve()),
                "sha256": file_sha256(args.checkpoint),
                "validation": checkpoint,
            },
            "alignment": {
                "landmarks": "InsightFace five-point kps",
                "geometry": "five-point similarity transform",
                "padding": ALIGNMENT_PADDING,
                "output_size": profile.input_size,
            },
            "threshold_note": "All thresholds are diagnostic reporting cutoffs only.",
            "summary": calculate_summary(records, args.thresholds),
        }
        rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        print(rendered)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered + "\n", encoding="utf-8")
        return 1 if report["summary"]["images_failed"] else 0
    except Exception as exc:
        print(f"FS-VFM preprocessing benchmark failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
