"""Inspect one local MiniFASNetV2 ONNX artifact without changing configuration."""

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

from school_face_id.recognition.anti_spoof import (  # noqa: E402
    file_sha256, minifasnet_v2_config, preprocess_pad_input,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
CSV_FIELDS = (
    "file", "label", "raw_0", "raw_1", "raw_2",
    "prob_0", "prob_1", "prob_2", "predicted_index",
    "live_index", "live_score", "passed", "inference_ms",
)


class InspectionError(RuntimeError):
    pass


def stable_softmax(values) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if array.size != 3 or not np.isfinite(array).all():
        raise InspectionError("Expected exactly 3 finite output values")
    shifted = array - float(np.max(array))
    exponentials = np.exp(shifted)
    return exponentials / float(np.sum(exponentials))


def interpret_three_output(values, mode="auto") -> tuple[np.ndarray, str]:
    raw = np.asarray(values, dtype=np.float64).reshape(-1)
    if raw.size != 3:
        raise InspectionError(f"Unexpected output size {raw.size}; expected exactly 3")
    if not np.isfinite(raw).all():
        raise InspectionError("Model output contains non-finite values")
    if mode == "auto":
        is_probability_vector = bool(
            np.all(raw >= 0.0) and np.isclose(float(np.sum(raw)), 1.0, atol=1e-3)
        )
        mode = "probabilities" if is_probability_vector else "logits"
    if mode == "probabilities":
        if np.any(raw < 0.0) or not np.isclose(float(np.sum(raw)), 1.0, atol=1e-3):
            raise InspectionError("Output was forced to probabilities but is not a probability vector")
        probabilities = raw / float(np.sum(raw))
    elif mode == "logits":
        probabilities = stable_softmax(raw)
    else:
        raise InspectionError(f"Unsupported output mode: {mode}")
    return probabilities.astype(np.float64), mode


def analyze_candidate_indexes(records, threshold=None):
    live_records = [record for record in records if record["label"] == "live"]
    spoof_records = [record for record in records if record["label"] == "spoof"]
    if not live_records or not spoof_records:
        return None
    analysis = {}
    for index in range(3):
        live_scores = [record["probabilities"][index] for record in live_records]
        spoof_scores = [record["probabilities"][index] for record in spoof_records]
        row = {
            "live_samples": len(live_records),
            "spoof_samples": len(spoof_records),
            "average_live_class_score_on_live": float(np.mean(live_scores)),
            "average_live_class_score_on_spoof": float(np.mean(spoof_scores)),
            "live_correctly_dominant": sum(
                record["predicted_index"] == index for record in live_records
            ),
            "spoof_rejected_by_dominance": sum(
                record["predicted_index"] != index for record in spoof_records
            ),
        }
        if threshold is not None:
            live_accepted = sum(score >= threshold for score in live_scores)
            spoof_accepted = sum(score >= threshold for score in spoof_scores)
            live_rejected = len(live_scores) - live_accepted
            spoof_rejected = len(spoof_scores) - spoof_accepted
            apcer = spoof_accepted / len(spoof_scores)
            bpcer = live_rejected / len(live_scores)
            row.update({
                "threshold": threshold,
                "live_accepted": live_accepted,
                "live_rejected": live_rejected,
                "spoof_accepted": spoof_accepted,
                "spoof_rejected": spoof_rejected,
                "APCER": apcer,
                "BPCER": bpcer,
                "ACER": (apcer + bpcer) / 2.0,
            })
        analysis[index] = row
    return analysis


def make_csv_row(record, live_index=None, threshold=None):
    raw = record["raw"]
    probabilities = record["probabilities"]
    live_score = probabilities[live_index] if live_index is not None else None
    passed = None
    if live_index is not None:
        passed = (
            live_score >= threshold if threshold is not None
            else record["predicted_index"] == live_index
        )
    values = {
        "file": record["file"], "label": record["label"],
        "raw_0": raw[0], "raw_1": raw[1], "raw_2": raw[2],
        "prob_0": probabilities[0], "prob_1": probabilities[1],
        "prob_2": probabilities[2], "predicted_index": record["predicted_index"],
        "live_index": "" if live_index is None else live_index,
        "live_score": "" if live_score is None else live_score,
        "passed": "" if passed is None else passed,
        "inference_ms": record["inference_ms"],
    }
    return {field: values[field] for field in CSV_FIELDS}


def _model_metadata(session, model_path: Path, synthetic_output):
    model_input = session.get_inputs()[0]
    outputs = session.get_outputs()
    return {
        "model": str(model_path.resolve()),
        "sha256": file_sha256(model_path),
        "providers": session.get_providers(),
        "input": {
            "name": model_input.name,
            "dtype": getattr(model_input, "type", "unknown"),
            "shape": list(model_input.shape),
        },
        "outputs": [
            {
                "name": output.name,
                "shape": list(output.shape) if getattr(output, "shape", None) is not None else None,
            }
            for output in outputs
        ],
        "synthetic_output_shape": list(np.asarray(synthetic_output).shape),
        "raw_synthetic_output": np.asarray(synthetic_output).tolist(),
        "synthetic_semantics": "not inferred",
    }


def _labeled_images(root: Path):
    for label in ("live", "spoof"):
        folder = root / label
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                yield label, path


def _face_bbox(image, full_image_crop=False):
    height, width = image.shape[:2]
    if full_image_crop:
        return (0, 0, width, height)
    from school_face_id.recognition.camera_service import detect_faces

    detection = detect_faces(image)
    if not detection.faces:
        raise InspectionError("no face")
    if len(detection.faces) != 1:
        raise InspectionError(f"multiple faces ({len(detection.faces)})")
    return tuple(int(value) for value in detection.faces[0].bbox)


def inspect_images(
    session, input_name, images_root, output_mode="auto", live_index=None,
    threshold=None, full_image_crop=False, verbose=False,
):
    config = minifasnet_v2_config(
        output_type="logits", live_class_index=live_index if live_index is not None else 1,
        threshold=threshold if threshold is not None else 0.0,
    )
    records = []
    for label, path in _labeled_images(images_root):
        try:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise InspectionError("image unreadable")
            bbox = _face_bbox(image, full_image_crop)
            tensor, crop_coordinates = preprocess_pad_input(image, bbox, config)
            started = time.perf_counter()
            outputs = session.run(None, {input_name: tensor})
            inference_ms = (time.perf_counter() - started) * 1000.0
            if not outputs:
                raise InspectionError("model returned no outputs")
            raw = np.asarray(outputs[0], dtype=np.float64).reshape(-1)
            probabilities, interpretation = interpret_three_output(raw, output_mode)
            predicted_index = int(np.argmax(probabilities))
            score = probabilities[live_index] if live_index is not None else None
            passed = None
            if live_index is not None:
                passed = score >= threshold if threshold is not None else predicted_index == live_index
            record = {
                "file": path.relative_to(images_root).as_posix(),
                "label": label, "bbox": list(bbox),
                "crop_coordinates": list(crop_coordinates),
                "tensor_shape": list(tensor.shape),
                "raw": raw.tolist(), "probabilities": probabilities.tolist(),
                "interpretation": interpretation, "predicted_index": predicted_index,
                "live_index": live_index, "live_score": score, "passed": passed,
                "candidate_decision_rule": (
                    None if live_index is None
                    else f"live_score >= {threshold}" if threshold is not None
                    else "predicted_index == live_index"
                ),
                "inference_ms": inference_ms,
            }
            if verbose:
                record["tensor_dtype"] = str(tensor.dtype)
                record["tensor_range"] = [float(np.min(tensor)), float(np.max(tensor))]
            records.append(record)
            print(json.dumps(record, ensure_ascii=False))
        except Exception as exc:
            print(
                json.dumps({
                    "file": path.relative_to(images_root).as_posix(),
                    "label": label, "error": str(exc),
                }, ensure_ascii=False),
                file=sys.stderr,
            )
    return records


def _write_csv(path: Path, records, live_index=None, threshold=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(make_csv_row(record, live_index, threshold) for record in records)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--live-index", type=int, choices=(0, 1, 2))
    parser.add_argument(
        "--output-mode", choices=("auto", "logits", "probabilities"), default="auto"
    )
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--csv", type=Path)
    parser.add_argument(
        "--full-image-crop", action="store_true",
        help="Treat every input image as an already-cropped face instead of running detection.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.model.is_file():
        print(f"Model file does not exist: {args.model}", file=sys.stderr)
        return 2
    if args.images is not None and not args.images.is_dir():
        print(f"Images directory does not exist: {args.images}", file=sys.stderr)
        return 2
    if args.threshold is not None and not 0.0 <= args.threshold <= 1.0:
        print("--threshold must be in the 0..1 range", file=sys.stderr)
        return 2
    try:
        import onnxruntime as ort

        session = ort.InferenceSession(str(args.model.resolve()), providers=["CPUExecutionProvider"])
        inputs = session.get_inputs()
        if len(inputs) != 1:
            raise InspectionError(f"Expected exactly one model input, found {len(inputs)}")
        input_name = inputs[0].name
        zero_tensor = np.zeros((1, 3, 80, 80), dtype=np.float32)
        synthetic_outputs = session.run(None, {input_name: zero_tensor})
        if not synthetic_outputs:
            raise InspectionError("Synthetic inference returned no outputs")
        synthetic_output = np.asarray(synthetic_outputs[0])
        print(json.dumps(
            _model_metadata(session, args.model, synthetic_output),
            ensure_ascii=False, indent=2,
        ))
        if synthetic_output.size != 3:
            raise InspectionError(
                f"Unexpected output size {synthetic_output.size}; expected exactly 3"
            )
        if args.images is None:
            return 0
        records = inspect_images(
            session, input_name, args.images, args.output_mode, args.live_index,
            args.threshold, args.full_image_crop, args.verbose,
        )
        if args.csv is not None:
            _write_csv(args.csv, records, args.live_index, args.threshold)
        analysis = analyze_candidate_indexes(records, args.threshold)
        if analysis is not None:
            print(json.dumps({"candidate_live_indexes": analysis}, ensure_ascii=False, indent=2))
            best = max(
                analysis,
                key=lambda index: (
                    analysis[index]["average_live_class_score_on_live"]
                    - analysis[index]["average_live_class_score_on_spoof"]
                ),
            )
            print(
                f"Best candidate on this tiny sample: index {best}. "
                "Diagnostic result only; do not treat it as a production class mapping."
            )
        elif args.verbose:
            print("Candidate comparison unavailable: both live and spoof samples are required.")
        return 0
    except Exception as exc:
        print(f"PAD model inspection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
