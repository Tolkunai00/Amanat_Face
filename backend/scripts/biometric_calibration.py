"""Pure helpers shared by the read-only biometric calibration commands."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

import numpy as np


COPY_SUFFIX = re.compile(r"(?i)(?:\s*\(\d+\)|[-_ ]copy(?:[-_ ]?\d+)?)$")


def normalized_filename(path: Path) -> str:
    """Collapse common copied-file names such as ``photo (1).jpg``."""
    return COPY_SUFFIX.sub("", path.stem).casefold() + path.suffix.casefold()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def filter_duplicates(paths, filename_scope="global"):
    """Return stable unique files and auditable exact/name-copy removals."""
    paths = sorted(Path(path) for path in paths)
    unique, removed = [], []
    seen_hashes, seen_names = {}, {}
    for path in paths:
        digest = file_sha256(path)
        normalized = normalized_filename(path)
        name_key = (str(path.parent.resolve()), normalized) if filename_scope == "directory" else normalized
        duplicate_of = seen_hashes.get(digest)
        reason = "exact_sha256" if duplicate_of else ""
        if duplicate_of is None:
            duplicate_of = seen_names.get(name_key)
            reason = "filename_copy" if duplicate_of else ""
        if duplicate_of is not None:
            removed.append({
                "file": str(path), "duplicate_of": str(duplicate_of),
                "reason": reason, "sha256": digest,
            })
            continue
        seen_hashes[digest] = path
        seen_names[name_key] = path
        unique.append(path)
    return unique, {
        "raw": len(paths), "unique": len(unique), "removed": len(removed),
        "removed_files": removed,
    }


def distribution(values):
    values = np.asarray(list(values), dtype=np.float64)
    if values.size == 0:
        return {key: None for key in ("count", "mean", "min", "max", "p05", "p50", "p95")} | {"count": 0}
    return {
        "count": int(values.size), "mean": float(np.mean(values)),
        "min": float(np.min(values)), "max": float(np.max(values)),
        "p05": float(np.percentile(values, 5)),
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
    }


def error_rates(genuine, impostor, threshold):
    genuine, impostor = list(genuine), list(impostor)
    far = sum(score >= threshold for score in impostor) / len(impostor) if impostor else None
    frr = sum(score < threshold for score in genuine) / len(genuine) if genuine else None
    return far, frr


def identity_threshold_sweep(genuine, impostor, thresholds=None):
    genuine, impostor = list(genuine), list(impostor)
    thresholds = thresholds if thresholds is not None else np.linspace(-1.0, 1.0, 401)
    rows = []
    for threshold in thresholds:
        far, frr = error_rates(genuine, impostor, float(threshold))
        rows.append({
            "threshold": float(threshold), "FAR": far, "FRR": frr,
            "TPR": None if frr is None else 1.0 - frr,
            "FPR": far,
        })
    return rows


def approximate_eer(rows, false_accept_key="FAR", false_reject_key="FRR"):
    eligible = [row for row in rows if row.get(false_accept_key) is not None and row.get(false_reject_key) is not None]
    if not eligible:
        return None, None
    selected = min(
        eligible,
        key=lambda row: (abs(row[false_accept_key] - row[false_reject_key]),
                         row[false_accept_key] + row[false_reject_key]),
    )
    return (
        float((selected[false_accept_key] + selected[false_reject_key]) / 2.0),
        float(selected["threshold"]),
    )


def pad_rates(live_scores, spoof_scores, threshold):
    live_scores, spoof_scores = list(live_scores), list(spoof_scores)
    apcer = sum(score >= threshold for score in spoof_scores) / len(spoof_scores) if spoof_scores else None
    bpcer = sum(score < threshold for score in live_scores) / len(live_scores) if live_scores else None
    acer = (apcer + bpcer) / 2.0 if apcer is not None and bpcer is not None else None
    return apcer, bpcer, acer


def pad_threshold_sweep(live_scores, spoof_scores, thresholds=None):
    thresholds = thresholds if thresholds is not None else np.linspace(0.0, 1.0, 201)
    return [
        {"threshold": float(value), **dict(zip(("APCER", "BPCER", "ACER"), pad_rates(live_scores, spoof_scores, float(value))))}
        for value in thresholds
    ]
