#!/usr/bin/env python3
"""Non-mutating failure-mode contract reporter.

Fault injection is deliberately not performed against production. Use this
manifest in an isolated Compose copy, capture /health/ready and Prometheus
results, and compare them with the expected contract printed here.
"""

import argparse
import json


SCENARIOS = {
    "db-unavailable": ("not_ready/503", "no reads or writes", "DatabaseUnavailable,BackendNotReady"),
    "one-camera-offline": ("ready or degraded/200", "other camera continues", "CameraOffline"),
    "both-cameras-offline": ("ready or degraded/200", "no new automatic attendance", "CameraOffline"),
    "minifas-corrupt": ("not_ready/503", "recognition startup blocked", "BackendNotReady"),
    "fsvfm-missing-shadow": ("degraded/200", "MiniFAS remains authoritative", "operator warning"),
    "fsvfm-missing-enforcement": ("not_ready/503", "recognition startup blocked", "BackendNotReady"),
    "arcface-unavailable": ("not_ready/503", "no recognition attendance", "BackendNotReady"),
    "migration-behind-head": ("not_ready/503", "deployment blocked", "MigrationMismatch"),
    "backup-failure": ("readiness unchanged", "deployment migration blocked", "BackupTooOld"),
    "disk-low": ("ready until writes fail", "deployment/operator intervention", "DiskSpaceLow/DiskSpaceCritical"),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    selected = SCENARIOS if args.scenario == "all" else {args.scenario: SCENARIOS[args.scenario]}
    report = {
        name: {"expected_health": values[0], "attendance_behavior": values[1], "expected_alert": values[2]}
        for name, values in selected.items()
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for name, values in report.items():
            print(f"{name}: health={values['expected_health']}; attendance={values['attendance_behavior']}; alert={values['expected_alert']}")
    print("NOT_TESTED: run fault injection only in an isolated copy; production data was not changed")


if __name__ == "__main__":
    main()
