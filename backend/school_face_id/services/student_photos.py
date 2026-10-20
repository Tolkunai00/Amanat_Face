"""Decode uploaded photos and normalize them for storage and face recognition."""

import warnings

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


class InvalidPhotoError(ValueError):
    pass


def normalize_photo(file):
    """Inspect file contents, orient the first frame, and fit large photos."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(file) as source:
                # JPEG can decode at reduced resolution before allocating pixels.
                source.draft("RGB", (2048, 2048))
                photo = ImageOps.exif_transpose(source)
                photo.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                # Composite transparent PNG/WebP/GIF photos on a white background.
                if photo.mode in ("RGBA", "LA") or "transparency" in photo.info:
                    rgba = photo.convert("RGBA")
                    rgb = Image.new("RGB", rgba.size, "white")
                    rgb.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    rgb = photo.convert("RGB")
                return cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidPhotoError("Фото имеет слишком большое разрешение для обработки") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise InvalidPhotoError(
            "Не удалось прочитать фото. Выберите изображение JPEG, PNG, WebP, BMP, TIFF или GIF."
        ) from exc
