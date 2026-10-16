"""Dry-run recognition load diagnostic that never creates attendance."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import time
import tracemalloc

import cv2
import numpy as np


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from school_face_id.recognition.embedding import embedding_from_face
from school_face_id.recognition.matcher import match_identity_details
from school_face_id.config.settings import RECOGNITION_MARGIN, RECOGNITION_THRESHOLD


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent / 100.0
    low, high = int(index), min(int(index) + 1, len(ordered) - 1)
    part = index - low
    return ordered[low] * (1 - part) + ordered[high] * part


def synthetic_job(faces, gallery):
    gallery_size = len(gallery)
    student_ids = [f"synthetic-{index}" for index in range(gallery_size)]
    started = time.perf_counter()
    unknown = 0
    for index in range(faces):
        query = gallery[index % gallery_size]
        result = match_identity_details(
            query, student_ids, student_ids, [""] * gallery_size, [""] * gallery_size,
            gallery, threshold=RECOGNITION_THRESHOLD, margin=RECOGNITION_MARGIN,
        )
        unknown += int(not result["matched"])
    return (time.perf_counter() - started) * 1000.0, faces, unknown


def replay_job(path):
    from school_face_id.recognition.camera_service import detect_faces, resize_for_recognition
    from school_face_id.repositories.recognition import load_recognition_embeddings

    frame = cv2.imread(str(path))
    if frame is None:
        raise ValueError("image is unreadable")
    gallery = load_recognition_embeddings()
    started = time.perf_counter()
    small, _, _ = resize_for_recognition(frame)
    faces = detect_faces(small).faces
    unknown = 0
    for face in faces:
        embedding = embedding_from_face(face)
        match = match_identity_details(
            embedding, gallery["student_ids"], gallery["names"], gallery["parent_ids"],
            gallery["classes"], gallery["embeddings"],
            RECOGNITION_THRESHOLD, RECOGNITION_MARGIN,
        )
        unknown += int(not match["matched"])
    return (time.perf_counter() - started) * 1000.0, len(faces), unknown


def run(args):
    if args.mode == "replay":
        if not args.input:
            raise SystemExit("--input is required in replay mode")
        paths = [path for path in Path(args.input).rglob("*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"}]
        if not paths:
            raise SystemExit("no replay images found")
        jobs = [paths[index % len(paths)] for index in range(args.iterations)]
        callback = replay_job
    else:
        jobs = [None] * args.iterations
        random = np.random.default_rng(20261007)
        gallery = random.normal(size=(args.gallery_size, 512)).astype(np.float32)
        gallery /= np.maximum(np.linalg.norm(gallery, axis=1, keepdims=True), 1e-6)
        callback = lambda _unused: synthetic_job(args.synthetic_faces, gallery)

    started_utc = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    tracemalloc.start()
    latencies, processed, unknown, errors = [], 0, 0, 0
    with ThreadPoolExecutor(max_workers=args.streams) as pool:
        futures = [pool.submit(callback, job) for job in jobs]
        for future in futures:
            try:
                latency, count, unknown_count = future.result()
                latencies.append(latency)
                processed += count
                unknown += unknown_count
            except Exception:
                errors += 1
    current_memory, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    elapsed = max(time.perf_counter() - started, 1e-9)
    report = {
        "mode": args.mode,
        "dry_run": True,
        "attendance_writes": False,
        "started_utc": started_utc,
        "ended_utc": datetime.now(timezone.utc).isoformat(),
        "streams": args.streams,
        "requests_processed": len(latencies),
        "faces_processed": processed,
        "unknown_decisions": unknown,
        "errors": errors,
        "elapsed_seconds": elapsed,
        "throughput_faces_per_second": processed / elapsed,
        "latency_ms": {
            "average": statistics.fmean(latencies) if latencies else None,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
        },
        "python_memory_growth_bytes": current_memory,
        "python_peak_memory_bytes": peak_memory,
        "warning": "Synthetic mode measures matcher concurrency only; it is not camera FPS capacity.",
    }
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if errors == 0 and processed > 0 else 1


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--mode", choices=("synthetic", "replay"), default="synthetic")
    result.add_argument("--input")
    result.add_argument("--streams", type=int, default=1)
    result.add_argument("--iterations", type=int, default=100)
    result.add_argument("--synthetic-faces", type=int, default=1)
    result.add_argument("--gallery-size", type=int, default=64)
    result.add_argument("--report", required=True)
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
