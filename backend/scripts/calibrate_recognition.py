"""Calibrate ArcFace identity decisions on a separate, read-only dataset.

This command emits candidates only. It never imports or writes production env
files, thresholds, enrollment embeddings, the database, or attendance records.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.biometric_calibration import (  # noqa: E402
    approximate_eer, distribution, error_rates, filter_duplicates, identity_threshold_sweep,
)
from scripts.create_recognition_manifest import build_manifest  # noqa: E402
from school_face_id.config.settings import RECOGNITION_MARGIN, RECOGNITION_THRESHOLD  # noqa: E402
from school_face_id.services.students import embedding_for  # noqa: E402


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
EXPLICIT_GALLERY = {"gallery", "enrollment"}
EXPLICIT_PROBE = {"probe", "validation"}
CONDITIONS = ("front", "left", "right", "far", "low_light")
CSV_FIELDS = (
    "file", "person_id", "expected_person_id", "predicted_person_id", "label",
    "top1_similarity", "top2_similarity", "margin", "matched", "error",
)


def _capture_group(person_root: Path, path: Path) -> str:
    relative = path.relative_to(person_root)
    if len(relative.parts) > 1:
        return relative.parts[0].casefold()
    stem = re.sub(r"(?i)(?:[-_ ](?:frame|img|photo))?[-_ ]?\d+$", "", path.stem)
    return stem.casefold() or path.stem.casefold()


def split_gallery_probe(dataset: Path, paths):
    """Prefer declared splits, otherwise split deterministic capture groups."""
    people = {}
    warnings = []
    for path in paths:
        relative = path.relative_to(dataset)
        if len(relative.parts) < 2:
            continue
        person_id = relative.parts[0]
        people.setdefault(person_id, []).append(path)
    gallery, probes = {}, []
    split_evidence = {}
    for person_id, person_paths in sorted(people.items()):
        person_root = dataset / person_id
        declared_gallery = [p for p in person_paths if p.relative_to(person_root).parts[0].casefold() in EXPLICIT_GALLERY]
        declared_probe = [p for p in person_paths if p.relative_to(person_root).parts[0].casefold() in EXPLICIT_PROBE]
        if declared_gallery or declared_probe:
            if person_id.casefold().startswith("unknown") and declared_probe and not declared_gallery:
                probes.extend((person_id, path, "explicit_unknown") for path in declared_probe)
                split_evidence[person_id] = {"mode": "explicit_unknown_probe", "gallery": 0, "probe": len(declared_probe)}
                continue
            if not declared_gallery or not declared_probe:
                warnings.append(f"{person_id}: both explicit gallery and probe folders are required")
                continue
            gallery[person_id] = declared_gallery
            probes.extend((person_id, path, "explicit") for path in declared_probe)
            split_evidence[person_id] = {"mode": "explicit", "gallery": len(declared_gallery), "probe": len(declared_probe)}
            continue
        groups = {}
        for path in person_paths:
            groups.setdefault(_capture_group(person_root, path), []).append(path)
        ordered = sorted(groups.items())
        if len(ordered) < 2:
            warnings.append(f"{person_id}: fewer than two independent capture groups")
            continue
        gallery_group, gallery_paths = ordered[0]
        gallery[person_id] = gallery_paths
        for group_id, group_paths in ordered[1:]:
            probes.extend((person_id, path, group_id) for path in group_paths)
        split_evidence[person_id] = {
            "mode": "deterministic_group_fallback", "gallery_group": gallery_group,
            "probe_groups": [name for name, _ in ordered[1:]],
            "warning": "session metadata unavailable; verify groups before relying on metrics",
        }
    return gallery, probes, split_evidence, warnings


def _condition(path: Path) -> str:
    value = path.as_posix().casefold()
    return next((name for name in CONDITIONS if name in value), "unspecified")


def _embed(path: Path):
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("image unreadable")
    # Uses the runtime accurate detector, quality gates and normalized ArcFace embedding.
    return embedding_for(image)


def margin_sweep(probe_results, threshold=RECOGNITION_THRESHOLD):
    rows = []
    for candidate in np.linspace(0.0, 0.30, 61):
        accepted = [row for row in probe_results if row["top1_similarity"] >= threshold and row["margin"] >= candidate]
        false_accepts = sum(row["predicted_person_id"] != row["expected_person_id"] for row in accepted)
        false_rejects = sum(
            not (row["top1_similarity"] >= threshold and row["margin"] >= candidate and row["predicted_person_id"] == row["expected_person_id"])
            for row in probe_results
        )
        rows.append({
            "margin": float(candidate),
            "false_accept_rate": false_accepts / len(probe_results) if probe_results else None,
            "false_reject_rate": false_rejects / len(probe_results) if probe_results else None,
        })
    return rows


def _candidates(threshold_rows, margin_rows):
    valid = [row for row in threshold_rows if row["FAR"] is not None and row["FRR"] is not None]
    if not valid:
        return []
    balanced = min(valid, key=lambda row: (abs(row["FAR"] - row["FRR"]), row["FAR"] + row["FRR"]))
    conservative_pool = [row for row in valid if row["FAR"] <= 0.001]
    conservative = min(conservative_pool or valid, key=lambda row: (row["FRR"], row["FAR"], -row["threshold"]))
    convenience_pool = [row for row in valid if row["FAR"] <= 0.01]
    convenience = min(convenience_pool or valid, key=lambda row: (row["FRR"], row["FAR"]))
    margin = min(margin_rows, key=lambda row: (
        abs(row["false_accept_rate"] - row["false_reject_rate"]),
        row["false_accept_rate"] + row["false_reject_rate"],
    )) if margin_rows else {"margin": None}
    return [
        {"profile": "conservative", **conservative, "candidate_margin": margin["margin"]},
        {"profile": "balanced", **balanced, "candidate_margin": margin["margin"]},
        {"profile": "convenience", **convenience, "candidate_margin": margin["margin"]},
    ]


def _manifest_cameras(path):
    if path is None or not Path(path).is_file():
        return {}
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        return {row["file"].replace("\\", "/"): row.get("camera_id", "") or "unspecified" for row in csv.DictReader(source)}


def calibrate(
    dataset: Path, threshold=RECOGNITION_THRESHOLD, margin=RECOGNITION_MARGIN,
    manifest_path=None, phase="calibration",
):
    all_paths = sorted(path for path in dataset.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
    unique_paths, duplicates = filter_duplicates(all_paths)
    gallery_paths, probes, split_evidence, warnings = split_gallery_probe(dataset, unique_paths)
    records, failures = [], []
    camera_map = _manifest_cameras(manifest_path)
    image_embeddings = []
    gallery = {}
    gallery_files = set()
    for person_id, paths in gallery_paths.items():
        values = []
        for path in paths:
            try:
                embedding = _embed(path)
                values.append(embedding)
                image_embeddings.append((person_id, path, embedding))
                gallery_files.add(path.resolve())
            except Exception as exc:
                failures.append({"file": path.relative_to(dataset).as_posix(), "error": f"{type(exc).__name__}: {exc}"})
        if values:
            gallery[person_id] = np.asarray(values, dtype=np.float32)

    genuine, impostor = [], []
    def bucket():
        return {"evaluated": 0, "correct": 0, "top1_correct": 0, "margin_failures": 0,
                "known_probes": 0, "unknown_probes": 0, "unknown_rejected": 0,
                "genuine_scores": [], "impostor_scores": []}
    per_person = {person: bucket() for person in gallery}
    per_condition = {condition: bucket() for condition in (*CONDITIONS, "unspecified")}
    per_camera = {}
    top1_correct = margin_failures = known_probes = unknown_probes = unknown_rejected = 0
    for expected, path, group_id in probes:
        relative = path.relative_to(dataset).as_posix()
        base = {"file": relative, "person_id": expected, "expected_person_id": expected}
        if path.resolve() in gallery_files:
            records.append({**base, "predicted_person_id": "", "label": "", "top1_similarity": "", "top2_similarity": "", "margin": "", "matched": False, "error": "gallery_probe_self_match_blocked"})
            continue
        is_unknown = expected.casefold().startswith("unknown")
        if expected not in gallery and not is_unknown:
            records.append({**base, "predicted_person_id": "", "label": "", "top1_similarity": "", "top2_similarity": "", "margin": "", "matched": False, "error": "expected identity has no valid gallery embedding"})
            continue
        try:
            embedding = _embed(path)
            image_embeddings.append((expected, path, embedding))
            scores = {
                person: float(np.max(matrix @ embedding))
                for person, matrix in gallery.items()
            }
            ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            predicted, top1 = ranked[0]
            top2 = ranked[1][1] if len(ranked) > 1 else -1.0
            score_margin = top1 - top2 if len(ranked) > 1 else 2.0
            correct = predicted == expected
            accepted = top1 >= threshold and score_margin >= margin
            if is_unknown:
                unknown_probes += 1
                unknown_rejected += int(not accepted)
                impostor.extend(scores.values())
            else:
                known_probes += 1
                top1_correct += int(correct)
                margin_failures += int(correct and top1 >= threshold and score_margin < margin)
                genuine.append(scores[expected])
                impostor.extend(score for person, score in scores.items() if person != expected)
            condition = _condition(path)
            camera = camera_map.get(relative, camera_map.get(f"{dataset.name}/{relative}", "unspecified"))
            per_camera.setdefault(camera, bucket())
            for stats in (per_person.setdefault(expected, bucket()), per_condition[condition], per_camera[camera]):
                stats["evaluated"] += 1
                stats["correct"] += int((not accepted) if is_unknown else (correct and accepted))
                stats["top1_correct"] += int(correct and not is_unknown)
                if not is_unknown:
                    stats["known_probes"] += 1
                    stats["genuine_scores"].append(scores[expected])
                    stats["impostor_scores"].extend(score for person, score in scores.items() if person != expected)
                    stats["margin_failures"] += int(correct and top1 >= threshold and score_margin < margin)
                else:
                    stats["unknown_probes"] += 1
                    stats["unknown_rejected"] += int(not accepted)
                    stats["impostor_scores"].extend(scores.values())
            records.append({
                **base, "predicted_person_id": predicted,
                "label": "unknown" if is_unknown else "genuine" if correct else "impostor",
                "top1_similarity": top1, "top2_similarity": top2,
                "margin": score_margin, "matched": bool((not accepted) if is_unknown else (accepted and correct)), "error": "",
                "capture_group": group_id,
            })
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            failures.append({"file": relative, "error": error})
            records.append({**base, "predicted_person_id": "", "label": "", "top1_similarity": "", "top2_similarity": "", "margin": "", "matched": False, "error": error})

    sweep = identity_threshold_sweep(genuine, impostor) if phase == "calibration" else []
    eer, eer_threshold = approximate_eer(sweep) if phase == "calibration" else (None, None)
    margins = margin_sweep([row for row in records if not row["error"]], threshold) if phase == "calibration" else []
    for buckets in (per_person, per_condition, per_camera):
        for value in buckets.values():
            scores = value.pop("genuine_scores")
            negative_scores = value.pop("impostor_scores")
            far, frr = error_rates(scores, negative_scores, threshold)
            value["acceptance_rate"] = value["correct"] / value["evaluated"] if value["evaluated"] else None
            value["genuine"] = distribution(scores)
            value["impostor"] = distribution(negative_scores)
            value["FAR"] = far
            value["FRR"] = frr
            value["top1_accuracy"] = value["top1_correct"] / value["known_probes"] if value["known_probes"] else None
            value["unknown_rejection_rate"] = value["unknown_rejected"] / value["unknown_probes"] if value["unknown_probes"] else None
    identities = len(gallery)
    identity_calibrated = bool(
        identities >= 3 and len(genuine) >= 10 and len(impostor) >= 20
        and all(value["evaluated"] for value in per_person.values())
        and not any(row["error"] == "gallery_probe_self_match_blocked" for row in records)
    )
    dangerous = []
    for left in range(len(image_embeddings)):
        person_a, path_a, embedding_a = image_embeddings[left]
        for right in range(left + 1, len(image_embeddings)):
            person_b, path_b, embedding_b = image_embeddings[right]
            if person_a == person_b or person_a.casefold().startswith("unknown") or person_b.casefold().startswith("unknown"):
                continue
            dangerous.append({
                "person_a": person_a, "person_b": person_b,
                "similarity": float(embedding_a @ embedding_b),
                "image_a": path_a.relative_to(dataset).as_posix(),
                "image_b": path_b.relative_to(dataset).as_posix(),
            })
    dangerous = sorted(dangerous, key=lambda row: row["similarity"], reverse=True)[:50]
    candidates = _candidates(sweep, margins) if phase == "calibration" else []
    configured_far, configured_frr = error_rates(genuine, impostor, threshold)
    return records, {
        "dataset_status": "EVALUATED" if unique_paths else "BLOCKED_DATASET",
        "images_found": duplicates["raw"],
        "images_evaluated": sum(not row["error"] for row in records),
        "images_failed": len(failures) + sum(row["error"] == "gallery_probe_self_match_blocked" for row in records),
        "duplicates": duplicates,
        "gallery_probe_split": split_evidence,
        "split_warnings": warnings,
        "genuine": distribution(genuine), "impostor": distribution(impostor),
        "threshold_sweep": sweep, "roc_points": sweep,
        "margin_sweep": margins,
        "eer": eer, "eer_threshold": eer_threshold,
        "configured_threshold_evaluation": {"threshold": threshold, "FAR": configured_far, "FRR": configured_frr},
        "FAR": configured_far, "FRR": configured_frr,
        "configured_threshold": threshold, "configured_margin": margin,
        "evaluation_phase": phase,
        "recommended_candidates": candidates,
        "threshold_selection_allowed": phase == "calibration",
        "top1_accuracy": top1_correct / known_probes if known_probes else None,
        "unknown_rejection_rate": unknown_rejected / unknown_probes if unknown_probes else None,
        "unknown_probe_count": unknown_probes,
        "margin_failures": margin_failures,
        "per_person": per_person, "per_camera": per_camera, "per_condition": per_condition,
        "highest_impostor_similarities": dangerous,
        "impostor_coverage": {"identities": identities, "comparisons": len(impostor), "sufficient": identities >= 3 and len(impostor) >= 20},
        "failures": failures,
        "IDENTITY_CALIBRATED": identity_calibrated,
        "warning": "Candidates are offline evidence only; no production configuration was changed.",
        "held_out_policy": "No candidate threshold or margin is generated from held-out test data." if phase == "test" else "Candidates may be generated only from calibration data.",
    }


def write_csv(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[1] / "recognition_validation")
    parser.add_argument("--csv", type=Path, default=Path("recognition_calibration.csv"))
    parser.add_argument("--json", type=Path, default=Path("recognition_calibration.json"))
    parser.add_argument("--threshold", type=float, help="Required for held-out test; selected on calibration data")
    parser.add_argument("--margin", type=float, help="Required for held-out test; selected on calibration data")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--phase", choices=("calibration", "test"), default="calibration")
    args = parser.parse_args(argv)
    if not args.dataset.is_dir():
        print(json.dumps({"dataset_status": "BLOCKED_DATASET", "IDENTITY_CALIBRATED": False, "dataset": str(args.dataset)}, indent=2))
        return 2
    if args.phase == "test" and (args.threshold is None or args.margin is None):
        parser.error("held-out test requires --threshold and --margin selected on calibration data")
    selected_dataset = args.dataset
    split_name = "development" if args.phase == "calibration" else "test"
    if (args.dataset / split_name).is_dir():
        selected_dataset = args.dataset / split_name
    metadata = args.dataset / "capture_metadata.csv"
    _, audit = build_manifest(selected_dataset, metadata if metadata.is_file() else None)
    if audit["status"] == "DATASET_PROTOCOL_FAIL":
        print(json.dumps(audit, ensure_ascii=False, indent=2))
        return 3
    threshold = RECOGNITION_THRESHOLD if args.threshold is None else args.threshold
    margin = RECOGNITION_MARGIN if args.margin is None else args.margin
    records, summary = calibrate(selected_dataset, threshold, margin, args.manifest, args.phase)
    write_csv(args.csv, records)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["images_evaluated"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
