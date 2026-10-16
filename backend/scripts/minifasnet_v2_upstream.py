"""Diagnostic-only copy of the upstream MiniFASNetV2 architecture and crop.

Source: https://github.com/minivision-ai/Silent-Face-Anti-Spoofing
Files: ``src/model_lib/MiniFASNet.py`` and ``src/generate_patches.py``.

This module deliberately has no imports from the production PAD adapter.  It is
kept next to the A/B diagnostic so the reference implementation cannot become
part of the application pipeline by accident.
"""

from __future__ import annotations

import cv2
import numpy as np
import torch
from torch.nn import BatchNorm1d, BatchNorm2d, Conv2d, Linear, Module, PReLU, Sequential


class Flatten(Module):
    def forward(self, value):
        return value.view(value.size(0), -1)


class ConvBlock(Module):
    def __init__(self, in_c, out_c, kernel=(1, 1), stride=(1, 1), padding=(0, 0), groups=1):
        super().__init__()
        self.conv = Conv2d(
            in_c, out_c, kernel_size=kernel, groups=groups,
            stride=stride, padding=padding, bias=False,
        )
        self.bn = BatchNorm2d(out_c)
        self.prelu = PReLU(out_c)

    def forward(self, value):
        return self.prelu(self.bn(self.conv(value)))


class LinearBlock(Module):
    def __init__(self, in_c, out_c, kernel=(1, 1), stride=(1, 1), padding=(0, 0), groups=1):
        super().__init__()
        self.conv = Conv2d(
            in_c, out_channels=out_c, kernel_size=kernel, groups=groups,
            stride=stride, padding=padding, bias=False,
        )
        self.bn = BatchNorm2d(out_c)

    def forward(self, value):
        return self.bn(self.conv(value))


class DepthWise(Module):
    def __init__(
        self, c1, c2, c3, residual=False, kernel=(3, 3),
        stride=(2, 2), padding=(1, 1), groups=1,
    ):
        super().__init__()
        c1_in, c1_out = c1
        c2_in, c2_out = c2
        c3_in, c3_out = c3
        self.conv = ConvBlock(c1_in, c1_out)
        self.conv_dw = ConvBlock(
            c2_in, c2_out, groups=c2_in, kernel=kernel,
            padding=padding, stride=stride,
        )
        self.project = LinearBlock(c3_in, c3_out)
        self.residual = residual

    def forward(self, value):
        shortcut = value
        value = self.project(self.conv_dw(self.conv(value)))
        return shortcut + value if self.residual else value


class Residual(Module):
    def __init__(self, c1, c2, c3, num_block, groups):
        super().__init__()
        self.model = Sequential(*[
            DepthWise(
                c1[index], c2[index], c3[index], residual=True,
                stride=(1, 1), padding=(1, 1), groups=groups,
            )
            for index in range(num_block)
        ])

    def forward(self, value):
        return self.model(value)


KEEP_1_8M_V2 = [
    32, 32, 103, 103, 64, 13, 13, 64, 13, 13, 64, 13, 13, 64, 13, 13,
    64, 231, 231, 128, 231, 231, 128, 52, 52, 128, 26, 26, 128, 77, 77,
    128, 26, 26, 128, 26, 26, 128, 308, 308, 128, 26, 26, 128, 26, 26,
    128, 512, 512,
]


