"""Evaluate an official fine-tuned FS-VFM ViT-S/16 cross-domain FAS checkpoint.

Diagnostic only: this script has no dependency on the production PAD adapter and
does not read or mutate production anti-spoof settings.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import statistics
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fsvfm_fas_upstream import (  # noqa: E402
    build_vit_small_fas, file_sha256, load_fas_checkpoint, preprocess_face,
)
from fsvfm_fas_upstream.preprocess import load_profile  # noqa: E402


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
CLASS_NAMES = ("spoof", "live")
LIVE_INDEX = 1
LIVE_CONDITIONS = ("front", "left", "right", "far", "low_light")
ATTACK_TYPES = ("phone_photo", "phone_video", "laptop_photo", "laptop_video", "print")
CSV_FIELDS = (
    "file", "relative_path", "label", "person_id", "condition", "attack_type",
    "live_score", "predicted_class", "probabilities", "inference_ms", "error",
)


class EvaluationError(RuntimeError):
    pass


def stable_softmax(values):
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    if values.size != 2 or not np.isfinite(values).all():
        raise EvaluationError("Expected exactly two finite FAS logits")
    exponentials = np.exp(values - np.max(values))
    return exponentials / exponentials.sum()


def class_mapping():
    return {
        "0": "spoof",
        "1": "live",
        "live_index": LIVE_INDEX,
        "evidence": [
            "cross_domain_FAS/train_vit.py fills fake labels with 0 and real labels with 1",
            "cross_domain_FAS/utils/evaluate.py uses softmax(...)[..., 1] as the positive score",
        ],
    }


def detect_bbox(image):
    from school_face_id.recognition.camera_service import detect_faces

    detection = detect_faces(image)
    if len(detection.faces) != 1:
        raise EvaluationError(f"expected one detected face, found {len(detection.faces)}")
    return tuple(int(value) for value in detection.faces[0].bbox)


def parse_image_metadata(root, path):
    """Return dataset metadata encoded in an image path below *root*."""
    relative_path = path.relative_to(root)
    parts = relative_path.parts
    label = parts[0] if parts and parts[0] in {"live", "spoof"} else None
    person_id = None
    condition = None
    attack_type = None
    if label == "live":
        person_id = parts[1] if len(parts) >= 3 else None
        condition = parts[2] if len(parts) >= 4 else None
    elif label == "spoof":
        attack_type = parts[1] if len(parts) >= 3 else None
    return {
        "file": path.name,
        "relative_path": relative_path.as_posix(),
        "label": label,
        "person_id": person_id,
        "condition": condition,
        "attack_type": attack_type,
    }


def iter_images(root):
    for label in ("live", "spoof"):
        folder = root / label
        for path in sorted(folder.rglob("*") if folder.is_dir() else []):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                yield label, path


def infer_one(model, tensor, device):
    import torch

    # This list bridge also supports torch 2.3 wheels used with NumPy 2.x.
    value = torch.tensor(tensor.tolist(), dtype=torch.float32, device=device)
    if device == "cuda":
        torch.cuda.synchronize()
    started = time.perf_counter()
    with torch.inference_mode():
        logits = model.cls_logits(value)
    if device == "cuda":
        torch.cuda.synchronize()
    inference_ms = (time.perf_counter() - started) * 1000.0
    logits = np.asarray(logits.detach().cpu().tolist(), dtype=np.float64).reshape(-1)
    probabilities = stable_softmax(logits)
    predicted_index = int(np.argmax(probabilities))
    return {
        "raw_logits": logits.tolist(),
        "probabilities": probabilities.tolist(),
        "predicted_index": predicted_index,
        "predicted_class": CLASS_NAMES[predicted_index],
        "live_score": float(probabilities[LIVE_INDEX]),
        "inference_ms": inference_ms,
    }


def evaluate_images(model, images_root, profile, device, verbose=False):
    records = []
    for label, path in iter_images(images_root):
        base = parse_image_metadata(images_root, path)
        try:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise EvaluationError("image unreadable")
            bbox = detect_bbox(image)
            tensor, crop = preprocess_face(image, bbox, profile)
            record = {
                **base,
                "bbox": list(bbox),
                "input_tensor_shape": list(tensor.shape),
                **infer_one(model, tensor, device),
            }
            if verbose:
                record.update({
                    "crop_coordinates": list(crop),
                    "input_dtype": str(tensor.dtype),
                    "input_range": [float(tensor.min()), float(tensor.max())],
                })
            records.append(record)
            print(json.dumps(record, ensure_ascii=False))
        except Exception as exc:
            record = {**base, "error": f"{type(exc).__name__}: {exc}"}
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), file=sys.stderr)
    return records


def percentile(values, quantile):
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=np.float64), quantile))


def grouped_score_summary(records, field, expected_values=()):
    grouped = {value: [] for value in expected_values}
    for record in records:
        value = record.get(field)
        if value is not None:
            grouped.setdefault(value, []).append(record)
    result = {}
    for value, rows in grouped.items():
        evaluated = [row for row in rows if "error" not in row]
        scores = [row["live_score"] for row in evaluated]
        result[value] = {
            "count": len(rows),
            "evaluated": len(evaluated),
            "failed": len(rows) - len(evaluated),
            "mean_live_score": statistics.fmean(scores) if scores else None,
            "min_live_score": min(scores) if scores else None,
            "max_live_score": max(scores) if scores else None,
        }
    return result


def calculate_summary(records, threshold=None):
    valid = [record for record in records if "error" not in record]
    live_scores = [record["live_score"] for record in valid if record["label"] == "live"]
    spoof_scores = [record["live_score"] for record in valid if record["label"] == "spoof"]
    latencies = [record["inference_ms"] for record in valid]
    summary = {
        "images_found": len(records),
        "images_evaluated": len(valid),
        "images_failed": len(records) - len(valid),
        "live_count": sum(record["label"] == "live" for record in records),
        "spoof_count": sum(record["label"] == "spoof" for record in records),
        "per_person": grouped_score_summary(
            (record for record in records if record["label"] == "live"), "person_id"
        ),
        "per_live_condition": grouped_score_summary(
            (record for record in records if record["label"] == "live"),
            "condition",
            LIVE_CONDITIONS,
        ),
        "per_attack_type": grouped_score_summary(
            (record for record in records if record["label"] == "spoof"),
            "attack_type",
            ATTACK_TYPES,
        ),
        "mean_live_score_on_live": statistics.fmean(live_scores) if live_scores else None,
        "mean_live_score_on_spoof": statistics.fmean(spoof_scores) if spoof_scores else None,
        "score_separation": (
            statistics.fmean(live_scores) - statistics.fmean(spoof_scores)
            if live_scores and spoof_scores else None
        ),
        "min_live_score": min(live_scores) if live_scores else None,
        "max_live_score": max(live_scores) if live_scores else None,
        "min_spoof_score": min(spoof_scores) if spoof_scores else None,
        "max_spoof_score": max(spoof_scores) if spoof_scores else None,
        "timing_ms": {
            "average": statistics.fmean(latencies) if latencies else None,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "sample_count": len(latencies),
            "capacity_conclusion": "not inferred from this small diagnostic sample",
        },
    }
    if threshold is not None:
        live_accepted = sum(score >= threshold for score in live_scores)
        spoof_accepted = sum(score >= threshold for score in spoof_scores)
        live_rejected = len(live_scores) - live_accepted
        spoof_rejected = len(spoof_scores) - spoof_accepted
        bpcer = live_rejected / len(live_scores) if live_scores else None
        apcer = spoof_accepted / len(spoof_scores) if spoof_scores else None
        summary["threshold_metrics"] = {
            "threshold": threshold,
            "live_accepted": live_accepted,
            "live_rejected": live_rejected,
            "spoof_accepted": spoof_accepted,
            "spoof_rejected": spoof_rejected,
            "APCER": apcer,
            "BPCER": bpcer,
            "ACER": (apcer + bpcer) / 2.0 if apcer is not None and bpcer is not None else None,
        }
    return summary


def csv_row(record):
    probabilities = record.get("probabilities")
    values = {
        "file": record["file"],
        "relative_path": record.get("relative_path", ""),
        "label": record["label"],
        "person_id": record.get("person_id") or "",
        "condition": record.get("condition") or "",
        "attack_type": record.get("attack_type") or "",
        "live_score": record.get("live_score", ""),
        "predicted_class": record.get("predicted_class", "unknown"),
        "probabilities": "" if probabilities is None else json.dumps(probabilities),
        "inference_ms": record.get("inference_ms", ""),
        "error": record.get("error", ""),
    }
    # Keep direct callers that construct pre-metadata records working. Records
    # produced by evaluate_images always contain relative_path and use CSV_FIELDS.
    if "relative_path" not in record:
        legacy_fields = (
            "file", "label", "live_score", "predicted_class", "probabilities", "inference_ms",
        )
        return {field: values[field] for field in legacy_fields}
    return {field: values[field] for field in CSV_FIELDS}


def write_csv(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_row(record) for record in records)


def preprocessing_metadata(profile):
    return {
        "expected_input_size": [224, 224],
        "color_order": profile.color_order,
        "mean": list(profile.mean),
        "std": list(profile.std),
        "crop_resize_strategy": profile.resize_strategy,
        "center_crop": False,
        "face_expansion": profile.face_expansion,
        "official_alignment_padding": 0.37,
        "face_crop_note": (
            "Official MCIO preparation creates landmark-aligned MTCNN face chips with padding=0.37. "
            "The bbox-only Amanat adaptation cannot reproduce landmark alignment; it uses the "
            "equivalent square extent 1 + 2*padding = 1.74."
        ),
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.checkpoint.is_file() or not args.images.is_dir():
        print("Checkpoint or images directory does not exist", file=sys.stderr)
        return 2
    if args.config is not None and not args.config.is_file():
        print(f"Config does not exist: {args.config}", file=sys.stderr)
        return 2
    if args.threshold is not None and not 0 <= args.threshold <= 1:
        print("--threshold must be between 0 and 1", file=sys.stderr)
        return 2
    try:
        import torch

        if args.device == "cuda" and not torch.cuda.is_available():
            raise EvaluationError("CUDA was requested but is not available")
        profile = load_profile(args.config)
        artifact = {
            "absolute_path": str(args.checkpoint.resolve()),
            "sha256": file_sha256(args.checkpoint),
            "size_bytes": args.checkpoint.stat().st_size,
        }
        print(json.dumps({"checkpoint_artifact": artifact}, ensure_ascii=False, indent=2))
        model = build_vit_small_fas()
        checkpoint = load_fas_checkpoint(model, args.checkpoint, args.device)
        print(json.dumps({"checkpoint_validation": checkpoint}, ensure_ascii=False, indent=2, default=str))
        print(json.dumps({
            "preprocessing": preprocessing_metadata(profile),
            "class_mapping": class_mapping(),
        }, ensure_ascii=False, indent=2))
        records = evaluate_images(model, args.images, profile, args.device, args.verbose)
        if args.csv:
            write_csv(args.csv, records)
        summary = calculate_summary(records, args.threshold)
        print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))
        return 1 if summary["images_failed"] else 0
    except Exception as exc:
        print(f"FS-VFM FAS evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
