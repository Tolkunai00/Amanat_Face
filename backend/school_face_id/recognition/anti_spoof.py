"""Local, profile-driven ONNX presentation-attack detection.

MiniFASNetV2 support is experimental. Model files are never downloaded here;
the configured local artifact and its class order remain deployment inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time

import cv2
import numpy as np

from ..config.settings import (
    ANTI_SPOOFING_CLASS_NAMES, ANTI_SPOOFING_LIVE_INDEX,
    ANTI_SPOOFING_MODEL_PATH, ANTI_SPOOFING_MODEL_SHA256,
    ANTI_SPOOFING_MODEL_TYPE, ANTI_SPOOFING_OUTPUT,
    ANTI_SPOOFING_THRESHOLD,
)
from ..config.model_artifacts import (
    ModelArtifactError, sha256_file, validate_model_artifact,
)


logger = logging.getLogger(__name__)
_lock = threading.Lock()
_model = None
_model_error = ""
_bypass_logged = False


class AntiSpoofConfigurationError(RuntimeError):
    pass


class InvalidPadCrop(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AntiSpoofModelConfig:
    model_type: str
    input_width: int
    input_height: int
    crop_scale: float
    channel_order: str
    normalization: str
    output_type: str
    live_class_index: int
    class_names: tuple[str, ...]
    threshold: float


@dataclass(frozen=True, slots=True)
class PadResult:
    passed: bool
    score: float | None = None
    bypassed: bool = False
    error: str = ""
    predicted_class: str = ""
    probabilities: dict[str, float] | None = None
    reason: str = ""
    inference_ms: float | None = None

    @property
    def live_score(self) -> float | None:
        return self.score


AntiSpoofResult = PadResult


def model_config() -> AntiSpoofModelConfig:
    if ANTI_SPOOFING_MODEL_TYPE == "minifasnet_v2_80":
        return minifasnet_v2_config(
            output_type=ANTI_SPOOFING_OUTPUT,
            live_class_index=ANTI_SPOOFING_LIVE_INDEX,
            class_names=tuple(ANTI_SPOOFING_CLASS_NAMES),
            threshold=ANTI_SPOOFING_THRESHOLD,
        )
    if ANTI_SPOOFING_MODEL_TYPE != "generic":
        raise AntiSpoofConfigurationError(
            f"Unsupported ANTI_SPOOFING_MODEL_TYPE={ANTI_SPOOFING_MODEL_TYPE!r}"
        )
    return AntiSpoofModelConfig(
        "generic", 80, 80, 1.4, "rgb", "divide_255",
        ANTI_SPOOFING_OUTPUT, ANTI_SPOOFING_LIVE_INDEX,
        tuple(ANTI_SPOOFING_CLASS_NAMES), ANTI_SPOOFING_THRESHOLD,
    )


def minifasnet_v2_config(
    output_type="logits",
    live_class_index=1,
    class_names=("print", "live", "replay"),
    threshold=ANTI_SPOOFING_THRESHOLD,
) -> AntiSpoofModelConfig:
    """Build the shared experimental MiniFASNetV2 preprocessing profile."""
    return AntiSpoofModelConfig(
        "minifasnet_v2_80", 80, 80, 2.7, "bgr", "divide_255",
        output_type, int(live_class_index), tuple(class_names), float(threshold),
    )


def file_sha256(path: Path) -> str:
    return sha256_file(path)


def scaled_face_crop(frame, box, scale: float):
    if frame is None or not hasattr(frame, "shape") or len(frame.shape) < 2:
        raise InvalidPadCrop("INVALID_PAD_CROP: frame is empty")
    frame_height, frame_width = frame.shape[:2]
    x1, y1, x2, y2 = [float(value) for value in box]
    width, height = x2 - x1, y2 - y1
    if width <= 0 or height <= 0 or frame_width <= 0 or frame_height <= 0:
        raise InvalidPadCrop("INVALID_PAD_CROP: bbox has no area")
    center_x, center_y = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    scaled_width, scaled_height = width * scale, height * scale
    crop_x1 = max(0, int(np.floor(center_x - scaled_width / 2.0)))
    crop_y1 = max(0, int(np.floor(center_y - scaled_height / 2.0)))
    crop_x2 = min(frame_width, int(np.ceil(center_x + scaled_width / 2.0)))
    crop_y2 = min(frame_height, int(np.ceil(center_y + scaled_height / 2.0)))
    if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
        raise InvalidPadCrop("INVALID_PAD_CROP: expanded bbox is outside the frame")
    crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
    if crop.size == 0:
        raise InvalidPadCrop("INVALID_PAD_CROP: expanded crop is empty")
    return crop, (crop_x1, crop_y1, crop_x2, crop_y2)


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - float(np.max(values))
    exponentials = np.exp(shifted)
    return exponentials / np.sum(exponentials)


def preprocess_pad_input(frame, box, config: AntiSpoofModelConfig):
    """Return the exact PAD tensor and expanded crop coordinates."""
    crop, coordinates = scaled_face_crop(frame, box, config.crop_scale)
    resized = cv2.resize(
        crop, (config.input_width, config.input_height), interpolation=cv2.INTER_LINEAR
    )
    if config.channel_order == "rgb":
        resized = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    elif config.channel_order != "bgr":
        raise AntiSpoofConfigurationError(
            f"Unsupported PAD channel order: {config.channel_order}"
        )
    tensor = resized.astype(np.float32)
    if config.normalization == "divide_255":
        tensor /= 255.0
    elif config.normalization != "none":
        raise AntiSpoofConfigurationError(
            f"Unsupported PAD normalization: {config.normalization}"
        )
    tensor = np.ascontiguousarray(np.transpose(tensor, (2, 0, 1))[None, ...])
    return tensor, coordinates


class OnnxAntiSpoof:
    def __init__(self, model_path, config=None, session_factory=None):
        self.config = config or model_config()
        path = Path(model_path)
        if ANTI_SPOOFING_MODEL_SHA256:
            try:
                validate_model_artifact(
                    str(path), ANTI_SPOOFING_MODEL_SHA256, "ANTI_SPOOFING_MODEL_PATH"
                )
            except ModelArtifactError as exc:
                raise AntiSpoofConfigurationError(str(exc)) from exc
        elif not path.is_file():
            raise AntiSpoofConfigurationError(f"Anti-spoof model file does not exist: {path}")
        if session_factory is None:
            import onnxruntime as ort

            session_factory = lambda value: ort.InferenceSession(
                str(value), providers=["CPUExecutionProvider"]
            )
        try:
            self.session = session_factory(path)
        except Exception as exc:
            raise AntiSpoofConfigurationError(
                f"ONNX Runtime could not load anti-spoof model: {type(exc).__name__}: {exc}"
            ) from exc
        self._validate_contract()

    @property
    def height(self):
        return self.config.input_height

    @property
    def width(self):
        return self.config.input_width

    def _validate_contract(self):
        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if len(inputs) != 1:
            raise AntiSpoofConfigurationError(
                f"Anti-spoof model must have exactly one input, found {len(inputs)}"
            )
        if not outputs:
            raise AntiSpoofConfigurationError("Anti-spoof model has no outputs")
        model_input = inputs[0]
        shape = list(model_input.shape)
        if len(shape) != 4:
            raise AntiSpoofConfigurationError(
                f"Anti-spoof input must be rank 4 NCHW, found shape {shape}"
            )
        expected = (1, 3, self.height, self.width)
        for actual, wanted in zip(shape, expected):
            if isinstance(actual, int) and actual != wanted:
                raise AntiSpoofConfigurationError(
                    f"Anti-spoof input must be NCHW {expected}, found {tuple(shape)}"
                )
        self.input_name = model_input.name
        synthetic = np.zeros(expected, dtype=np.float32)
        try:
            raw_outputs = self.session.run(None, {self.input_name: synthetic})
            if not raw_outputs:
                raise ValueError("model returned no outputs")
            probabilities = self._probabilities(
                np.asarray(raw_outputs[0], dtype=np.float32).reshape(-1)
            )
            live_index = 0 if self.config.output_type == "scalar" else self.config.live_class_index
            if not 0 <= live_index < probabilities.size:
                raise AntiSpoofConfigurationError(
                    "ANTI_SPOOFING_LIVE_INDEX is outside the model output"
                )
            if self.config.model_type == "minifasnet_v2_80":
                if probabilities.size != 3:
                    raise AntiSpoofConfigurationError(
                        "minifasnet_v2_80 profile requires one three-class output"
                    )
                if len(self.config.class_names) != 3:
                    raise AntiSpoofConfigurationError(
                        "minifasnet_v2_80 requires exactly three ANTI_SPOOFING_CLASS_NAMES"
                    )
        except AntiSpoofConfigurationError:
            raise
        except Exception as exc:
            raise AntiSpoofConfigurationError(
                f"Anti-spoof synthetic validation inference failed: {type(exc).__name__}: {exc}"
            ) from exc

    def _tensor(self, frame, box):
        tensor, _ = preprocess_pad_input(frame, box, self.config)
        return tensor

    def _probabilities(self, output: np.ndarray) -> np.ndarray:
        if not output.size or not np.isfinite(output).all():
            raise AntiSpoofConfigurationError("Anti-spoof model returned invalid output")
        if self.config.output_type == "scalar":
            if output.size != 1:
                raise AntiSpoofConfigurationError("Scalar PAD output must contain one value")
            value = float(output[0])
            live = value if 0.0 <= value <= 1.0 else 1.0 / (1.0 + np.exp(-value))
            return np.asarray([live], dtype=np.float32)
        if self.config.output_type == "probabilities":
            if np.any(output < 0.0) or np.any(output > 1.0):
                raise AntiSpoofConfigurationError("PAD probabilities must be in the 0..1 range")
            total = float(np.sum(output))
            if total <= 0:
                raise AntiSpoofConfigurationError("PAD probabilities sum to zero")
            return output / total
        if self.config.output_type != "logits":
            raise AntiSpoofConfigurationError(
                f"Unsupported ANTI_SPOOFING_OUTPUT={self.config.output_type!r}"
            )
        return _softmax(output)

    def predict(self, frame, box) -> PadResult:
        started = time.perf_counter()
        try:
            tensor = self._tensor(frame, box)
            output = np.asarray(
                self.session.run(None, {self.input_name: tensor})[0], dtype=np.float32
            ).reshape(-1)
            values = self._probabilities(output)
            live_index = 0 if self.config.output_type == "scalar" else self.config.live_class_index
            if not 0 <= live_index < values.size:
                raise AntiSpoofConfigurationError(
                    "ANTI_SPOOFING_LIVE_INDEX is outside the model output"
                )
            names = self.config.class_names
            if len(names) != values.size:
                names = tuple(f"class_{index}" for index in range(values.size))
            probabilities = {name: float(value) for name, value in zip(names, values)}
            predicted_index = int(np.argmax(values))
            live_score = float(values[live_index])
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            passed = live_score >= self.config.threshold
            return PadResult(
                passed=passed, score=live_score,
                predicted_class=names[predicted_index], probabilities=probabilities,
                reason="" if passed else "PAD_BELOW_THRESHOLD",
                inference_ms=elapsed_ms,
            )
        except InvalidPadCrop:
            return PadResult(
                passed=False, error="INVALID_PAD_CROP", reason="INVALID_PAD_CROP",
                inference_ms=(time.perf_counter() - started) * 1000.0,
            )

    def live_probability(self, frame, box):
        result = self.predict(frame, box)
        if result.score is None:
            raise InvalidPadCrop(result.reason or "INVALID_PAD_CROP")
        return result.score

    def is_live(self, frame, box):
        return self.predict(frame, box).passed


def get_anti_spoof_model():
    global _model, _model_error
    if not ANTI_SPOOFING_MODEL_PATH:
        return None
    with _lock:
        if _model is None:
            try:
                _model = OnnxAntiSpoof(ANTI_SPOOFING_MODEL_PATH)
                _model_error = ""
            except Exception as exc:
                _model_error = f"{type(exc).__name__}: {exc}"
                raise
        return _model


def anti_spoof_status():
    if _model is not None:
        return {"status": "ready"}
    if _model_error:
        return {"status": "not_ready", "error": _model_error}
    if not ANTI_SPOOFING_MODEL_PATH:
        return {"status": "not_ready", "error": "model is not configured"}
    return {"status": "not_ready", "error": "model is not loaded"}


def anti_spoof_is_live(frame, box):
    result = evaluate_anti_spoof(frame, box)
    return result.passed or result.bypassed


def evaluate_anti_spoof(frame, box) -> PadResult:
    global _bypass_logged
    model = get_anti_spoof_model()
    if model is None:
        with _lock:
            if not _bypass_logged:
                logger.warning("pad_bypass anti_spoof_bypassed=true reason=model_not_configured")
                _bypass_logged = True
        return PadResult(
            passed=False, bypassed=True, reason="MODEL_NOT_CONFIGURED", inference_ms=0.0
        )
    try:
        result = model.predict(frame, box)
    except Exception as exc:
        logger.exception("pad_fail reason=inference_error")
        return PadResult(
            passed=False, error=type(exc).__name__, reason="PAD_INFERENCE_ERROR"
        )
    logger.debug(
        "pad_%s live_score=%s predicted_class=%s pad_inference_ms=%.2f reason=%s",
        "pass" if result.passed else "fail", result.score, result.predicted_class,
        result.inference_ms or 0.0, result.reason,
    )
    return result


def anti_spoof_loaded():
    return _model is not None
