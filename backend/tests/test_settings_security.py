import hashlib

import pytest

from school_face_id.config import settings


def configure_safe_production(monkeypatch, tmp_path):
    model = tmp_path / "anti_spoof.onnx"
    model.write_bytes(b"test-model-artifact")
    monkeypatch.setattr(settings, "PRODUCTION", True)
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "AUTO_MIGRATE", False)
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "postgresql://amanat:unique-database-secret@example.invalid/amanat",
    )
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "a-unique-admin-password")
    monkeypatch.setattr(settings, "SESSION_SECRET", "a-unique-session-secret-at-least-32-characters")
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", ["https://app.example.com"])
    monkeypatch.setattr(settings, "FACE_MODEL_LICENSE", "commercial")
    monkeypatch.setattr(settings, "ANTI_SPOOFING_MODEL_PATH", str(model))
    monkeypatch.setattr(
        settings, "ANTI_SPOOFING_MODEL_SHA256", hashlib.sha256(model.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(settings, "FSVFM_ENABLED", False)
    monkeypatch.setitem(
        settings.CAMERA_INDEXES,
        "keldi",
        "rtsp://camera-user:unique-secret@camera/stream",
    )
    return model


def test_development_configuration_does_not_require_secrets(monkeypatch):
    monkeypatch.setattr(settings, "PRODUCTION", False)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "")
    settings.validate_runtime_settings()


def test_production_rejects_missing_admin_password(monkeypatch, tmp_path):
    configure_safe_production(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "")
    with pytest.raises(settings.ProductionConfigError, match="ADMIN_PASSWORD"):
        settings.validate_runtime_settings()


def test_production_rejects_placeholder_secret(monkeypatch, tmp_path):
    configure_safe_production(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "CHANGE_ME_DIFFERENT_ADMIN_PASSWORD_32")
    with pytest.raises(settings.ProductionConfigError, match="ADMIN_PASSWORD"):
        settings.validate_runtime_settings()


def test_production_rejects_default_camera_credentials(monkeypatch, tmp_path):
    configure_safe_production(monkeypatch, tmp_path)
    monkeypatch.setitem(settings.CAMERA_INDEXES, "keldi", "rtsp://admin:admin@camera/stream")
    with pytest.raises(RuntimeError, match="CAMERA_KELDI_SOURCE"):
        settings.validate_runtime_settings()


def test_production_accepts_explicit_safe_values(monkeypatch, tmp_path):
    configure_safe_production(monkeypatch, tmp_path)
    settings.validate_runtime_settings()


def test_production_rejects_missing_pad_model(monkeypatch, tmp_path):
    configure_safe_production(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "ANTI_SPOOFING_MODEL_PATH", "")
    with pytest.raises(settings.ProductionConfigError, match="ANTI_SPOOFING_MODEL_PATH"):
        settings.validate_runtime_settings()


def test_both_camera_sources_accept_device_indexes_or_rtsp(monkeypatch):
    monkeypatch.setenv("CAMERA_TEST_SOURCE", "1")
    assert settings.env_camera_source("CAMERA_TEST_SOURCE", 0) == 1
    monkeypatch.setenv(
        "CAMERA_TEST_SOURCE", "rtsp://camera-user:secret@192.168.1.100:554/stream"
    )
    assert settings.env_camera_source("CAMERA_TEST_SOURCE", 0).startswith("rtsp://")


def test_negative_camera_index_has_clear_error(monkeypatch):
    monkeypatch.setenv("CAMERA_TEST_SOURCE", "-1")
    with pytest.raises(RuntimeError, match="zero or greater"):
        settings.env_camera_source("CAMERA_TEST_SOURCE", 0)


def test_invalid_pad_sha256_has_clear_configuration_error(monkeypatch):
    monkeypatch.setattr(settings, "PRODUCTION", False)
    monkeypatch.setattr(settings, "ANTI_SPOOFING_MODEL_SHA256", "not-a-sha256")
    with pytest.raises(RuntimeError, match="64-character hexadecimal"):
        settings.validate_runtime_settings()
