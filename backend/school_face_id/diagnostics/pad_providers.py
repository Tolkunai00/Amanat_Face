"""Generic diagnostic PAD provider contract and local baseline adapters."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
from pathlib import Path
import threading
import time
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True, slots=True)
class PADProviderResult:
    model_name: str
    model_version: str
    live_score: float | None
    predicted_live: bool
    latency_ms: float
    error: str = ""


@runtime_checkable
class PADProvider(Protocol):
    model_name: str
    model_version: str
    threshold: float
    checkpoint: str
    license_status: str
    input_specification: str
    def predict(self, frame, bbox) -> PADProviderResult: ...


def _sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


ANTI_SPOOF_MN3_SHA256 = "c4c99af04603b62d7e44f6f4daeb33e0daeccc696008c0b1d62f6f5cebbb3262"
ANTI_SPOOF_MN3_SHA384 = (
    "6de4534964b723397b3e8c995cadcf43bc007cc2f9930b95ae25f76adccece5d1"
    "d4d058d0b15117b9e4a9f758424f92a"
)
ANTI_SPOOF_MN3_SIZE = 12_270_179


def validate_checkpoint(path: Path, expected_sha256: str, expected_size: int) -> dict:
    """Validate a pinned artifact without exposing model content in logs."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"checkpoint not found: {path}")
    size = path.stat().st_size
    if size != expected_size:
        raise ValueError(f"checkpoint size mismatch: expected {expected_size}, got {size}")
    digest = _sha256(path)
    if digest.casefold() != expected_sha256.casefold():
        raise ValueError(f"checkpoint SHA256 mismatch: expected {expected_sha256}, got {digest}")
    return {"path": str(path.resolve()), "size": size, "sha256": digest}


