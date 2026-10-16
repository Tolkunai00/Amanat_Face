"""Experimental FS-VFM PAD provider, isolated from identity recognition."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time

import numpy as np

from ..config.model_artifacts import validate_model_artifact
from ..config.settings import (
    FSVFM_CHECKPOINT, FSVFM_CHECKPOINT_SHA256, FSVFM_ENABLED,
    FSVFM_SHADOW_MODE, FSVFM_THRESHOLD,
)


logger = logging.getLogger(__name__)
MODEL_NAME = "fsvfm_vit_small_patch16_fas"
_provider = None
_provider_error = ""
_provider_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class FSVFMResult:
    live_score: float | None
    predicted_live: bool
    threshold: float
    model_name: str
    latency_ms: float
    error: str = ""


class FSVFMProvider:
    """Lazy local checkpoint adapter for live/spoof inference only."""

    def __init__(self, checkpoint, threshold=FSVFM_THRESHOLD, device="cpu"):
        from scripts.fsvfm_fas_upstream import build_vit_small_fas, load_fas_checkpoint
        from scripts.fsvfm_fas_upstream.preprocess import OFFICIAL_PROFILE

        validate_model_artifact(
            str(checkpoint), FSVFM_CHECKPOINT_SHA256, "FSVFM_CHECKPOINT"
        )
        self.threshold = float(threshold)
        if not 0 <= self.threshold <= 1:
            raise ValueError("FSVFM threshold must be between zero and one")
        self.device = device
        self.profile = OFFICIAL_PROFILE
        self.model = build_vit_small_fas()
        load_fas_checkpoint(self.model, Path(checkpoint), device)
        self._inference_lock = threading.Lock()

    def predict(self, frame, bbox) -> FSVFMResult:
        from scripts.fsvfm_fas_upstream import preprocess_face
        import torch

        started = time.perf_counter()
        try:
            tensor, _ = preprocess_face(frame, bbox, self.profile)
            value = torch.tensor(tensor.tolist(), dtype=torch.float32, device=self.device)
            with self._inference_lock, torch.inference_mode():
                logits = self.model.cls_logits(value)
            values = np.asarray(logits.detach().cpu().tolist(), dtype=np.float64).reshape(-1)
            if values.size != 2 or not np.isfinite(values).all():
                raise ValueError("expected exactly two finite FS-VFM logits")
            probabilities = np.exp(values - np.max(values))
            probabilities /= probabilities.sum()
            live_score = float(probabilities[1])
            return FSVFMResult(
                live_score=live_score,
                predicted_live=live_score >= self.threshold,
                threshold=self.threshold,
                model_name=MODEL_NAME,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        except Exception as exc:
            logger.exception("fsvfm_inference_failed")
            return FSVFMResult(
                live_score=None,
                predicted_live=False,
                threshold=self.threshold,
                model_name=MODEL_NAME,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=f"{type(exc).__name__}: {exc}",
            )


def get_fsvfm_provider():
    global _provider, _provider_error
    if not FSVFM_ENABLED:
        return None
    if not FSVFM_CHECKPOINT:
        _provider_error = "FSVFM_CHECKPOINT is not configured"
        return None
    with _provider_lock:
        if _provider is None and not _provider_error:
            try:
                _provider = FSVFMProvider(FSVFM_CHECKPOINT, FSVFM_THRESHOLD)
            except Exception as exc:
                _provider_error = f"{type(exc).__name__}: {exc}"
                logger.exception("fsvfm_provider_initialization_failed")
        return _provider


def evaluate_fsvfm(frame, bbox) -> FSVFMResult:
    if not FSVFM_ENABLED:
        return FSVFMResult(None, False, FSVFM_THRESHOLD, MODEL_NAME, 0.0, "FSVFM_DISABLED")
    provider = get_fsvfm_provider()
    if provider is None:
        return FSVFMResult(
            None,
            False,
            FSVFM_THRESHOLD,
            MODEL_NAME,
            0.0,
            _provider_error or "FSVFM_PROVIDER_UNAVAILABLE",
        )
    return provider.predict(frame, bbox)


def initialize_fsvfm_for_startup():
    """Validate and cache FS-VFM once; shadow failures remain explicitly degraded."""
    global _provider_error
    if not FSVFM_ENABLED:
        return {"status": "disabled", "enforced": False}
    if not FSVFM_CHECKPOINT:
        _provider_error = "FSVFM_CHECKPOINT is not configured"
    elif not FSVFM_CHECKPOINT_SHA256:
        _provider_error = "FSVFM_CHECKPOINT_SHA256 is not configured"
    else:
        get_fsvfm_provider()
    if _provider is not None:
        return {"status": "ready", "enforced": not FSVFM_SHADOW_MODE}
    error = _provider_error or "FSVFM provider is unavailable"
    if FSVFM_SHADOW_MODE:
        logger.error("fsvfm_startup_degraded error=%s", error)
        return {"status": "degraded", "enforced": False, "error": error}
    raise RuntimeError(error)


def fsvfm_status():
    if not FSVFM_ENABLED:
        return {"status": "disabled", "enforced": False}
    if _provider is not None:
        return {"status": "ready", "enforced": not FSVFM_SHADOW_MODE}
    error = _provider_error or "FS-VFM startup validation has not completed"
    return {
        "status": "degraded" if FSVFM_SHADOW_MODE else "not_ready",
        "enforced": not FSVFM_SHADOW_MODE,
        "error": error,
    }
