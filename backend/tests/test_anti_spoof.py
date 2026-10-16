import hashlib
import os
from types import SimpleNamespace

import numpy as np
import pytest

from school_face_id.recognition import anti_spoof


def config(**changes):
    values = {
        "model_type": "minifasnet_v2_80",
        "input_width": 80,
        "input_height": 80,
        "crop_scale": 2.7,
        "channel_order": "bgr",
        "normalization": "divide_255",
        "output_type": "logits",
        "live_class_index": 1,
        "class_names": ("print", "live", "replay"),
        "threshold": 0.80,
    }
    values.update(changes)
    return anti_spoof.AntiSpoofModelConfig(**values)


class FakeSession:
    def __init__(self, output, shape=(1, 3, 80, 80)):
        self.output = np.asarray(output, dtype=np.float32)
        self.input = SimpleNamespace(name="input", shape=list(shape))
        self.outputs = [SimpleNamespace(name="output")]
        self.last_tensor = None

    def get_inputs(self):
        return [self.input]

    def get_outputs(self):
        return self.outputs

    def run(self, *_args, **kwargs):
        feed = kwargs or (_args[1] if len(_args) > 1 else {})
        if feed:
            self.last_tensor = next(iter(feed.values()))
        return [self.output]


def model_with_output(output, model_config=None):
    model = anti_spoof.OnnxAntiSpoof.__new__(anti_spoof.OnnxAntiSpoof)
    model.config = model_config or config()
    model.session = FakeSession(output)
    model.input_name = "input"
    return model


def test_minifas_preprocessing_shape_bgr_and_divide_255():
    model = model_with_output([[0.0, 3.0, 0.0]])
    frame = np.zeros((120, 120, 3), dtype=np.uint8)
    frame[:, :, 0] = 255
    frame[:, :, 1] = 128
    tensor = model._tensor(frame, (40, 40, 80, 80))
    assert tensor.shape == (1, 3, 80, 80)
    assert tensor.dtype == np.float32
    assert tensor[0, 0, 20, 20] == pytest.approx(1.0)
    assert tensor[0, 1, 20, 20] == pytest.approx(128 / 255.0)
    assert tensor[0, 2, 20, 20] == pytest.approx(0.0)


def test_minifas_crop_uses_scale_2_7_around_bbox_center():
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    crop, coordinates = anti_spoof.scaled_face_crop(frame, (80, 80, 120, 120), 2.7)
    assert coordinates == (46, 46, 154, 154)
    assert crop.shape[:2] == (108, 108)


def test_scaled_crop_clamps_bbox_at_frame_border():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    crop, coordinates = anti_spoof.scaled_face_crop(frame, (0, 0, 20, 20), 2.7)
    assert coordinates == (0, 0, 37, 37)
    assert crop.shape[:2] == (37, 37)


def test_invalid_crop_is_rejected_without_exception_from_predict():
    result = model_with_output([[0.0, 3.0, 0.0]]).predict(
        np.zeros((100, 100, 3), dtype=np.uint8), (50, 50, 50, 80)
    )
    assert result.passed is False
    assert result.reason == "INVALID_PAD_CROP"
    assert result.score is None


def test_three_class_logits_use_softmax_and_preserve_probabilities():
    result = model_with_output([[0.0, 3.0, 1.0]]).predict(
        np.full((120, 120, 3), 127, dtype=np.uint8), (20, 20, 100, 100)
    )
    assert result.live_score > 0.8
    assert result.predicted_class == "live"
    assert set(result.probabilities) == {"print", "live", "replay"}
    assert sum(result.probabilities.values()) == pytest.approx(1.0)
    assert result.passed is True


def test_pad_threshold_uses_live_probability_not_argmax():
    result = model_with_output(
        [[0.0, 1.0, 0.5]], config(threshold=0.80)
    ).predict(np.zeros((100, 100, 3), dtype=np.uint8), (20, 20, 80, 80))
    assert result.predicted_class == "live"
    assert result.live_score < 0.80
    assert result.passed is False
    assert result.reason == "PAD_BELOW_THRESHOLD"


def test_model_contract_requires_nchw_80(monkeypatch, tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"fake")
    monkeypatch.setattr(anti_spoof, "ANTI_SPOOFING_MODEL_SHA256", "")
    with pytest.raises(anti_spoof.AntiSpoofConfigurationError, match="NCHW"):
        anti_spoof.OnnxAntiSpoof(
            path, config(), session_factory=lambda _: FakeSession([[0, 1, 0]], (1, 3, 64, 64))
        )


def test_sha256_match_allows_model_loading(monkeypatch, tmp_path):
    path = tmp_path / "model.onnx"
    content = b"verified-local-model"
    path.write_bytes(content)
    monkeypatch.setattr(
        anti_spoof, "ANTI_SPOOFING_MODEL_SHA256", hashlib.sha256(content).hexdigest()
    )
    model = anti_spoof.OnnxAntiSpoof(
        path, config(), session_factory=lambda _: FakeSession([[0.0, 3.0, 0.0]])
    )
    assert model.input_name == "input"


def test_sha256_mismatch_fails_loudly(monkeypatch, tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"wrong-model")
    monkeypatch.setattr(anti_spoof, "ANTI_SPOOFING_MODEL_SHA256", "0" * 64)
    with pytest.raises(anti_spoof.AntiSpoofConfigurationError, match="SHA256 mismatch"):
        anti_spoof.OnnxAntiSpoof(
            path, config(), session_factory=lambda _: FakeSession([[0.0, 3.0, 0.0]])
        )


def test_inference_errors_fail_closed(monkeypatch):
    class BrokenModel:
        def predict(self, _frame, _box):
            raise RuntimeError("broken")

    monkeypatch.setattr(anti_spoof, "get_anti_spoof_model", lambda: BrokenModel())
    assert anti_spoof.anti_spoof_is_live(
        np.zeros((20, 20, 3), dtype=np.uint8), (0, 0, 20, 20)
    ) is False


def test_missing_model_is_explicit_development_bypass(monkeypatch):
    monkeypatch.setattr(anti_spoof, "get_anti_spoof_model", lambda: None)
    result = anti_spoof.evaluate_anti_spoof(
        np.zeros((20, 20, 3), dtype=np.uint8), (0, 0, 20, 20)
    )
    assert result.passed is False
    assert result.bypassed is True


def test_configured_model_load_error_is_not_bypassed(monkeypatch):
    monkeypatch.setattr(anti_spoof, "ANTI_SPOOFING_MODEL_PATH", "broken.onnx")
    monkeypatch.setattr(anti_spoof, "_model", None)
    with pytest.raises(anti_spoof.AntiSpoofConfigurationError, match="does not exist"):
        anti_spoof.get_anti_spoof_model()


@pytest.mark.skipif(
    not os.getenv("ANTI_SPOOFING_MODEL_PATH"),
    reason="Set ANTI_SPOOFING_MODEL_PATH for optional real-model integration",
)
def test_configured_real_model_contract():
    model = anti_spoof.OnnxAntiSpoof(os.environ["ANTI_SPOOFING_MODEL_PATH"])
    assert model.width == 80
    assert model.height == 80
