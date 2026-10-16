"""A/B diagnostic: current ONNX versus official upstream MiniFASNetV2 .pth.

This is a standalone developer tool.  It never reads or changes production PAD
settings, thresholds, or class mappings.
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
import json
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from minifasnet_v2_upstream import MiniFASNetV2, upstream_tensor  # noqa: E402
from school_face_id.recognition.anti_spoof import (  # noqa: E402
    file_sha256, minifasnet_v2_config, preprocess_pad_input,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
UPSTREAM_REPOSITORY = "https://github.com/minivision-ai/Silent-Face-Anti-Spoofing"
UPSTREAM_MODEL = (
    UPSTREAM_REPOSITORY
    + "/blob/master/resources/anti_spoof_models/2.7_80x80_MiniFASNetV2.pth"
)


class ComparisonError(RuntimeError):
    pass


def softmax(values):
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    shifted = values - np.max(values)
    exponentials = np.exp(shifted)
    return exponentials / np.sum(exponentials)


def load_upstream_model(path: Path):
    import torch

    model = MiniFASNetV2(conv6_kernel=(5, 5))
    state = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(state, dict):
        raise ComparisonError("The .pth file does not contain a state_dict")
    if state and next(iter(state)).startswith("module."):
        state = OrderedDict((key[7:], value) for key, value in state.items())
    incompatible = model.load_state_dict(state, strict=True)
    if incompatible.missing_keys or incompatible.unexpected_keys:
        raise ComparisonError(f"Non-strict state_dict load: {incompatible}")
    model.eval()
    return model


def detect_bbox(image):
    from school_face_id.recognition.camera_service import detect_faces

    detection = detect_faces(image)
    if len(detection.faces) != 1:
        raise ComparisonError(f"expected one detected face, found {len(detection.faces)}")
    return tuple(int(value) for value in detection.faces[0].bbox)


def compare_tensor(tensor, onnx_session, input_name, torch_model):
    import torch

    onnx_logits = np.asarray(
        onnx_session.run(None, {input_name: tensor.copy()})[0], dtype=np.float64
    ).reshape(-1)
    # torch 2.3 wheels cannot use the NumPy C bridge with NumPy 2.x. Building
    # from/to plain lists keeps this diagnostic usable in that environment and
    # still guarantees that the runtimes receive independent tensor copies.
    torch_input = torch.tensor(tensor.copy().tolist(), dtype=torch.float32)
    with torch.inference_mode():
        pth_logits = np.asarray(torch_model(torch_input).cpu().tolist(), dtype=np.float64).reshape(-1)
    if onnx_logits.size != 3 or pth_logits.size != 3:
        raise ComparisonError(
            f"expected three logits, got ONNX={onnx_logits.shape}, PTH={pth_logits.shape}"
        )
    onnx_probs = softmax(onnx_logits)
    pth_probs = softmax(pth_logits)
    return {
        "onnx_logits": onnx_logits.tolist(),
        "pth_logits": pth_logits.tolist(),
        "onnx_probs": onnx_probs.tolist(),
        "pth_probs": pth_probs.tolist(),
        "onnx_predicted_index": int(np.argmax(onnx_probs)),
        "pth_predicted_index": int(np.argmax(pth_probs)),
        "max_absolute_logit_difference": float(np.max(np.abs(onnx_logits - pth_logits))),
        "probability_diff": float(np.max(np.abs(onnx_probs - pth_probs))),
        "same_predicted_class": bool(np.argmax(onnx_probs) == np.argmax(pth_probs)),
    }


def iter_images(root: Path):
    for label in ("live", "spoof"):
        folder = root / label
        for path in sorted(folder.iterdir() if folder.is_dir() else []):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                yield label, path


def compare_images(onnx_session, torch_model, images_root: Path):
    input_name = onnx_session.get_inputs()[0].name
    config = minifasnet_v2_config()
    records = []
    for label, path in iter_images(images_root):
        base = {
            "file": path.relative_to(images_root).as_posix(),
            "label": label,
            "onnx_probs": None,
            "pth_probs": None,
            "probability_diff": None,
            "same_predicted_class": None,
            "our_crop_vs_upstream_crop_difference": None,
        }
        try:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise ComparisonError("image unreadable")
            bbox = detect_bbox(image)  # exactly once; shared by both branches
            tensor, our_coordinates = preprocess_pad_input(image, bbox, config)
            reference, upstream_coordinates = upstream_tensor(image, bbox, config.crop_scale)
            result = {
                **base,
                "bbox_xyxy": list(bbox),
                "our_crop_coordinates_xyxy_exclusive": list(our_coordinates),
                "upstream_crop_coordinates_xyxy_inclusive": list(upstream_coordinates),
                "tensor_shape": list(tensor.shape),
                "tensor_dtype": str(tensor.dtype),
                "tensor_order": "NCHW from OpenCV BGR",
                "our_crop_vs_upstream_crop_difference": float(
                    np.max(np.abs(tensor.astype(np.float64) - reference.astype(np.float64)))
                ),
                **compare_tensor(tensor, onnx_session, input_name, torch_model),
            }
            records.append(result)
        except Exception as exc:
            records.append({**base, "error": f"{type(exc).__name__}: {exc}"})
    return records


def build_report(onnx_path, pth_path, session, model, records):
    input_meta = session.get_inputs()[0]
    output_meta = session.get_outputs()[0]
    return {
        "diagnostic_only": True,
        "production_settings_changed": False,
        "threshold_selected": False,
        "live_index_changed": False,
        "upstream": {
            "repository": UPSTREAM_REPOSITORY,
            "model": UPSTREAM_MODEL,
            "architecture": UPSTREAM_REPOSITORY + "/blob/master/src/model_lib/MiniFASNet.py",
            "crop": UPSTREAM_REPOSITORY + "/blob/master/src/generate_patches.py",
        },
        "artifacts": {
            "onnx": str(onnx_path.resolve()),
            "onnx_sha256": file_sha256(onnx_path),
            "pth": str(pth_path.resolve()),
            "pth_sha256": file_sha256(pth_path),
        },
        "checks": {
            "architecture": "MiniFASNetV2 / keep_dict['1.8M_'] / 3 classes",
            "state_dict_loading": "strict=True",
            "conv6_kernel_expected": [5, 5],
            "conv6_kernel_actual": list(model.conv_6_dw.conv.kernel_size),
            "eval_mode": not model.training,
            "tensor_order": "NCHW, BGR channels, float32 divided by 255",
            "onnx_input": {"name": input_meta.name, "shape": list(input_meta.shape), "type": input_meta.type},
            "onnx_output": {"name": output_meta.name, "shape": list(output_meta.shape), "type": output_meta.type},
            "export_assumption": "ONNX output and upstream model output are raw logits",
            "shared_input_copy_per_runtime": True,
            "reference_mode": "official CropImage geometry + OpenCV resize + upstream ToTensor semantics",
            "crop_difference_metric": "max absolute difference between normalized 1x3x80x80 tensors",
        },
        "files": records,
        "summary": {
            "images_found": len(records),
            "images_compared": sum("error" not in row for row in records),
            "images_failed": sum("error" in row for row in records),
            "max_absolute_logit_difference": max(
                (row["max_absolute_logit_difference"] for row in records if "error" not in row),
                default=None,
            ),
            "max_absolute_probability_difference": max(
                (row["probability_diff"] for row in records if "error" not in row),
                default=None,
            ),
            "all_predicted_classes_equal": all(
                row["same_predicted_class"] for row in records if "error" not in row
            ),
        },
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--pth", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    missing = [path for path in (args.onnx, args.pth) if not path.is_file()]
    if missing or not args.images.is_dir():
        print(f"Missing input: {missing or args.images}", file=sys.stderr)
        return 2
    try:
        import onnxruntime as ort

        session = ort.InferenceSession(str(args.onnx.resolve()), providers=["CPUExecutionProvider"])
        if len(session.get_inputs()) != 1 or len(session.get_outputs()) < 1:
            raise ComparisonError("expected one ONNX input and at least one output")
        model = load_upstream_model(args.pth)
        records = compare_images(session, model, args.images)
        report = build_report(args.onnx, args.pth, session, model, records)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        print(rendered)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered + "\n", encoding="utf-8")
        return 1 if report["summary"]["images_failed"] else 0
    except Exception as exc:
        print(f"A/B diagnostic failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
