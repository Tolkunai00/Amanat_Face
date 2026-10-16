import importlib.util
import json
import logging
import os
from pathlib import Path

import pytest
import yaml

from school_face_id.api.routers import health
from school_face_id.observability.logging import JsonFormatter, redact


ROOT = Path(__file__).resolve().parents[2]


def load_preflight():
    path = ROOT / "deploy" / "production_preflight.py"
    spec = importlib.util.spec_from_file_location("production_preflight", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe_env():
    return {
        "DOMAIN": "school.example.org",
        "CORS_ALLOWED_ORIGINS": "https://school.example.org",
        "FACE_MODEL_NAME": "licensed-model",
        "RELEASE_VERSION": "v1.2.3",
        "ENVIRONMENT": "production",
        "DEBUG": "false",
        "AUTO_MIGRATE": "false",
        "FSVFM_SHADOW_MODE": "true",
    }


def test_missing_tls_certificate_blocks_deploy(tmp_path, monkeypatch):
    preflight = load_preflight()
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    with pytest.raises(preflight.ExternalBlock, match="TLS file not found"):
        preflight.check_tls(safe_env())


def test_mismatched_tls_key_is_rejected(tmp_path, monkeypatch):
    preflight = load_preflight()
    certs = tmp_path / "deploy" / "certs"
    certs.mkdir(parents=True)
    (certs / "fullchain.pem").write_text("certificate", encoding="utf-8")
    (certs / "privkey.pem").write_text("private key", encoding="utf-8")
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "openssl")

    def fake_openssl(_binary, args, input_bytes=None):
        if "-pubkey" in args:
            return b"certificate-public-key"
        if "-pubout" in args:
            return b"different-private-public-key"
        return b""

    monkeypatch.setattr(preflight, "run_openssl", fake_openssl)
    with pytest.raises(preflight.GateError, match="does not match"):
        preflight.check_tls(safe_env())


def write_secrets(root, values, mode=0o600):
    directory = root / "deploy" / "secrets"
    directory.mkdir(parents=True)
    for name, value in values.items():
        path = directory / name
        path.write_text(value, encoding="utf-8")
        path.chmod(mode)


def test_placeholder_secret_is_rejected(tmp_path, monkeypatch):
    preflight = load_preflight()
    write_secrets(tmp_path, {
        "postgres_password": "change-me-password-value-1234567890",
        "admin_bootstrap_password": "valid-admin-value-123",
        "session_secret": "valid-session-value-12345678901234567890",
    })
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    with pytest.raises(preflight.GateError, match="placeholder"):
        preflight.check_secrets({})


def test_world_readable_secret_is_rejected(tmp_path, monkeypatch):
    preflight = load_preflight()
    write_secrets(tmp_path, {
        "postgres_password": "valid-postgres-value-123456789012345",
        "admin_bootstrap_password": "valid-admin-value-123",
        "session_secret": "valid-session-value-12345678901234567890",
    }, mode=0o604)
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    with pytest.raises(preflight.GateError, match="permissions"):
        preflight.check_secrets({})


def test_alert_rules_parse_and_contain_required_alerts():
    parsed = yaml.safe_load((ROOT / "deploy/prometheus/rules/amanat.yml").read_text(encoding="utf-8"))
    names = {rule["alert"] for group in parsed["groups"] for rule in group["rules"]}
    required = {
        "BackendDown", "BackendNotReady", "DatabaseUnavailable", "MigrationMismatch",
        "CameraOffline", "CameraHighFailureRate", "CameraReconnectStorm",
        "RecognitionErrorRateHigh", "AttendanceErrorRateHigh", "PADFailureRateHigh",
        "HighPipelineLatency", "BackupTooOld", "OffsiteBackupFailure", "DiskSpaceLow",
    }
    assert required <= names


def test_failed_model_digest_is_rejected(tmp_path, monkeypatch):
    preflight = load_preflight()
    models = tmp_path / "deploy" / "models"
    models.mkdir(parents=True)
    (models / "anti_spoof.onnx").write_bytes(b"corrupt-minifas")
    (models / "fsvfm_vit_s_oci2m_run0.pth.tar").write_bytes(b"shadow-model")
    env = safe_env() | {
        "ANTI_SPOOFING_MODEL_SHA256": "0" * 64,
        "FSVFM_CHECKPOINT_SHA256": __import__("hashlib").sha256(b"shadow-model").hexdigest(),
    }
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    with pytest.raises(preflight.GateError, match="digest mismatch"):
        preflight.check_models(env)


def test_public_gateway_blocks_metrics_but_prometheus_scrapes_backend():
    nginx = (ROOT / "deploy/nginx/default.conf.template").read_text(encoding="utf-8")
    prometheus = (ROOT / "deploy/prometheus/prometheus.yml").read_text(encoding="utf-8")
    assert "location = /api/v1/metrics { return 404; }" in nginx
    assert 'targets: ["backend:8080"]' in prometheus


def test_structured_logging_redacts_credentials_and_has_required_fields():
    message = "password=hunter2 rtsp://admin:camera-secret@10.0.0.2/live token=abc"
    safe = redact(message)
    assert "hunter2" not in safe and "camera-secret" not in safe and "token=abc" not in safe
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, message, (), None)
    payload = json.loads(JsonFormatter().format(record))
    for field in ("timestamp", "level", "service", "event", "request_id", "camera_id", "track_id", "student_id", "latency_ms", "error_type"):
        assert field in payload
    assert "hunter2" not in payload["message"]


def test_release_version_exposed_safely(monkeypatch):
    monkeypatch.setattr(health, "RELEASE_VERSION", "release-2026.10.08")
    result = health.healthcheck()
    assert result["release_version"] == "release-2026.10.08"
    assert result["recognition"]["production_ready"] is False


def test_deploy_dry_run_branch_precedes_all_mutating_steps():
    script = (ROOT / "deploy/production_deploy.sh").read_text(encoding="utf-8")
    dry_exit = script.index('if [ "$dry_run" = 1 ]')
    backup = script.index('run --rm -e BACKUP_RUN_ONCE=1 backup')
    migration = script.index('python -m school_face_id.db.migrate upgrade')
    start = script.index(' up -d')
    assert dry_exit < backup < migration < start
    assert 'report BLOCKED_EXTERNAL production_backup' in script


def test_failed_backup_and_migration_stop_deploy():
    script = (ROOT / "deploy/production_deploy.sh").read_text(encoding="utf-8")
    assert "backup failed; migration was not attempted" in script
    assert "migration failed; release was not started" in script
    assert script.index("backup failed; migration was not attempted") < script.index("school_face_id.db.migrate upgrade")


def test_failed_model_validation_precedes_backup_and_migration():
    script = (ROOT / "deploy/production_deploy.sh").read_text(encoding="utf-8")
    assert script.index("preflight_gate models") < script.index("BACKUP_RUN_ONCE=1")
    assert script.index("preflight_gate models") < script.index("school_face_id.db.migrate upgrade")


def test_rollback_never_restores_database_implicitly():
    script = (ROOT / "deploy/production_rollback.sh").read_text(encoding="utf-8")
    assert 'restore_db=""' in script
    assert 'if [ -n "$restore_db" ]' in script
    assert "code rollback is not DB rollback" in script
