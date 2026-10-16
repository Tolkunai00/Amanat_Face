"""Side-effect-free camera capture helpers for biometric validation datasets.

This module deliberately imports only the camera/detection layer.  It never
calls recognition, enrollment, database, or attendance services.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import time
import uuid

import cv2
import numpy as np


SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def validate_component(value: str, label: str) -> str:
    value = str(value).strip()
    if not SAFE_COMPONENT.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"{label} must be a safe path component")
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_camera_source(value: str):
    """Match the runtime convention: integers are local devices, strings URLs."""
    value = str(value).strip()
    try:
        result = int(value)
    except ValueError:
        return value
    if result < 0:
        raise ValueError("camera index must be zero or greater")
    return result


def camera_identifier(source: str, explicit: str | None = None) -> str:
    """Return a safe stable label without persisting camera URLs/credentials."""
    if explicit:
        return validate_component(explicit, "camera-id")
    parsed = parse_camera_source(source)
    if isinstance(parsed, int):
        return f"camera_{parsed}"
    digest = hashlib.sha256(str(parsed).encode("utf-8")).hexdigest()[:12]
    return f"camera_{digest}"


def open_camera(camera_id: str, source):
    """Open via the existing camera stack without starting its worker thread."""
    from school_face_id.recognition.camera_service import CameraWorker

    return CameraWorker(camera_id, source)._open_camera()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FrameQuality:
    accepted: bool
    reason: str
    faces: int
    blur_score: float | None
    brightness: float | None
    bbox: tuple[int, int, int, int] | None

    def as_dict(self):
        return {
            "accepted": self.accepted, "reason": self.reason, "faces": self.faces,
            "blur_score": self.blur_score, "brightness": self.brightness,
            "bbox": list(self.bbox) if self.bbox else None,
        }


def assess_frame(frame: np.ndarray, faces, *, min_blur=20.0, min_brightness=8.0) -> FrameQuality:
    """Reject only unusable input; identity similarity is intentionally absent."""
    faces = tuple(faces)
    if not faces:
        return FrameQuality(False, "no_face", 0, None, None, None)
    if len(faces) != 1:
        return FrameQuality(False, "multiple_faces", len(faces), None, None, None)
    height, width = frame.shape[:2]
    raw = getattr(faces[0], "bbox", faces[0])
    x1, y1, x2, y2 = (int(value) for value in raw)
    x1, x2 = sorted((max(0, x1), min(width, x2)))
    y1, y2 = sorted((max(0, y1), min(height, y2)))
    if x2 <= x1 or y2 <= y1:
        return FrameQuality(False, "invalid_face_box", 1, None, None, None)
    crop = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(np.mean(gray))
    bbox = (x1, y1, x2, y2)
    if brightness < min_brightness:
        return FrameQuality(False, "unusable_darkness", 1, blur, brightness, bbox)
    if blur < min_blur:
        return FrameQuality(False, "severe_blur", 1, blur, brightness, bbox)
    return FrameQuality(True, "accepted", 1, blur, brightness, bbox)


def detect_for_capture(frame):
    """Use the runtime's accurate face detector, without identity or PAD inference."""
    from school_face_id.recognition.camera_service import analyze_faces

    return analyze_faces(frame, "accurate")


def encode_jpeg(frame: np.ndarray, quality=95) -> bytes:
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return encoded.tobytes()


def existing_hashes(root: Path) -> dict[str, Path]:
    result = {}
    if not root.exists():
        return result
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
            result.setdefault(sha256_file(path), path)
    return result


def save_unique_jpeg(directory: Path, frame: np.ndarray, known_hashes: dict[str, Path], sequence: int):
    payload = encode_jpeg(frame)
    digest = sha256_bytes(payload)
    if digest in known_hashes:
        return None, digest, known_hashes[digest]
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    path = directory / f"{stamp}_{sequence:06d}.jpg"
    path.write_bytes(payload)
    known_hashes[digest] = path
    return path, digest, None


def capture_loop(
    *, camera_id: str, source, destination: Path, hash_root: Path, on_saved, sample_interval: float,
    max_images: int, min_blur: float, min_brightness: float, preview: bool,
    detector=detect_for_capture,
):
    """Capture valid, sampled frames. `on_saved` persists dataset metadata."""
    if sample_interval <= 0:
        raise ValueError("sample interval must be greater than zero")
    capture = open_camera(camera_id, source)
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"unable to open camera {camera_id!r}")
    hashes = existing_hashes(hash_root)
    saved = sequence = 0
    last_sample = 0.0
    try:
        while max_images <= 0 or saved < max_images:
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError("camera read failed")
            quality = assess_frame(
                frame, detector(frame), min_blur=min_blur, min_brightness=min_brightness,
            )
            now = time.monotonic()
            if now - last_sample >= sample_interval:
                last_sample = now
                print({"capture_time": utc_now(), "camera_id": camera_id, **quality.as_dict()})
                if quality.accepted:
                    sequence += 1
                    path, digest, duplicate = save_unique_jpeg(destination, frame, hashes, sequence)
                    if path is None:
                        print({"rejected": "exact_duplicate", "duplicate_of": str(duplicate), "sha256": digest})
                    else:
                        on_saved(path, digest, quality)
                        saved += 1
            if preview:
                display = frame.copy()
                color = (0, 180, 0) if quality.accepted else (0, 0, 255)
                if quality.bbox:
                    x1, y1, x2, y2 = quality.bbox
                    cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)
                cv2.putText(display, quality.reason, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, .7, color, 2)
                cv2.imshow(f"validation capture: {camera_id}", display)
                if cv2.waitKey(1) & 0xFF in {ord("q"), 27}:
                    break
    finally:
        capture.release()
        if preview:
            cv2.destroyAllWindows()
    return saved


def new_burst_id() -> str:
    return f"burst-{uuid.uuid4()}"
