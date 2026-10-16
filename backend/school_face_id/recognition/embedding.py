"""Canonical InsightFace embedding extraction helpers."""

from __future__ import annotations

import numpy as np


EMBEDDING_SIZE = 512


class InvalidEmbeddingError(ValueError):
    pass


def normalize_embedding(embedding) -> np.ndarray:
    vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
    if vector.size != EMBEDDING_SIZE or not np.isfinite(vector).all():
        raise InvalidEmbeddingError("Face embedding has an invalid shape or values")
    norm = float(np.linalg.norm(vector))
    if norm < 1e-6:
        raise InvalidEmbeddingError("Face embedding is empty")
    return (vector / norm).astype(np.float32, copy=False)


def embedding_from_face(face) -> np.ndarray:
    """Use InsightFace's aligned embedding and enforce one normalized space."""
    return normalize_embedding(getattr(face, "embedding", None))


def select_largest_face(faces):
    return max(
        faces,
        key=lambda face: max(0.0, float(face.bbox[2] - face.bbox[0]))
        * max(0.0, float(face.bbox[3] - face.bbox[1])),
    )


def extract_embedding(image, app, require_single: bool = True) -> np.ndarray | None:
    """Detect, select and normalize with the same operations used at runtime."""
    if image is None:
        return None
    faces = app.get(image)
    if not faces:
        return None
    if require_single and len(faces) != 1:
        raise ValueError("Image must contain exactly one face")
    face = faces[0] if require_single else select_largest_face(faces)
    return embedding_from_face(face)
