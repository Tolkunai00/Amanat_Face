"""Capture a gallery or probe session without enrollment/attendance side effects."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dataset_capture_common import (  # noqa: E402
    camera_identifier, capture_loop, new_burst_id, parse_camera_source, utc_now, validate_component,
)


CONDITIONS = ("front", "left", "right", "far", "low_light")
FIELDS = (
    "file", "person_id", "role", "session_id", "condition", "camera_id",
    "capture_time", "sha256", "burst_sequence_id", "blur_score", "brightness",
)


def capture_path(output: Path, person_id: str, role: str, session_id: str, condition: str) -> Path:
    return output / validate_component(person_id, "person-id") / role / validate_component(session_id, "session-id") / condition


def append_metadata(output: Path, row: dict):
    path = output / "capture_metadata.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", required=True, help="Local index or RTSP/HTTP camera source")
    parser.add_argument("--camera-id", help="Stable camera label; defaults to --camera")
    parser.add_argument("--person-id", required=True)
    parser.add_argument("--role", required=True, choices=("gallery", "probe"))
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--condition", required=True, choices=CONDITIONS)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "recognition_validation")
    parser.add_argument("--sample-interval", type=float, default=1.0)
    parser.add_argument("--max-images", type=int, default=30, help="0 means until operator quits")
    parser.add_argument("--min-blur", type=float, default=20.0)
    parser.add_argument("--min-brightness", type=float, default=8.0)
    parser.add_argument("--no-preview", action="store_true")
    args = parser.parse_args(argv)
    camera_id = camera_identifier(args.camera, args.camera_id)
    person = validate_component(args.person_id, "person-id")
    session = validate_component(args.session_id, "session-id")
    destination = capture_path(args.output, person, args.role, session, args.condition)
    burst_id = new_burst_id()

    def saved(path, digest, quality):
        append_metadata(args.output, {
            "file": path.relative_to(args.output).as_posix(), "person_id": person,
            "role": args.role, "session_id": session, "condition": args.condition,
            "camera_id": camera_id, "capture_time": utc_now(), "sha256": digest,
            "burst_sequence_id": burst_id, "blur_score": quality.blur_score,
            "brightness": quality.brightness,
        })

    count = capture_loop(
        camera_id=camera_id, source=parse_camera_source(args.camera), destination=destination,
        hash_root=args.output,
        on_saved=saved, sample_interval=args.sample_interval, max_images=args.max_images,
        min_blur=args.min_blur, min_brightness=args.min_brightness,
        preview=not args.no_preview,
    )
    print({"saved": count, "destination": str(destination), "burst_sequence_id": burst_id})
    return 0 if count else 2


if __name__ == "__main__":
    raise SystemExit(main())
