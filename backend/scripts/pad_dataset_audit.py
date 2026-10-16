"""Audit PAD attack-session coverage without decoding biometric media."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re


ATTACK_TYPES = ("phone_photo", "phone_video", "laptop_photo", "laptop_video", "print")
CONDITIONS = ("front", "left", "right", "far", "low_light")
MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".mp4", ".mov", ".avi", ".mkv"}
REQUIRED_SESSION_FIELDS = (
    "attack_session_id", "attack_type", "source_identity", "camera_id",
    "capture_time", "display_or_print_device",
)
COPY_SUFFIX = re.compile(r"(?i)(?:\s*\(\d+\)|[-_ ]copy(?:[-_ ]?\d+)?)$")


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _layout(root: Path, path: Path):
    parts = path.relative_to(root).parts
    offset = 1 if parts and parts[0] in {"development", "test"} else 0
    split = parts[0] if offset else "unspecified"
    label = parts[offset] if len(parts) > offset else ""
    return parts, offset, split, label


def audit_dataset(root: Path):
    media = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES)
    coverage = {
        attack: {
            "status": "NOT_TESTED", "sessions": 0, "unique_identities": 0,
            "unique_devices": 0, "unique_cameras": 0, "frames_images": 0,
            "duplicates": 0,
        }
        for attack in ATTACK_TYPES
    }
    sessions, violations = {}, []
    seen_hash, seen_name = {}, {}
    duplicate_count = defaultdict(int)
    exact_duplicates = filename_copies = 0
    live_sessions = set()
    live_conditions = set()
    split_keys = defaultdict(set)
    for path in media:
        relative = path.relative_to(root).as_posix()
        parts, offset, split, label = _layout(root, path)
        digest = sha256_file(path)
        name_key = (str(path.parent.resolve()), COPY_SUFFIX.sub("", path.stem).casefold() + path.suffix.casefold())
        sha_duplicate = seen_hash.get(digest)
        name_duplicate = seen_name.get(name_key)
        duplicate_of = sha_duplicate or name_duplicate
        seen_hash.setdefault(digest, path)
        seen_name.setdefault(name_key, path)
        if label == "live":
            if len(parts) < offset + 6:
                violations.append({"code": "INVALID_LIVE_PATH", "file": relative})
                continue
            person, camera, capture_session, condition = parts[offset + 1:offset + 5]
            if condition not in CONDITIONS:
                violations.append({"code": "INVALID_LIVE_CONDITION", "file": relative, "condition": condition})
            live_sessions.add((split, person, camera, capture_session))
            live_conditions.add(condition)
            split_keys[("live_source", person, camera, capture_session)].add(split)
            if duplicate_of:
                if sha_duplicate:
                    exact_duplicates += 1
                else:
                    filename_copies += 1
                violations.append({
                    "code": "EXACT_DUPLICATE" if sha_duplicate else "FILENAME_COPY",
                    "file": relative, "duplicate_of": Path(duplicate_of).relative_to(root).as_posix(),
                })
            continue
        if label != "spoof" or len(parts) < offset + 4:
            violations.append({"code": "INVALID_SPOOF_PATH", "file": relative})
            continue
        attack, session_dir = parts[offset + 1:offset + 3]
        if attack not in ATTACK_TYPES:
            violations.append({"code": "INVALID_ATTACK_TYPE", "file": relative, "attack_type": attack})
            continue
        session_path = root.joinpath(*parts[:offset + 3])
        key = (split, attack, session_dir)
        if key not in sessions:
            metadata_path = session_path / "metadata.json"
            legacy_path = session_path / "session.json"
            if not metadata_path.is_file() and legacy_path.is_file():
                metadata_path = legacy_path
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                metadata = {}
                violations.append({"code": "MISSING_OR_INVALID_SESSION_METADATA", "session": session_path.relative_to(root).as_posix(), "detail": type(exc).__name__})
            missing = [field for field in REQUIRED_SESSION_FIELDS if not str(metadata.get(field, "")).strip()]
            if missing:
                violations.append({"code": "MISSING_SESSION_FIELDS", "session": session_path.relative_to(root).as_posix(), "fields": missing})
            if metadata.get("attack_session_id") and metadata["attack_session_id"] != session_dir:
                violations.append({"code": "ATTACK_SESSION_ID_MISMATCH", "session": session_path.relative_to(root).as_posix()})
            if metadata.get("attack_type") and metadata["attack_type"] != attack:
                violations.append({"code": "ATTACK_TYPE_MISMATCH", "session": session_path.relative_to(root).as_posix()})
            sessions[key] = {"metadata": metadata, "files": 0, "duplicates": 0}
            for field in ("attack_session_id", "source_identity", "display_or_print_device"):
                if metadata.get(field):
                    split_keys[(field, str(metadata[field]))].add(split)
        if not sha_duplicate:
            sessions[key]["files"] += 1
        if duplicate_of:
            if sha_duplicate:
                exact_duplicates += 1
            else:
                filename_copies += 1
            sessions[key]["duplicates"] += 1
            duplicate_count[attack] += 1
            violations.append({
                "code": "EXACT_DUPLICATE" if sha_duplicate else "FILENAME_COPY",
                "file": relative, "duplicate_of": Path(duplicate_of).relative_to(root).as_posix(),
            })
    for key, splits in split_keys.items():
        if "development" in splits and "test" in splits:
            violations.append({"code": "DEVELOPMENT_TEST_GROUP_LEAKAGE", "group": list(key), "splits": sorted(splits)})
    for attack in ATTACK_TYPES:
        rows = [value for (split, kind, session), value in sessions.items() if kind == attack]
        metadata = [row["metadata"] for row in rows]
        if rows:
            coverage[attack] = {
                "status": "EVALUATED" if all(all(str(item.get(field, "")).strip() for field in REQUIRED_SESSION_FIELDS) for item in metadata) else "PROTOCOL_VIOLATION",
                "sessions": len(rows),
                "unique_identities": len({item.get("source_identity") for item in metadata if item.get("source_identity")}),
                "unique_devices": len({item.get("display_or_print_device") for item in metadata if item.get("display_or_print_device")}),
                "unique_cameras": len({item.get("camera_id") for item in metadata if item.get("camera_id")}),
                "frames_images": sum(row["files"] for row in rows),
                "duplicates": duplicate_count[attack],
            }
    live_cameras = {
        parts[offset + 2]
        for path in media
        for parts, offset, split, label in [_layout(root, path)]
        if label == "live" and len(parts) >= offset + 6
    }
    all_devices = {
        str(row["metadata"].get("display_or_print_device"))
        for row in sessions.values() if row["metadata"].get("display_or_print_device")
    }
    all_sources = {
        str(row["metadata"].get("source_identity"))
        for row in sessions.values() if row["metadata"].get("source_identity")
    }
    spoof_cameras = {
        str(row["metadata"].get("camera_id"))
        for row in sessions.values() if row["metadata"].get("camera_id")
    }
    def devices_for(prefix):
        return {
            str(value["metadata"].get("display_or_print_device"))
            for (split, attack, session), value in sessions.items()
            if attack.startswith(prefix) and value["metadata"].get("display_or_print_device")
        }
    live_gaps = sorted(set(CONDITIONS) - live_conditions)
    complete = bool(live_sessions) and not live_gaps and all(coverage[name]["status"] == "EVALUATED" for name in ATTACK_TYPES) and not violations
    cross_camera = bool(
        len(live_cameras) >= 2
        and all(coverage[attack]["unique_cameras"] >= 2 for attack in ATTACK_TYPES)
    )
    return {
        "dataset_status": "BLOCKED_DATASET" if not media else "PASS" if complete else "INCOMPLETE",
        "attack_coverage": coverage,
        "attack_sessions": len(sessions),
        "devices": len(all_devices),
        "source_identities": len(all_sources),
        "cameras": sorted(live_cameras | spoof_cameras),
        "device_coverage": {
            "phones": len(devices_for("phone_")),
            "laptops": len(devices_for("laptop_")),
            "printers": len(devices_for("print")),
            "cameras": len(live_cameras | spoof_cameras),
        },
        "camera_coverage": {
            camera: {
                "live_sessions": sum(item[2] == camera for item in live_sessions),
                "attack_sessions": sum(str(value["metadata"].get("camera_id")) == camera for value in sessions.values()),
                "attack_types": sorted({attack for (split, attack, session), value in sessions.items() if str(value["metadata"].get("camera_id")) == camera}),
            }
            for camera in sorted(live_cameras | spoof_cameras)
        },
        "live_sessions": len(live_sessions),
        "live_conditions": sorted(live_conditions),
        "live_condition_gaps": live_gaps,
        "media_files": len(media) - exact_duplicates,
        "duplicates": exact_duplicates + filename_copies,
        "exact_duplicates": exact_duplicates,
        "filename_copies": filename_copies,
        "protocol_violations": violations,
        "PAD_DATASET_COMPLETE": complete,
        "CROSS_CAMERA_VALIDATED": cross_camera,
        "split_policy": "development/test groups must be disjoint by attack session, device, and source identity",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[1] / "pad_validation")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = audit_dataset(args.dataset)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if report["PAD_DATASET_COMPLETE"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
