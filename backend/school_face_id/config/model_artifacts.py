"""Reusable validation for locally supplied model artifacts."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


class ModelArtifactError(RuntimeError):
    """A configured model artifact is missing, unreadable, or untrusted."""


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    size_bytes: int
    sha256: str


def is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file without loading the complete artifact into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_model_artifact(path_value: str, expected_sha256: str, setting_name: str) -> ModelArtifact:
    """Validate existence, readability, non-empty size, and configured SHA-256."""
    path = Path(path_value)
    if not path.exists():
        raise ModelArtifactError(f"{setting_name} does not exist")
    if not path.is_file():
        raise ModelArtifactError(f"{setting_name} is not a file")
    if not os.access(path, os.R_OK):
        raise ModelArtifactError(f"{setting_name} is not readable")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ModelArtifactError(f"{setting_name} cannot be inspected: {exc}") from exc
    if size <= 0:
        raise ModelArtifactError(f"{setting_name} is empty")
    expected = expected_sha256.strip().lower()
    if not is_sha256(expected):
        raise ModelArtifactError(f"{setting_name}_SHA256 must be a 64-character hexadecimal digest")
    try:
        actual = sha256_file(path)
    except OSError as exc:
        raise ModelArtifactError(f"{setting_name} is not readable: {exc}") from exc
    if actual != expected:
        raise ModelArtifactError(f"{setting_name}_SHA256 mismatch")
    return ModelArtifact(size_bytes=size, sha256=actual)
