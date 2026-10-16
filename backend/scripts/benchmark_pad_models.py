"""Compare diagnostic PAD providers on identical group-aware evidence."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import statistics
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.biometric_calibration import approximate_eer, filter_duplicates, pad_rates, pad_threshold_sweep  # noqa: E402
from scripts.calibrate_pad import ATTACK_TYPES, LIVE_CONDITIONS, iter_frames  # noqa: E402
from scripts.evaluate_fsvfm_fas import detect_bbox  # noqa: E402
from school_face_id.diagnostics.pad_providers import (  # noqa: E402
    AntiSpoofMN3DiagnosticProvider, FSVFMDiagnosticProvider, MiniFASNetV2DiagnosticProvider, PADProvider,
    load_external_provider,
)


MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".mp4", ".mov", ".avi", ".mkv"}
CSV_FIELDS = (
    "model_name", "model_version", "file", "evidence_group", "split", "label",
    "person_id", "camera_id", "condition", "attack_type", "live_score",
    "predicted_live", "latency_ms", "error",
)


def _stem_group(path: Path):
    import re
    return re.sub(r"(?i)(?:[-_ ](?:frame|img|photo))?[-_ ]?\d+$", "", path.stem).casefold()


def evidence_metadata(root: Path, path: Path):
    parts = path.relative_to(root).parts
    offset = 1 if parts and parts[0] in {"development", "test"} else 0
    split = parts[0] if offset else "unspecified"
    label = parts[offset] if len(parts) > offset else ""
    metadata = {
        "split": split, "label": label, "person_id": "", "camera_id": "",
        "condition": "", "attack_type": "", "evidence_group": "",
    }
    if label == "live":
        tail = parts[offset + 1:]
        if len(tail) >= 5:  # person/camera/session/condition/file
            person, camera, session, condition = tail[:4]
            group = f"{split}/live/{person}/{camera}/{session}/{condition}"
        elif len(tail) >= 3:  # legacy person/condition/file
            person, condition = tail[:2]
            camera, session = "unspecified", _stem_group(path)
            group = f"{split}/live/{person}/{camera}/{condition}/{session}"
        else:
            raise ValueError("invalid live hierarchy")
        metadata.update(person_id=person, camera_id=camera, condition=condition, evidence_group=group)
    elif label == "spoof":
        tail = parts[offset + 1:]
        if len(tail) < 2:
            raise ValueError("invalid spoof hierarchy")
        attack = tail[0]
        if len(tail) >= 3:
            session = tail[1]
            session_dir = root.joinpath(*parts[:offset + 3])
            session_json = session_dir / "metadata.json"
            if not session_json.is_file() and (session_dir / "session.json").is_file():
                session_json = session_dir / "session.json"
            try:
                session_meta = json.loads(session_json.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                session_meta = {}
            person = str(session_meta.get("source_identity", ""))
            camera = str(session_meta.get("camera_id", ""))
        else:
            session, person, camera = _stem_group(path), "", "unspecified"
        metadata.update(
            attack_type=attack, person_id=person, camera_id=camera,
            evidence_group=f"{split}/spoof/{attack}/{session}",
        )
    else:
        raise ValueError("media must be under live/ or spoof/")
    return metadata


def collect_evidence(root: Path, split="calibration"):
    paths = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES)
    # Reused frame names in distinct attack sessions are allowed; copied names
    # inside one session and exact SHA duplicates anywhere are not.
    unique, duplicates = filter_duplicates(paths, filename_scope="directory")
    wanted = "development" if split == "calibration" else "test" if split == "test" else None
    evidence = []
    for path in unique:
        metadata = evidence_metadata(root, path)
        if wanted and metadata["split"] not in {wanted, "unspecified"}:
            continue
        evidence.append((path, metadata))
    return evidence, duplicates


def benchmark(evidence, providers, sample_seconds=1.0, max_frames=30):
    records = []
    for path, metadata in evidence:
        relative = path.as_posix()
        try:
            frames = iter_frames(path, sample_seconds, max_frames)
            for frame_name, frame in frames:
                file_name = f"{relative}#frame={frame_name}" if path.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv"} else relative
                try:
                    bbox = detect_bbox(frame)
                    detection_error = ""
                except Exception as exc:
                    bbox = None; detection_error = f"{type(exc).__name__}: {exc}"
                for provider in providers:
                    if detection_error:
                        result = None
                    else:
                        result = provider.predict(frame, bbox)
                    records.append({
                        "model_name": provider.model_name,
                        "model_version": provider.model_version,
                        "file": file_name, **metadata,
                        "live_score": None if result is None else result.live_score,
                        "predicted_live": False if result is None else result.predicted_live,
                        "latency_ms": None if result is None else result.latency_ms,
                        "error": detection_error if result is None else result.error,
                    })
        except Exception as exc:
            for provider in providers:
                records.append({
                    "model_name": provider.model_name, "model_version": provider.model_version,
                    "file": relative, **metadata, "live_score": None,
                    "predicted_live": False, "latency_ms": None,
                    "error": f"{type(exc).__name__}: {exc}",
                })
    return records


def aggregate_model_groups(records):
    groups = {}
    for row in records:
        groups.setdefault(row["evidence_group"], []).append(row)
    result = []
    for group, rows in groups.items():
        valid = [row for row in rows if not row["error"] and row["live_score"] is not None]
        if not valid:
            result.append({**rows[0], "file": group, "live_score": None, "error": "no evaluable frame"})
            continue
        selected = (max if rows[0]["label"] == "spoof" else min)(valid, key=lambda row: row["live_score"])
        result.append({**selected, "file": group})
    return result


def _metric_subset(rows, threshold):
    valid = [row for row in rows if not row["error"] and row["live_score"] is not None]
    live = [row["live_score"] for row in valid if row["label"] == "live"]
    spoof = [row["live_score"] for row in valid if row["label"] == "spoof"]
    apcer, bpcer, acer = pad_rates(live, spoof, threshold)
    return {"count": len(rows), "evaluated": len(valid), "APCER": apcer, "BPCER": bpcer, "ACER": acer}


def _best_calibration_point(sweep):
    eligible = [row for row in sweep if row["ACER"] is not None]
    if not eligible:
        return None
    # Deterministic and conservative tie-break: lower APCER, then lower BPCER.
    return min(eligible, key=lambda row: (row["ACER"], row["APCER"], row["BPCER"], row["threshold"]))


def model_verdict(*, missing_attacks, license_status, screening_only, acceptable_attacks=False):
    """License/coverage gate; measured security acceptance remains an explicit input."""
    if missing_attacks or not acceptable_attacks:
        return "FAIL"
    if license_status.startswith("UNKNOWN"):
        return "RESEARCH_ONLY"
    if license_status.startswith("RESEARCH_ONLY"):
        return "RESEARCH_ONLY"
    return "SCREENING_CANDIDATE" if screening_only else "PILOT_CANDIDATE"


def summarize(records, providers, phase="calibration", screening_only=False):
    report = {}
    provider_map = {provider.model_name: provider for provider in providers}
    for model_name, provider in provider_map.items():
        frame_rows = [row for row in records if row["model_name"] == model_name]
        groups = aggregate_model_groups(frame_rows)
        overall = _metric_subset(groups, provider.threshold)
        attacks = {}
        for attack in ATTACK_TYPES:
            subset = [row for row in groups if row["label"] == "spoof" and row["attack_type"] == attack]
            attacks[attack] = {"status": "NOT_TESTED", **_metric_subset(subset, provider.threshold)} if not subset else {"status": "EVALUATED", **_metric_subset(subset, provider.threshold)}
        def grouped(field, values=None):
            keys = values or sorted({row[field] for row in groups if row.get(field)})
            return {value: _metric_subset([row for row in groups if row.get(field) == value], provider.threshold) for value in keys}
        valid_latencies = [row["latency_ms"] for row in frame_rows if row["latency_ms"] is not None and not row["error"]]
        failed = sum(bool(row["error"]) for row in groups)
        cameras = grouped("camera_id")
        camera_rates = [value["APCER"] for value in cameras.values() if value["APCER"] is not None]
        cross_camera = max(camera_rates) - min(camera_rates) if len(camera_rates) > 1 else None
        sweep = pad_threshold_sweep(
            [row["live_score"] for row in groups if row["label"] == "live" and not row["error"]],
            [row["live_score"] for row in groups if row["label"] == "spoof" and not row["error"]],
        )
        eer, eer_threshold = approximate_eer(sweep, "APCER", "BPCER")
        best_point = _best_calibration_point(sweep) if phase == "calibration" else None
        missing = [attack for attack, value in attacks.items() if value["status"] == "NOT_TESTED"]
        # No universal APCER cutoff is invented here. Human acceptance is required,
        # so a benchmark cannot promote itself merely by minimizing ACER.
        verdict = model_verdict(
            missing_attacks=missing, license_status=provider.license_status,
            screening_only=screening_only, acceptable_attacks=False,
        )
        report[model_name] = {
            "model": model_name, "model_version": provider.model_version,
            "checkpoint": provider.checkpoint, "license_status": provider.license_status,
            "checkpoint_metadata": getattr(provider, "checkpoint_metadata", None),
            "preprocessing_assumption": getattr(provider, "preprocessing_assumption", "NOT_DECLARED"),
            "input": provider.input_specification, "threshold": provider.threshold,
            "phase": phase, "overall": overall,
            "failure_rate": failed / len(groups) if groups else None,
            "latency_ms": {
                "average": statistics.fmean(valid_latencies) if valid_latencies else None,
                "p50": float(np.percentile(valid_latencies, 50)) if valid_latencies else None,
                "p95": float(np.percentile(valid_latencies, 95)) if valid_latencies else None,
            },
            "per_attack": attacks, "per_camera": cameras,
            "per_condition": grouped("condition", LIVE_CONDITIONS),
            "per_person": grouped("person_id"),
            "cross_camera_APCER_range": cross_camera,
            "candidate_threshold": eer_threshold if phase == "calibration" else None,
            "candidate_eer_like": eer if phase == "calibration" else None,
            "best_calibration_point": best_point,
            "threshold_sweep": sweep if phase == "calibration" else [],
            "missing_attack_categories": missing,
            "verdict": verdict,
        }
    # Enforce that every adapter received exactly the same evidence groups.
    group_sets = {
        name: {row["evidence_group"] for row in records if row["model_name"] == name}
        for name in provider_map
    }
    same_groups = len({frozenset(value) for value in group_sets.values()}) <= 1
    def license_rank(value):
        status = value["license_status"]
        return 0 if status.startswith("COMMERCIAL_OK") else 1 if status.startswith("RESEARCH_ONLY") else 2

    ranking = sorted(report, key=lambda name: (
        max((value["APCER"] for value in report[name]["per_attack"].values() if value["APCER"] is not None), default=1.0),
        report[name]["overall"]["APCER"] if report[name]["overall"]["APCER"] is not None else 1.0,
        report[name]["overall"]["BPCER"] if report[name]["overall"]["BPCER"] is not None else 1.0,
        report[name]["failure_rate"] if report[name]["failure_rate"] is not None else 1.0,
        report[name]["latency_ms"]["average"] if report[name]["latency_ms"]["average"] is not None else float("inf"),
        license_rank(report[name]),
        report[name]["cross_camera_APCER_range"] if report[name]["cross_camera_APCER_range"] is not None else 1.0,
    ))
    eligible = [name for name in ranking if report[name]["verdict"] in {"PILOT_CANDIDATE", "PRODUCTION_CANDIDATE"}] if same_groups else []
    return {
        "same_evidence_groups": same_groups,
        "SCREENING_ONLY": bool(screening_only),
        "comparison_priority": ["worst_case_APCER", "overall_APCER", "BPCER", "failed_rate", "latency", "license", "cross_camera_stability"],
        "ranking": ranking, "models": report,
        "BEST_PAD_CANDIDATE": eligible[0] if eligible else "NONE",
        "FSVFM_ENFORCEMENT_ALLOWED": False,
        "production_thresholds_changed": False,
        "ACCEPTANCE_LIMITS_APPROVED": False,
    }


def parse_thresholds(values):
    result = {}
    for value in values:
        name, separator, raw = value.partition("=")
        if not separator or not 0 <= float(raw) <= 1:
            raise ValueError("thresholds must use model=value with value in 0..1")
        result[name] = float(raw)
    return result


def build_providers(args, thresholds):
    root = Path(__file__).resolve().parents[2]
    providers, failures = [], {}
    for name in args.models:
        try:
            if name == "minifas":
                providers.append(MiniFASNetV2DiagnosticProvider(args.minifas_checkpoint or root / "deploy/models/anti_spoof.onnx", thresholds.get("minifas", 0.80)))
            elif name == "fsvfm":
                providers.append(FSVFMDiagnosticProvider(args.fsvfm_checkpoint or root / "deploy/models/fsvfm_vit_s_oci2m_run0.pth.tar", thresholds.get("fsvfm", 0.50), args.device))
            elif name == "anti_spoof_mn3":
                providers.append(AntiSpoofMN3DiagnosticProvider(
                    args.anti_spoof_mn3_checkpoint or root / "deploy/models/anti-spoof-mn3.onnx",
                    thresholds.get("anti_spoof_mn3", 0.50),
                ))
            else:
                raise ValueError(f"unknown built-in provider: {name}")
        except Exception as exc:
            failures[name] = f"{type(exc).__name__}: {exc}"
    for spec in args.provider:
        try:
            providers.append(load_external_provider(spec))
        except Exception as exc:
            failures[spec] = f"{type(exc).__name__}: {exc}"
    return providers, failures


def write_csv(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader(); writer.writerows(records)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--models", nargs="+", choices=("minifas", "fsvfm", "anti_spoof_mn3"), default=("minifas", "fsvfm", "anti_spoof_mn3"))
    parser.add_argument("--provider", action="append", default=[], help="Explicit future adapter module:factory")
    parser.add_argument("--minifas-checkpoint", type=Path)
    parser.add_argument("--fsvfm-checkpoint", type=Path)
    parser.add_argument("--anti-spoof-mn3-checkpoint", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--phase", choices=("calibration", "test"), default="calibration")
    parser.add_argument("--threshold", action="append", default=[], help="model=value; mandatory for held-out test")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--video-sample-seconds", type=float, default=1.0)
    parser.add_argument("--max-frames-per-video", type=int, default=30)
    args = parser.parse_args(argv)
    thresholds = parse_thresholds(args.threshold)
    if args.phase == "test":
        missing = [name for name in args.models if name not in thresholds]
        if missing:
            parser.error("held-out test requires preselected --threshold for: " + ", ".join(missing))
    evidence, duplicates = collect_evidence(args.images, args.phase)
    providers, provider_failures = build_providers(args, thresholds)
    records = benchmark(evidence, providers, args.video_sample_seconds, args.max_frames_per_video) if providers else []
    standardized_root = Path(__file__).resolve().parents[1] / "pad_validation"
    try:
        standardized = args.images.resolve().is_relative_to(standardized_root.resolve())
    except AttributeError:  # pragma: no cover - Python 3.8/3.9 compatibility
        standardized = str(args.images.resolve()).startswith(str(standardized_root.resolve()))
    screening_only = not standardized
    summary = summarize(records, providers, args.phase, screening_only=screening_only)
    summary.update({
        "dataset": str(args.images), "evidence_sources": len(evidence),
        "duplicates": duplicates, "provider_failures": provider_failures,
        "dataset_classification": "standardized_validation" if standardized else "historical_screening",
        "historical_data_used_for_production_verdict": False,
    })
    write_csv(args.csv, records)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if providers and evidence else 2


if __name__ == "__main__":
    raise SystemExit(main())
