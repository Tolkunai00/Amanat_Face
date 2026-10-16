from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from ...config.settings import (
    ANTI_SPOOFING_MODEL_PATH, BACKUP_STATUS_FILE, CAMERA_INDEXES, FACE_MODEL_LICENSE,
    FACE_MODEL_NAME, OFFSITE_BACKUP_STATUS_FILE, RELEASE_VERSION,
    DISK_CRITICAL_PERCENT, DISK_WARNING_PERCENT,
)
from ...db.connection import get_connection

router = APIRouter(tags=["system"])


def _error_component(exc):
    return {"status": "not_ready", "error": type(exc).__name__}


def readiness_report():
    """Build a non-mutating readiness report from critical dependencies."""
    components = {}
    try:
        from ...config.settings import validate_runtime_settings
        validate_runtime_settings(check_model_files=False)
        components["config"] = {"status": "ready"}
    except Exception as exc:
        components["config"] = {"status": "not_ready", "error": str(exc)}

    try:
        connection = get_connection()
        try:
            connection.execute("SELECT 1").fetchone()
        finally:
            connection.close()
        components["database"] = {"status": "ready"}
    except Exception as exc:
        components["database"] = _error_component(exc)

    try:
        from ...db.migrations import migration_status
        components["migrations"] = migration_status()
    except Exception as exc:
        components["migrations"] = _error_component(exc)

    from ...recognition.camera_service import identity_model_status
    from ...recognition.anti_spoof import anti_spoof_status
    from ...recognition.fsvfm_pad import fsvfm_status
    components["identity_model"] = identity_model_status()
    components["anti_spoof"] = anti_spoof_status()
    components["fsvfm"] = fsvfm_status()
    if OFFSITE_BACKUP_STATUS_FILE:
        try:
            offsite = json.loads(Path(OFFSITE_BACKUP_STATUS_FILE).read_text(encoding="utf-8"))
            components["offsite_backup"] = {
                "status": offsite.get("status", "unknown"),
                "mode": offsite.get("mode", "unknown"),
            }
        except (OSError, ValueError):
            components["offsite_backup"] = {"status": "not_configured", "mode": "none"}
    else:
        components["offsite_backup"] = {"status": "not_configured", "mode": "none"}
    components["alert_delivery"] = {
        "status": "configured" if __import__("os").getenv("ALERTMANAGER_RECEIVER", "").strip() else "not_configured"
    }

    critical = ("config", "database", "migrations", "identity_model", "anti_spoof")
    if any(components[name].get("status") != "ready" for name in critical):
        status = "not_ready"
    elif components["fsvfm"].get("status") == "not_ready":
        status = "not_ready"
    elif components["fsvfm"].get("status") == "degraded":
        status = "degraded"
    else:
        status = "ready"
    return {"status": status, "components": components}


@router.get("/health")
def healthcheck():
    from ...recognition.anti_spoof import anti_spoof_loaded
    return {
        "status": "ok", "service": "school-face-id-api", "api_version": "v1",
        "release_version": RELEASE_VERSION,
        "recognition": {
            "model": FACE_MODEL_NAME,
            "commercial_license_declared": FACE_MODEL_LICENSE == "commercial",
            "anti_spoofing_configured": bool(ANTI_SPOOFING_MODEL_PATH),
            "anti_spoofing_loaded": anti_spoof_loaded(),
            # PHASE 2C cannot assert biometric/legal/onsite release gates.
            "production_ready": False,
        },
    }


@router.get("/health/live")
def liveness():
    return {"status": "live", "service": "school-face-id-api"}


@router.get("/health/ready")
def readiness():
    report = readiness_report()
    return JSONResponse(report, status_code=200 if report["status"] != "not_ready" else 503)


@router.get("/health/cameras")
def camera_health():
    from ...recognition.camera_service import camera_health_statuses
    fields = (
        "camera_id", "status", "last_frame_at", "last_error_at", "last_reconnect_at",
        "frames_read_total", "frames_failed_total", "reconnects_total",
        "consecutive_failures",
    )
    return {
        "status": "available",
        "cameras": [
            {field: item.get(field) for field in fields}
            for item in camera_health_statuses(CAMERA_INDEXES)
        ],
    }


