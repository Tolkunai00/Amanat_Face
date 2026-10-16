"""Audit a recognition gallery/probe dataset and write its private manifest."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
ROLES = {"gallery", "probe"}
CONDITIONS = {"front", "left", "right", "far", "low_light"}
FIELDS = (
    "file", "person_id", "role", "session_id", "condition", "camera_id",
    "capture_time", "sha256", "burst_sequence_id",
)
COPY_SUFFIX = re.compile(r"(?i)(?:\s*\(\d+\)|[-_ ]copy(?:[-_ ]?\d+)?)$")


def sha256_file(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_metadata(path: Path | None):
    if path is None or not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        return {row["file"].replace("\\", "/"): row for row in csv.DictReader(source)}


def parse_path(dataset: Path, path: Path):
    parts = path.relative_to(dataset).parts
    # Optional development/test prefix is intentionally not part of the public manifest schema.
    offset = 1 if parts and parts[0] in {"development", "test"} else 0
    if len(parts) < offset + 5:
        return None, "expected person/role/session/condition/file hierarchy"
    person_id, role, session_id, condition = parts[offset:offset + 4]
    if role not in ROLES:
        return None, f"invalid role {role!r}"
    if condition not in CONDITIONS:
        return None, f"invalid condition {condition!r}"
    return {
        "person_id": person_id, "role": role, "session_id": session_id,
        "condition": condition,
        "split": parts[0] if offset else "unspecified",
    }, ""


def build_manifest(dataset: Path, metadata_path: Path | None = None):
    if metadata_path is None:
        default_metadata = dataset / "capture_metadata.csv"
        metadata_path = default_metadata if default_metadata.is_file() else None
    metadata = load_metadata(metadata_path)
    if dataset.name in {"development", "test"}:
        prefix = dataset.name + "/"
        metadata.update({key[len(prefix):]: value for key, value in list(metadata.items()) if key.startswith(prefix)})
    paths = sorted(path for path in dataset.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
    rows, violations = [], []
    sha_rows, name_rows, file_id_rows, burst_rows = {}, {}, {}, {}
    sessions, session_splits = {}, {}
    for path in paths:
        relative = path.relative_to(dataset).as_posix()
        parsed, error = parse_path(dataset, path)
        if error:
            violations.append({"code": "INVALID_PATH", "file": relative, "detail": error})
            continue
        digest = sha256_file(path)
        meta = metadata.get(relative, {})
        if not meta:
            violations.append({"code": "MISSING_CAPTURE_METADATA", "file": relative})
        else:
            missing_meta = [field for field in ("camera_id", "capture_time", "burst_sequence_id") if not str(meta.get(field, "")).strip()]
            if missing_meta:
                violations.append({"code": "MISSING_CAPTURE_FIELDS", "file": relative, "fields": missing_meta})
            if meta.get("sha256") and meta["sha256"].strip().lower() != digest:
                violations.append({"code": "SHA256_METADATA_MISMATCH", "file": relative})
            for field in ("person_id", "role", "session_id", "condition"):
                if meta.get(field) and meta[field].strip() != parsed[field]:
                    violations.append({"code": "CAPTURE_METADATA_MISMATCH", "file": relative, "field": field})
        capture_time = meta.get("capture_time", "").strip()
        if capture_time:
            try:
                datetime.fromisoformat(capture_time.replace("Z", "+00:00"))
            except ValueError:
                violations.append({"code": "INVALID_CAPTURE_TIME", "file": relative, "detail": capture_time})
        row = {
            "file": relative, "person_id": parsed["person_id"], "role": parsed["role"],
            "session_id": parsed["session_id"], "condition": parsed["condition"],
            "camera_id": meta.get("camera_id", "").strip(),
            "capture_time": capture_time, "sha256": digest,
            "burst_sequence_id": meta.get("burst_sequence_id", "").strip(),
            "split": parsed["split"],
        }
        # Exact bytes are counted once, but the protocol violation remains visible.
        is_exact_duplicate = digest in sha_rows
        if not is_exact_duplicate:
            rows.append(row)
        sessions.setdefault((row["person_id"], row["session_id"]), set()).add(row["role"])
        session_splits.setdefault((row["person_id"], row["session_id"]), set()).add(row["split"])
        for previous in sha_rows.get(digest, []):
            code = "GALLERY_PROBE_SHA_LEAKAGE" if previous["role"] != row["role"] else "SHA_DUPLICATE"
            violations.append({"code": code, "file": relative, "other_file": previous["file"], "sha256": digest})
        sha_rows.setdefault(digest, []).append(row)
        try:
            stat = path.stat()
            file_id = (stat.st_dev, stat.st_ino)
        except OSError:
            file_id = None
        if file_id is not None:
            for previous in file_id_rows.get(file_id, []):
                if previous["role"] != row["role"]:
                    violations.append({"code": "GALLERY_PROBE_SAME_FILE", "file": relative, "other_file": previous["file"]})
            file_id_rows.setdefault(file_id, []).append(row)
        name = (row["person_id"], COPY_SUFFIX.sub("", path.stem).casefold() + path.suffix.casefold())
        for previous in name_rows.get(name, []):
            violations.append({"code": "FILENAME_COPY", "file": relative, "other_file": previous["file"]})
        name_rows.setdefault(name, []).append(row)
        burst = row["burst_sequence_id"]
        if burst:
            for previous in burst_rows.get((row["person_id"], burst), []):
                if previous["role"] != row["role"]:
                    violations.append({
                        "code": "GALLERY_PROBE_BURST_LEAKAGE", "file": relative,
                        "other_file": previous["file"], "burst_sequence_id": burst,
                    })
            burst_rows.setdefault((row["person_id"], burst), []).append(row)
    for (person, session), roles in sessions.items():
        if len(roles) > 1:
            violations.append({
                "code": "GALLERY_PROBE_SESSION_LEAKAGE", "person_id": person,
                "session_id": session,
                "detail": "same capture session appears in gallery and probe",
            })
        splits = session_splits[(person, session)]
        if "development" in splits and "test" in splits:
            violations.append({
                "code": "DEVELOPMENT_TEST_SESSION_LEAKAGE", "person_id": person,
                "session_id": session, "splits": sorted(splits),
            })
    people = sorted({row["person_id"] for row in rows})
    operational = {}
    gallery_required = {"front", "left", "right"}
    probe_required = set(CONDITIONS)
    for person in people:
        subset = [row for row in rows if row["person_id"] == person]
        gallery_rows = [row for row in subset if row["role"] == "gallery"]
        probe_rows = [row for row in subset if row["role"] == "probe"]
        gallery_conditions = {row["condition"] for row in gallery_rows}
        probe_conditions = {row["condition"] for row in probe_rows}
        gallery_sessions = sorted({row["session_id"] for row in gallery_rows})
        probe_sessions = sorted({row["session_id"] for row in probe_rows})
        operational[person] = {
            "images": len(subset),
            "gallery_count": len(gallery_rows), "probe_count": len(probe_rows),
            "gallery_sessions": gallery_sessions,
            "probe_sessions": probe_sessions,
            "conditions": sorted({row["condition"] for row in subset}),
            "gallery_conditions": sorted(gallery_conditions),
            "probe_conditions": sorted(probe_conditions),
            "cameras": sorted({row["camera_id"] for row in subset if row["camera_id"]}),
            "gallery_gaps": sorted(gallery_required - gallery_conditions),
            "probe_gaps": sorted(probe_required - probe_conditions),
            "validation_status": "NOT_VALIDATABLE" if not probe_sessions else "VALIDATABLE_WITH_GAPS" if (gallery_required - gallery_conditions or probe_required - probe_conditions) else "OPERATIONAL_COVERAGE_PRESENT",
        }
    cameras = sorted({row["camera_id"] for row in rows if row["camera_id"]})
    cross_camera = bool(len(cameras) >= 2 and people and all(
        len({row["camera_id"] for row in rows if row["person_id"] == person and row["role"] == "probe" and row["camera_id"]}) >= 2
        for person in people
    ))
    coverage_complete = bool(people and cameras) and all(
        not value["gallery_gaps"] and not value["probe_gaps"] and value["probe_sessions"]
        for value in operational.values()
    )
    status = "BLOCKED_DATASET" if not rows else "DATASET_PROTOCOL_FAIL" if violations else "READY" if coverage_complete else "INCOMPLETE_COVERAGE"
    report = {
        "status": status,
        "persons": len(people),
        "gallery_sessions": len({(row["person_id"], row["session_id"]) for row in rows if row["role"] == "gallery"}),
        "probe_sessions": len({(row["person_id"], row["session_id"]) for row in rows if row["role"] == "probe"}),
        "cameras": cameras,
        "camera_coverage": {camera: sum(row["camera_id"] == camera for row in rows) for camera in cameras},
        "images": len(rows), "unique_sha": len(sha_rows),
        "duplicates": sum(max(0, len(values) - 1) for values in sha_rows.values()),
        "protocol_violations": violations,
        "conditions_coverage": {
            condition: sum(row["condition"] == condition for row in rows)
            for condition in CONDITIONS
        },
        "per_person": operational,
        "CROSS_CAMERA_VALIDATED": cross_camera,
        "RECOGNITION_DATASET_READY": status == "READY",
        "IDENTITY_CALIBRATED": False,
        "note": "The audit reports observed coverage; it does not claim statistical sufficiency.",
    }
    return rows, report


def write_manifest(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[1] / "recognition_validation")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--metadata", type=Path)
    args = parser.parse_args(argv)
    rows, report = build_manifest(args.dataset, args.metadata)
    write_manifest(args.manifest, rows)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.audit:
        args.audit.parent.mkdir(parents=True, exist_ok=True)
        args.audit.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if report["status"] == "READY" else 2 if report["status"] == "BLOCKED_DATASET" else 1


if __name__ == "__main__":
    raise SystemExit(main())
