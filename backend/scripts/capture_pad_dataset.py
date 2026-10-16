"""Capture standardized live and presentation-attack PAD evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dataset_capture_common import (  # noqa: E402
    camera_identifier, capture_loop, parse_camera_source, utc_now, validate_component,
)


ATTACK_TYPES = ("phone_photo", "phone_video", "laptop_photo", "laptop_video", "print")
CONDITIONS = ("front", "left", "right", "far", "low_light")


def live_capture_path(output: Path, person_id: str, camera_id: str, session_id: str, condition: str) -> Path:
    return (
        output / "live" / validate_component(person_id, "person-id")
        / validate_component(camera_id, "camera-id")
        / validate_component(session_id, "session-id") / condition
    )


def attack_capture_path(output: Path, attack_type: str, session_id: str) -> Path:
    if attack_type not in ATTACK_TYPES:
        raise ValueError(f"unsupported attack type: {attack_type}")
    return output / "spoof" / attack_type / validate_component(session_id, "attack-session-id")


def attack_metadata(
    *, attack_session_id: str, attack_type: str, source_identity: str,
    camera_id: str, display_or_print_device: str, notes: str | None = None,
    device_type: str | None = None, screen_brightness: str | None = None,
    paper_type: str | None = None, print_size: str | None = None,
):
    """Return explicit facts only; unknown optional properties remain null."""
    return {
        "attack_session_id": attack_session_id,
        "attack_type": attack_type,
        "source_identity": source_identity,
        "camera_id": camera_id,
        "capture_time": utc_now(),
        "display_or_print_device": display_or_print_device,
        "notes": notes or None,
        "device_type": device_type or None,
        "screen_brightness": screen_brightness or None,
        "paper_type": paper_type or None,
        "print_size": print_size or None,
    }


def write_session_metadata(session_dir: Path, metadata: dict) -> Path:
    path = session_dir / "metadata.json"
    session_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        stable = ("attack_session_id", "attack_type", "source_identity", "camera_id", "display_or_print_device")
        mismatch = [key for key in stable if existing.get(key) != metadata.get(key)]
        if mismatch:
            raise ValueError("existing attack metadata conflicts on: " + ", ".join(mismatch))
        return path
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", required=True, help="Local index or RTSP/HTTP camera source")
    parser.add_argument("--camera-id", help="Stable camera label; defaults to --camera")
    parser.add_argument("--label", required=True, choices=("live", "spoof"))
    parser.add_argument("--person-id", help="Required for live captures")
    parser.add_argument("--attack-type", choices=ATTACK_TYPES, help="Required for spoof captures")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--condition", choices=CONDITIONS, help="Required for live captures")
    parser.add_argument("--source-identity", help="Required for spoof captures")
    parser.add_argument("--display-or-print-device", help="Stable physical device ID; required for spoof")
    parser.add_argument("--device-type")
    parser.add_argument("--screen-brightness", help="Manual value only; omit when unknown")
    parser.add_argument("--paper-type", help="Manual value only; omit when unknown")
    parser.add_argument("--print-size", help="Manual value only; omit when unknown")
    parser.add_argument("--notes")
    parser.add_argument("--split", choices=("development", "test"))
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "pad_validation")
    parser.add_argument("--sample-interval", type=float, default=1.0,
                        help="Seconds between saved samples; frames remain one attack-session evidence group")
    parser.add_argument("--max-images", type=int, default=30, help="0 means until operator quits")
    parser.add_argument("--min-blur", type=float, default=20.0)
    parser.add_argument("--min-brightness", type=float, default=8.0)
    parser.add_argument("--no-preview", action="store_true")
    args = parser.parse_args(argv)

    root = args.output / args.split if args.split else args.output
    camera_id = camera_identifier(args.camera, args.camera_id)
    session_id = validate_component(args.session_id, "session-id")
    if args.label == "live":
        if not args.person_id or not args.condition:
            parser.error("live capture requires --person-id and --condition")
        destination = live_capture_path(root, args.person_id, camera_id, session_id, args.condition)
    else:
        if not args.attack_type or not args.source_identity or not args.display_or_print_device:
            parser.error("spoof capture requires --attack-type, --source-identity and --display-or-print-device")
        source_identity = validate_component(args.source_identity, "source-identity")
        destination = attack_capture_path(root, args.attack_type, session_id)
        metadata = attack_metadata(
            attack_session_id=session_id, attack_type=args.attack_type,
            source_identity=source_identity, camera_id=camera_id,
            display_or_print_device=args.display_or_print_device, notes=args.notes,
            device_type=args.device_type, screen_brightness=args.screen_brightness,
            paper_type=args.paper_type, print_size=args.print_size,
        )
        write_session_metadata(destination, metadata)

    frame_metadata = destination / "frames.jsonl"
    def saved(path, digest, quality):
        row = {
            "file": path.name, "sha256": digest, "capture_time": utc_now(),
            "camera_id": camera_id, "session_id": session_id,
            "blur_score": quality.blur_score, "brightness": quality.brightness,
        }
        with frame_metadata.open("a", encoding="utf-8") as target:
            target.write(json.dumps(row, ensure_ascii=False) + "\n")

    count = capture_loop(
        camera_id=camera_id, source=parse_camera_source(args.camera), destination=destination,
        hash_root=args.output, on_saved=saved, sample_interval=args.sample_interval,
        max_images=args.max_images, min_blur=args.min_blur,
        min_brightness=args.min_brightness, preview=not args.no_preview,
    )
    print({"saved": count, "destination": str(destination), "evidence_group": session_id})
    return 0 if count else 2


if __name__ == "__main__":
    raise SystemExit(main())
