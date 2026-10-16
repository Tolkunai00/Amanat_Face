"""Audit recognition collection coverage and gallery/probe leakage."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.create_recognition_manifest import build_manifest, write_manifest  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[1] / "recognition_validation")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--metadata", type=Path)
    args = parser.parse_args(argv)
    rows, report = build_manifest(args.dataset, args.metadata)
    if args.manifest:
        write_manifest(args.manifest, rows)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if report["RECOGNITION_DATASET_READY"] else 2 if report["status"] == "BLOCKED_DATASET" else 1


if __name__ == "__main__":
    raise SystemExit(main())