class MiniFASNetV2(Module):
    """Upstream ``MiniFASNetV2`` with the 80x80 model defaults."""

    def __init__(self, embedding_size=128, conv6_kernel=(5, 5), drop_p=0.2, num_classes=3):
        super().__init__()
        keep = KEEP_1_8M_V2
        self.embedding_size = embedding_size
        self.conv1 = ConvBlock(3, keep[0], kernel=(3, 3), stride=(2, 2), padding=(1, 1))
        self.conv2_dw = ConvBlock(
            keep[0], keep[1], kernel=(3, 3), padding=(1, 1), groups=keep[1]
        )
        self.conv_23 = DepthWise(
            (keep[1], keep[2]), (keep[2], keep[3]), (keep[3], keep[4]),
            groups=keep[3],
        )
        self.conv_3 = self._residual(keep, (4, 7, 10, 13), 4, keep[4])
        self.conv_34 = DepthWise(
            (keep[16], keep[17]), (keep[17], keep[18]), (keep[18], keep[19]),
            groups=keep[19],
        )
        self.conv_4 = self._residual(keep, (19, 22, 25, 28, 31, 34), 6, keep[19])
        self.conv_45 = DepthWise(
            (keep[37], keep[38]), (keep[38], keep[39]), (keep[39], keep[40]),
            groups=keep[40],
        )
        self.conv_5 = self._residual(keep, (40, 43), 2, keep[40])
        self.conv_6_sep = ConvBlock(keep[46], keep[47])
        self.conv_6_dw = LinearBlock(
            keep[47], keep[48], groups=keep[48], kernel=conv6_kernel
        )
        self.conv_6_flatten = Flatten()
        self.linear = Linear(512, embedding_size, bias=False)
        self.bn = BatchNorm1d(embedding_size)
        self.drop = torch.nn.Dropout(p=drop_p)
        self.prob = Linear(embedding_size, num_classes, bias=False)

    @staticmethod
    def _residual(keep, starts, count, groups):
        c1 = [(keep[index], keep[index + 1]) for index in starts]
        c2 = [(keep[index + 1], keep[index + 2]) for index in starts]
        c3 = [(keep[index + 2], keep[index + 3]) for index in starts]
        return Residual(c1, c2, c3, count, groups)

    def forward(self, value):
        value = self.conv1(value)
        value = self.conv2_dw(value)
        value = self.conv_23(value)
        value = self.conv_3(value)
        value = self.conv_34(value)
        value = self.conv_4(value)
        value = self.conv_45(value)
        value = self.conv_5(value)
        value = self.conv_6_sep(value)
        value = self.conv_6_dw(value)
        value = self.conv_6_flatten(value)
        if self.embedding_size != 512:
            value = self.linear(value)
        return self.prob(self.drop(self.bn(value)))


class CropImage:
    """Official upstream ``CropImage`` behavior (bbox is x, y, width, height)."""

    @staticmethod
    def get_new_box(src_w, src_h, bbox, scale):
        x, y, box_w, box_h = bbox
        scale = min((src_h - 1) / box_h, min((src_w - 1) / box_w, scale))
        new_width = box_w * scale
        new_height = box_h * scale
        center_x, center_y = box_w / 2 + x, box_h / 2 + y
        left = center_x - new_width / 2
        top = center_y - new_height / 2
        right = center_x + new_width / 2
        bottom = center_y + new_height / 2
        if left < 0:
            right -= left
            left = 0
        if top < 0:
            bottom -= top
            top = 0
        if right > src_w - 1:
            left -= right - src_w + 1
            right = src_w - 1
        if bottom > src_h - 1:
            top -= bottom - src_h + 1
            bottom = src_h - 1
        return int(left), int(top), int(right), int(bottom)

    def crop(self, image, bbox, scale=2.7, out_w=80, out_h=80):
        src_h, src_w, _ = image.shape
        left, top, right, bottom = self.get_new_box(src_w, src_h, bbox, scale)
        patch = image[top:bottom + 1, left:right + 1]
        return cv2.resize(patch, (out_w, out_h)), (left, top, right, bottom)


def upstream_tensor(image: np.ndarray, bbox_xyxy, scale=2.7):
    """Apply upstream CropImage + ToTensor while preserving OpenCV BGR order."""
    x1, y1, x2, y2 = bbox_xyxy
    bbox_xywh = (x1, y1, x2 - x1, y2 - y1)
    resized, coordinates = CropImage().crop(image, bbox_xywh, scale=scale)
    tensor = resized.astype(np.float32) / np.float32(255.0)
    tensor = np.ascontiguousarray(tensor.transpose(2, 0, 1)[None, ...])
    return tensor, coordinates