class AntiSpoofMN3DiagnosticProvider:
    """Open Model Zoo anti-spoof-mn3 diagnostic adapter (never an attendance gate)."""

    model_name = "anti_spoof_mn3"
    license_status = "COMMERCIAL_OK (MIT model; organizational legal approval still required)"
    commercial_use_status = "MIT permits commercial use"
    input_specification = (
        "exact detector bbox crop; no alignment; OpenCV cubic 128x128; BGR->RGB; "
        "(pixel-[151.2405,119.5950,107.8395])/[63.0105,56.4570,55.0035]; NCHW"
    )
    preprocessing_assumption = None
    class_mapping = {"real": 0, "spoof": 1}
    source_url = (
        "https://storage.openvinotoolkit.org/repositories/open_model_zoo/public/2022.1/"
        "anti-spoof-mn3/anti-spoof-mn3.onnx"
    )

    def __init__(self, checkpoint: Path, threshold=0.50):
        import onnxruntime as ort

        metadata = validate_checkpoint(checkpoint, ANTI_SPOOF_MN3_SHA256, ANTI_SPOOF_MN3_SIZE)
        self.checkpoint = metadata["path"]
        self.model_version = metadata["sha256"]
        self.threshold = float(threshold)
        self._session = ort.InferenceSession(self.checkpoint, providers=["CPUExecutionProvider"])
        inputs, outputs = self._session.get_inputs(), self._session.get_outputs()
        if len(inputs) != 1 or inputs[0].name != "actual_input_1" or list(inputs[0].shape) != [1, 3, 128, 128]:
            raise ValueError("unexpected anti-spoof-mn3 input contract")
        if len(outputs) != 1 or outputs[0].name != "output1" or list(outputs[0].shape) != [1, 2]:
            raise ValueError("unexpected anti-spoof-mn3 output contract")
        self._input_name = inputs[0].name
        self._output_name = outputs[0].name
        self.checkpoint_metadata = {
            **metadata,
            "official_sha384": ANTI_SPOOF_MN3_SHA384,
            "strict_load": True,
            "missing_keys": [],
            "unexpected_keys": [],
            "input_shape": [1, 3, 128, 128],
            "output_shape": [1, 2],
            "framework": "ONNX / ONNX Runtime CPUExecutionProvider",
            "source": self.source_url,
        }

    @staticmethod
    def preprocess(frame, bbox):
        import cv2

        if frame is None or getattr(frame, "ndim", 0) != 3 or frame.shape[2] != 3:
            raise ValueError("expected an HxWx3 BGR frame")
        x1, y1, x2, y2 = (int(value) for value in bbox)
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            raise ValueError("empty face bbox")
        crop = frame[y1:y2, x1:x2]
        rgb = cv2.cvtColor(cv2.resize(crop, (128, 128), interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2RGB)
        values = rgb.astype(np.float32)
        values = (values - np.asarray([151.2405, 119.5950, 107.8395], dtype=np.float32))
        values /= np.asarray([63.0105, 56.4570, 55.0035], dtype=np.float32)
        return np.transpose(values, (2, 0, 1))[None, ...]

    def predict(self, frame, bbox):
        started = time.perf_counter()
        try:
            tensor = self.preprocess(frame, bbox)
            output = np.asarray(
                self._session.run([self._output_name], {self._input_name: tensor})[0],
                dtype=np.float64,
            )
            if output.shape != (1, 2) or not np.isfinite(output).all():
                raise ValueError(f"invalid output shape/value: {output.shape}")
            if not np.isclose(output.sum(), 1.0, atol=1e-4):
                raise ValueError("model output does not satisfy documented probability semantics")
            score = float(output[0, self.class_mapping["real"]])
            return PADProviderResult(
                self.model_name, self.model_version, score, score >= self.threshold,
                (time.perf_counter() - started) * 1000, "",
            )
        except Exception as exc:
            return PADProviderResult(
                self.model_name, self.model_version, None, False,
                (time.perf_counter() - started) * 1000, f"{type(exc).__name__}: {exc}",
            )


class MiniFASNetV2DiagnosticProvider:
    model_name = "minifasnet_v2"
    license_status = "historical baseline; upstream repository/license review required before any production use"
    input_specification = "bbox crop scale 2.7; OpenCV BGR; float32 /255; NCHW 1x3x80x80"
    preprocessing_assumption = None

    def __init__(self, checkpoint: Path, threshold=0.80):
        from school_face_id.recognition.anti_spoof import OnnxAntiSpoof, minifasnet_v2_config

        self.checkpoint = str(Path(checkpoint).resolve())
        self.model_version = _sha256(Path(checkpoint))
        self.threshold = float(threshold)
        self.checkpoint_metadata = {"path": self.checkpoint, "size": Path(checkpoint).stat().st_size, "sha256": self.model_version}
        self._model = OnnxAntiSpoof(
            checkpoint,
            minifasnet_v2_config(threshold=self.threshold),
        )

    def predict(self, frame, bbox):
        started = time.perf_counter()
        try:
            result = self._model.predict(frame, bbox)
            return PADProviderResult(
                self.model_name, self.model_version, result.live_score,
                bool(result.live_score is not None and result.live_score >= self.threshold),
                result.inference_ms if result.inference_ms is not None else (time.perf_counter() - started) * 1000,
                result.error,
            )
        except Exception as exc:
            return PADProviderResult(
                self.model_name, self.model_version, None, False,
                (time.perf_counter() - started) * 1000, f"{type(exc).__name__}: {exc}",
            )


class FSVFMDiagnosticProvider:
    model_name = "fsvfm_vit_small_patch16_fas"
    license_status = "research baseline; checkpoint provenance/license must be reviewed for production"
    input_specification = "bbox square expansion 1.74; RGB; official mean/std; NCHW 1x3x224x224"
    preprocessing_assumption = None

    def __init__(self, checkpoint: Path, threshold=0.50, device="cpu"):
        from scripts.fsvfm_fas_upstream import build_vit_small_fas, load_fas_checkpoint
        from scripts.fsvfm_fas_upstream.preprocess import OFFICIAL_PROFILE

        self.checkpoint = str(Path(checkpoint).resolve())
        self.model_version = _sha256(Path(checkpoint))
        self.threshold = float(threshold)
        self.checkpoint_metadata = {"path": self.checkpoint, "size": Path(checkpoint).stat().st_size, "sha256": self.model_version}
        self.device = device
        self.profile = OFFICIAL_PROFILE
        self._model = build_vit_small_fas()
        load_fas_checkpoint(self._model, Path(checkpoint), device)
        self._lock = threading.Lock()

    def predict(self, frame, bbox):
        from scripts.fsvfm_fas_upstream import preprocess_face
        import numpy as np
        import torch

        started = time.perf_counter()
        try:
            tensor, _ = preprocess_face(frame, bbox, self.profile)
            value = torch.tensor(tensor.tolist(), dtype=torch.float32, device=self.device)
            with self._lock, torch.inference_mode():
                logits = self._model.cls_logits(value)
            values = np.asarray(logits.detach().cpu().tolist(), dtype=np.float64).reshape(-1)
            probabilities = np.exp(values - np.max(values)); probabilities /= probabilities.sum()
            score = float(probabilities[1])
            return PADProviderResult(
                self.model_name, self.model_version, score, score >= self.threshold,
                (time.perf_counter() - started) * 1000, "",
            )
        except Exception as exc:
            return PADProviderResult(
                self.model_name, self.model_version, None, False,
                (time.perf_counter() - started) * 1000, f"{type(exc).__name__}: {exc}",
            )


def load_external_provider(specification: str, **kwargs) -> PADProvider:
    """Load an explicitly configured future adapter as ``module:factory``."""
    if ":" not in specification:
        raise ValueError("external provider must use module:factory syntax")
    module_name, factory_name = specification.split(":", 1)
    provider = getattr(importlib.import_module(module_name), factory_name)(**kwargs)
    if not isinstance(provider, PADProvider):
        raise TypeError("external provider does not implement the PADProvider contract")
    return provider