@router.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
def metrics():
    """Small Prometheus surface without credentials, images or biometric data."""
    database_up = 0
    try:
        conn = get_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        database_up = 1
    except Exception:
        database_up = 0
    from ...recognition.camera_service import camera_health_statuses
    from ...observability.metrics import snapshot

    lines = [
        "# HELP amanat_up API process readiness.",
        "# TYPE amanat_up gauge",
        "amanat_up 1",
        "# HELP amanat_database_up PostgreSQL connectivity.",
        "# TYPE amanat_database_up gauge",
        f"amanat_database_up {database_up}",
        "# HELP amanat_release_info Release provenance (value is always one).",
        "# TYPE amanat_release_info gauge",
        f'amanat_release_info{{version="{RELEASE_VERSION.replace(chr(34), "").replace(chr(92), "")}"}} 1',
        "# HELP amanat_camera_connected Camera worker has an active stream.",
        "# TYPE amanat_camera_connected gauge",
        "# HELP amanat_camera_error Camera worker is in error state.",
        "# TYPE amanat_camera_error gauge",
        "# HELP amanat_camera_reconnect_seconds Seconds until the next reconnect attempt.",
        "# TYPE amanat_camera_reconnect_seconds gauge",
        "# HELP amanat_camera_last_frame_age_seconds Age of the latest frame.",
        "# TYPE amanat_camera_last_frame_age_seconds gauge",
    ]
    backup_timestamp = 0.0
    backup_size = 0
    backup_verified = 0
    if BACKUP_STATUS_FILE:
        try:
            last_line = Path(BACKUP_STATUS_FILE).read_text(encoding="utf-8").splitlines()[-1]
            stamp = last_line.split()[0]
            backup_timestamp = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            ).timestamp()
            backup_verified = 1 if " verified " in f" {last_line} " else 0
            parts = last_line.split()
            if len(parts) >= 3:
                metadata = Path(parts[2] + ".json")
                if metadata.is_file():
                    backup_size = int(json.loads(metadata.read_text(encoding="utf-8")).get("backup_size", 0))
        except (OSError, IndexError, ValueError):
            backup_timestamp = 0.0
    try:
        backend_ready = int(readiness_report().get("status") != "not_ready")
    except Exception:
        backend_ready = 0
    lines.extend([
        "# HELP amanat_backup_last_verified_timestamp_seconds Last successfully restored backup.",
        "# TYPE amanat_backup_last_verified_timestamp_seconds gauge",
        f"amanat_backup_last_verified_timestamp_seconds {backup_timestamp:.0f}",
        "# HELP amanat_backup_age_seconds Seconds since the last successfully restored backup.",
        "# TYPE amanat_backup_age_seconds gauge",
        f"amanat_backup_age_seconds {max(0.0, time.time() - backup_timestamp) if backup_timestamp else -1:.3f}",
        "# HELP amanat_backup_last_size_bytes Size of the last verified backup.",
        "# TYPE amanat_backup_last_size_bytes gauge",
        f"amanat_backup_last_size_bytes {backup_size}",
        "# HELP amanat_backup_last_verification_success Whether the latest backup passed restore verification.",
        "# TYPE amanat_backup_last_verification_success gauge",
        f"amanat_backup_last_verification_success {backup_verified}",
    ])
    offsite_success = 0
    if OFFSITE_BACKUP_STATUS_FILE:
        try:
            offsite_success = int(json.loads(Path(OFFSITE_BACKUP_STATUS_FILE).read_text(encoding="utf-8")).get("status") == "uploaded")
        except (OSError, ValueError):
            pass
    lines.extend([
        "# HELP amanat_offsite_backup_success Whether the latest offsite upload succeeded.",
        "# TYPE amanat_offsite_backup_success gauge",
        f"amanat_offsite_backup_success {offsite_success}",
    ])
    migration_ready = 0
    try:
        from ...db.migrations import migration_status
        migration_ready = int(migration_status().get("status") == "ready")
    except Exception:
        pass
    lines.extend([
        "# HELP amanat_migration_at_head Whether the database revision equals Alembic head.",
        "# TYPE amanat_migration_at_head gauge",
        f"amanat_migration_at_head {migration_ready}",
        "# HELP amanat_backend_ready Whether database and migration gates are ready.",
        "# TYPE amanat_backend_ready gauge",
        f"amanat_backend_ready {backend_ready}",
        f"amanat_disk_warning_threshold_ratio {DISK_WARNING_PERCENT / 100:.4f}",
        f"amanat_disk_critical_threshold_ratio {DISK_CRITICAL_PERCENT / 100:.4f}",
    ])
    for volume, configured_path in (
        ("database", "/monitored/postgres"),
        ("backup", str(Path(BACKUP_STATUS_FILE).parent) if BACKUP_STATUS_FILE else ""),
        ("logs", str(Path(__file__).resolve().parents[4] / "logs")),
        ("models", ANTI_SPOOFING_MODEL_PATH),
    ):
        try:
            probe = Path(configured_path)
            probe = probe if probe.is_dir() else probe.parent
            usage = shutil.disk_usage(probe)
            lines.append(f'amanat_volume_free_bytes{{volume="{volume}"}} {usage.free}')
            lines.append(f'amanat_volume_free_ratio{{volume="{volume}"}} {usage.free / usage.total:.6f}')
        except (OSError, ValueError, ZeroDivisionError):
            lines.append(f'amanat_volume_free_bytes{{volume="{volume}"}} -1')
            lines.append(f'amanat_volume_free_ratio{{volume="{volume}"}} -1')
    now = time.time()
    for item in camera_health_statuses(CAMERA_INDEXES):
        camera = item["camera"].replace('"', "")
        last_frame = item.get("last_frame_at")
        age = max(0.0, now - last_frame) if last_frame else -1
        lines.extend([
            f'amanat_camera_connected{{camera="{camera}"}} {1 if item["status"] == "HEALTHY" else 0}',
            f'amanat_camera_error{{camera="{camera}"}} {1 if item["status"] != "HEALTHY" else 0}',
            f'amanat_camera_reconnect_seconds{{camera="{camera}"}} {item["reconnect_in_seconds"]}',
            f'amanat_camera_last_frame_age_seconds{{camera="{camera}"}} {age:.3f}',
            f'amanat_camera_frames_total{{camera="{camera}"}} {item["frames_read_total"]}',
            f'amanat_camera_frame_failures_total{{camera="{camera}"}} {item["frames_failed_total"]}',
            f'amanat_camera_reconnects_total{{camera="{camera}"}} {item["reconnects_total"]}',
            f'amanat_camera_offline{{camera="{camera}"}} {1 if item["status"] == "OFFLINE" else 0}',
            f'amanat_camera_stale_total{{camera="{camera}"}} {item["stale_total"]}',
        ])
    counters, latencies = snapshot()
    for name in (
        "recognition_total", "recognition_unknown_total", "recognition_errors_total",
        "attendance_created_total", "attendance_duplicate_total", "attendance_errors_total",
        "pad_total", "pad_failures_total",
    ):
        lines.append(f"amanat_{name} {counters.get(name, 0)}")
    for name in (
        "face_detection_latency_ms", "pad_latency_ms", "recognition_latency_ms",
        "pipeline_latency_ms",
    ):
        values = latencies.get(name, {"count": 0, "sum": 0.0, "last": 0.0})
        average = values["sum"] / values["count"] if values["count"] else 0.0
        lines.extend([
            f"amanat_{name} {average:.6f}",
            f"amanat_{name}_count {int(values['count'])}",
            f"amanat_{name}_sum {values['sum']:.6f}",
        ])
    return "\n".join(lines) + "\n"
