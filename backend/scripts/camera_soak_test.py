"""Headless RTSP/USB camera soak diagnostic; never writes attendance."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import platform
from pathlib import Path
import statistics
import sys
import time
import tracemalloc
import zlib

import cv2
import numpy as np


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def duration_seconds(value: str) -> float:
    value = value.strip().lower()
    multiplier = 1
    if value.endswith("m"):
        multiplier, value = 60, value[:-1]
    elif value.endswith("h"):
        multiplier, value = 3600, value[:-1]
    seconds = float(value) * multiplier
    if seconds <= 0:
        raise argparse.ArgumentTypeError("duration must be greater than zero")
    return seconds


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent / 100.0
    low, high = int(index), min(int(index) + 1, len(ordered) - 1)
    fraction = index - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def camera_source(value):
    try:
        return int(value)
    except ValueError:
        return value


def fingerprint(frame):
    height, width = frame.shape[:2]
    sampled = frame[::max(1, height // 16), ::max(1, width // 16)]
    return zlib.adler32(np.ascontiguousarray(sampled).tobytes())


def open_capture(source):
    params = []
    if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
        params.extend((cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000))
    if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
        params.extend((cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000))
    if isinstance(source, str) and source.lower().startswith("rtsp://"):
        capture = cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)
    else:
        capture = cv2.VideoCapture(source)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return capture


def process_memory_bytes():
    try:
        import psutil  # optional operator dependency
        return psutil.Process().memory_info().rss
    except (ImportError, OSError):
        return None


def run_pipeline(frame):
    """Diagnostic detection/PAD only; deliberately bypasses attendance services."""
    from school_face_id.recognition.anti_spoof import evaluate_anti_spoof
    from school_face_id.recognition.camera_service import detect_faces, resize_for_recognition

    started = time.perf_counter()
    small, _, _ = resize_for_recognition(frame)
    detection = detect_faces(small)
    pad_latencies = []
    for face in detection.faces:
        result = evaluate_anti_spoof(small, tuple(face.bbox.astype(int)))
        if result.inference_ms is not None:
            pad_latencies.append(result.inference_ms)
    return (time.perf_counter() - started) * 1000.0, pad_latencies


def run(args):
    source = camera_source(args.camera)
    started_wall = time.time()
    started_cpu = time.process_time()
    deadline = time.monotonic() + args.duration
    start_iso = datetime.now(timezone.utc).isoformat()
    attempted = successful = failed = reconnects = stale_events = 0
    offline_started = time.monotonic()
    offline_seconds = 0.0
    capture = None
    opened_once = False
    latencies = []
    intervals = []
    pipeline_latencies = []
    pad_latencies = []
    last_success = None
    last_fingerprint = None
    identical = 0
    next_sample = time.monotonic()
    memory_samples = []
    tracemalloc.start()

    while time.monotonic() < deadline:
        if capture is None or not capture.isOpened():
            if capture is not None:
                capture.release()
            capture = open_capture(source)
            if opened_once:
                reconnects += 1
            if not capture.isOpened():
                failed += 1
                time.sleep(min(args.sample_interval, 1.0))
                continue
            opened_once = True
            if offline_started is not None:
                offline_seconds += time.monotonic() - offline_started
                offline_started = None

        attempted += 1
        read_started = time.perf_counter()
        ok, frame = capture.read()
        read_ms = (time.perf_counter() - read_started) * 1000.0
        latencies.append(read_ms)
        if not ok or frame is None:
            failed += 1
            capture.release()
            capture = None
            offline_started = offline_started or time.monotonic()
            continue

        current_fingerprint = fingerprint(frame)
        identical = identical + 1 if current_fingerprint == last_fingerprint else 0
        last_fingerprint = current_fingerprint
        if identical >= args.stale_frames:
            stale_events += 1
            failed += 1
            capture.release()
            capture = None
            offline_started = offline_started or time.monotonic()
            identical = 0
            continue

        now = time.monotonic()
        successful += 1
        if last_success is not None:
            intervals.append(now - last_success)
        last_success = now
        if args.pipeline:
            pipeline_ms, current_pad = run_pipeline(frame)
            pipeline_latencies.append(pipeline_ms)
            pad_latencies.extend(current_pad)
        if now >= next_sample:
            memory_samples.append(process_memory_bytes())
            next_sample = now + args.sample_interval

    if capture is not None:
        capture.release()
    if offline_started is not None:
        offline_seconds += time.monotonic() - offline_started
    _, peak_python_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    elapsed = max(time.time() - started_wall, 1e-9)
    failure_rate = failed / max(attempted, 1)
    report = {
        "start_utc": start_iso,
        "end_utc": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": elapsed,
        "camera_kind": "rtsp" if isinstance(source, str) else "local_device",
        "frames_attempted": attempted,
        "frames_successful": successful,
        "frames_failed": failed,
        "failure_rate": failure_rate,
        "reconnects": reconnects,
        "stale_events": stale_events,
        "offline_seconds": offline_seconds,
        "avg_frame_interval_seconds": statistics.fmean(intervals) if intervals else None,
        "read_latency_ms": {
            "average": statistics.fmean(latencies) if latencies else None,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
        },
        "pipeline_latency_ms": {
            "average": statistics.fmean(pipeline_latencies) if pipeline_latencies else None,
            "p95": percentile(pipeline_latencies, 95),
        },
        "pad_latency_ms": {
            "average": statistics.fmean(pad_latencies) if pad_latencies else None,
            "p95": percentile(pad_latencies, 95),
        },
        "process_rss_bytes": next((value for value in reversed(memory_samples) if value), None),
        "python_peak_memory_bytes": peak_python_memory,
        "cpu_percent_single_core": (time.process_time() - started_cpu) / elapsed * 100.0,
        "criteria": {
            "max_failure_rate": args.max_failure_rate,
            "max_reconnects": args.max_reconnects,
            "max_offline_seconds": args.max_offline_seconds,
            "max_p95_latency_ms": args.max_p95_latency,
        },
    }
    p95 = report["read_latency_ms"]["p95"]
    report["passed"] = bool(
        successful > 0
        and failure_rate <= args.max_failure_rate
        and reconnects <= args.max_reconnects
        and offline_seconds <= args.max_offline_seconds
        and p95 is not None
        and p95 <= args.max_p95_latency
    )
    if args.device_info:
        report["device_info"] = {
            "platform": platform.platform(),
            "opencv": cv2.__version__,
        }
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.csv:
        with Path(args.csv).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=(
                "start_utc", "end_utc", "duration_seconds", "frames_attempted",
                "frames_successful", "frames_failed", "failure_rate", "reconnects",
                "stale_events", "offline_seconds", "passed",
            ))
            writer.writeheader()
            writer.writerow({key: report[key] for key in writer.fieldnames})
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--camera", required=True, help="RTSP URL or local device index")
    result.add_argument("--duration", type=duration_seconds, default=duration_seconds("5m"))
    result.add_argument("--report", required=True)
    result.add_argument("--csv")
    result.add_argument("--sample-interval", type=float, default=5.0)
    result.add_argument("--device-info", action="store_true")
    result.add_argument("--pipeline", action="store_true")
    result.add_argument("--stale-frames", type=int, default=30)
    result.add_argument("--max-failure-rate", type=float, default=0.05)
    result.add_argument("--max-reconnects", type=int, default=10)
    result.add_argument("--max-offline-seconds", type=float, default=30.0)
    result.add_argument("--max-p95-latency", type=float, default=1000.0)
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
