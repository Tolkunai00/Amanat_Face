"""Official FS-VFM FAS evaluation preprocessing and Amanat bbox adaptation."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class PreprocessProfile:
    input_size: int = 224
    color_order: str = "RGB"
    mean: tuple[float, float, float] = (
        0.5482207536697388, 0.42340534925460815, 0.3654651641845703,
    )
    std: tuple[float, float, float] = (
        0.2789176106452942, 0.2438540756702423, 0.23493893444538116,
    )
    # Official preprocess_MCIO.py passes padding=0.37 to landmark-aligned
    # extract_image_chips.  For a bbox-only evaluator this corresponds to a
    # total square extent of 1 + 2 * padding.
    face_expansion: float = 1.74
    resize_strategy: str = "direct PIL resize to 224x224 (no center crop)"


OFFICIAL_PROFILE = PreprocessProfile()


def load_profile(path: Path | None):
    if path is None:
        return OFFICIAL_PROFILE
    data = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    allowed = {"mean", "std", "input_size", "face_expansion"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unsupported preprocessing config keys: {sorted(unknown)}")
    values = {
        "mean": tuple(data.get("mean", OFFICIAL_PROFILE.mean)),
        "std": tuple(data.get("std", OFFICIAL_PROFILE.std)),
        "input_size": int(data.get("input_size", OFFICIAL_PROFILE.input_size)),
        "face_expansion": float(data.get("face_expansion", OFFICIAL_PROFILE.face_expansion)),
    }
    if len(values["mean"]) != 3 or len(values["std"]) != 3:
        raise ValueError("mean and std must contain three RGB values")
    if values["input_size"] != 224:
        raise ValueError("Official ViT-S/16 FAS checkpoint requires input_size=224")
    if values["face_expansion"] <= 0:
        raise ValueError("face_expansion must be positive")
    return PreprocessProfile(**values)


def expanded_square_crop(image, bbox_xyxy, expansion):
    height, width = image.shape[:2]
    x1, y1, x2, y2 = [float(value) for value in bbox_xyxy]
    box_width, box_height = x2 - x1, y2 - y1
    if box_width <= 0 or box_height <= 0:
        raise ValueError("Face bbox has no area")
    side = max(box_width, box_height) * expansion
    center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
    left = max(0, int(np.floor(center_x - side / 2)))
    top = max(0, int(np.floor(center_y - side / 2)))
    right = min(width, int(np.ceil(center_x + side / 2)))
    bottom = min(height, int(np.ceil(center_y + side / 2)))
    if right <= left or bottom <= top:
        raise ValueError("Expanded face crop is empty")
    return image[top:bottom, left:right], (left, top, right, bottom)


def preprocess_face(image_bgr, bbox_xyxy, profile=OFFICIAL_PROFILE):
    """Crop a detected face, then reproduce official evaluation transforms.

    Official MCIO files are landmark-aligned face chips with padding=0.37.
    Under the evaluator's bbox-only constraint, the equivalent 1.74 square
    extent is used before the exact RGB/PIL/ToTensor/Normalize path.
    """
    crop, coordinates = expanded_square_crop(image_bgr, bbox_xyxy, profile.face_expansion)
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb.astype(np.uint8)).resize(
        (profile.input_size, profile.input_size), resample=Image.Resampling.BICUBIC
    )
    array = np.asarray(pil, dtype=np.float32) / np.float32(255.0)
    array = (array - np.asarray(profile.mean, dtype=np.float32)) / np.asarray(
        profile.std, dtype=np.float32
    )
    tensor = np.ascontiguousarray(array.transpose(2, 0, 1)[None, ...])
    return tensor, coordinates
