import numpy as np
import pytest

from scripts.compare_minifasnet_v2 import compare_tensor, softmax
from scripts.minifasnet_v2_upstream import CropImage, MiniFASNetV2, upstream_tensor


def test_upstream_architecture_has_80x80_conv6_kernel_and_three_logits():
    import torch

    model = MiniFASNetV2(conv6_kernel=(5, 5)).eval()
    with torch.inference_mode():
        output = model(torch.zeros((1, 3, 80, 80), dtype=torch.float32))
    assert model.conv_6_dw.conv.kernel_size == (5, 5)
    assert output.shape == (1, 3)
    assert model.training is False


def test_official_crop_shifts_box_inside_image_and_keeps_requested_size():
    assert CropImage.get_new_box(100, 100, (0, 10, 20, 20), 2.7) == (0, 0, 54, 54)


def test_upstream_tensor_is_bgr_float32_divided_by_255():
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    image[:, :, 0] = 255
    tensor, _ = upstream_tensor(image, (5, 5, 15, 15), scale=1.0)
    assert tensor.shape == (1, 3, 80, 80)
    assert tensor.dtype == np.float32
    assert tensor[0, 0].mean() == pytest.approx(1.0)
    assert tensor[0, 1].mean() == pytest.approx(0.0)


def test_compare_tensor_sends_independent_copies_to_both_runtimes():
    import torch

    class Session:
        def run(self, _outputs, inputs):
            value = next(iter(inputs.values()))
            value.fill(0)
            return [np.asarray([[1.0, 2.0, 3.0]], dtype=np.float32)]

    class Model(torch.nn.Module):
        def forward(self, value):
            assert torch.count_nonzero(value).item() > 0
            return torch.tensor([[1.0, 2.0, 3.0]])

    tensor = np.ones((1, 3, 80, 80), dtype=np.float32)
    result = compare_tensor(tensor, Session(), "input", Model())
    assert result["max_absolute_logit_difference"] == 0.0
    assert result["probability_diff"] == 0.0
    assert result["same_predicted_class"] is True
    assert np.all(tensor == 1.0)


def test_softmax_is_stable():
    assert softmax([1000, 1001, 999]).sum() == pytest.approx(1.0)
