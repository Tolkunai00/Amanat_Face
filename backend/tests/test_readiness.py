import asyncio

import pytest
from fastapi.testclient import TestClient

from school_face_id import main
from school_face_id.api.routers import health
from school_face_id.config import settings
from school_face_id.db import migrations
from school_face_id.main import app
from school_face_id.recognition import anti_spoof, camera_service, fsvfm_pad


class ReadyConnection:
    def execute(self, _sql):
        return self

    def fetchone(self):
        return (1,)

    def close(self):
        pass


def configure_ready_components(monkeypatch, fsvfm_status="ready"):
    monkeypatch.setattr(settings, "validate_runtime_settings", lambda **_kwargs: None)
    monkeypatch.setattr(health, "get_connection", lambda: ReadyConnection())
    monkeypatch.setattr(migrations, "migration_status", lambda: {"status": "ready"})
    monkeypatch.setattr(camera_service, "identity_model_status", lambda: {"status": "ready"})
    monkeypatch.setattr(anti_spoof, "anti_spoof_status", lambda: {"status": "ready"})
    monkeypatch.setattr(
        fsvfm_pad,
        "fsvfm_status",
        lambda: {"status": fsvfm_status, "enforced": False},
    )


def test_health_live_is_independent_of_dependencies(monkeypatch):
    monkeypatch.setattr(health, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError()))
    response = TestClient(app).get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "live"


def test_health_ready_reports_ready(monkeypatch):
    configure_ready_components(monkeypatch)
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_missing_fsvfm_in_shadow_mode_is_degraded(monkeypatch):
    monkeypatch.setattr(fsvfm_pad, "FSVFM_ENABLED", True)
    monkeypatch.setattr(fsvfm_pad, "FSVFM_SHADOW_MODE", True)
    monkeypatch.setattr(fsvfm_pad, "FSVFM_CHECKPOINT", "")
    monkeypatch.setattr(fsvfm_pad, "_provider", None)
    monkeypatch.setattr(fsvfm_pad, "_provider_error", "")
    result = fsvfm_pad.initialize_fsvfm_for_startup()
    assert result["status"] == "degraded"


def test_missing_fsvfm_in_enforcement_mode_is_not_ready(monkeypatch):
    monkeypatch.setattr(fsvfm_pad, "FSVFM_ENABLED", True)
    monkeypatch.setattr(fsvfm_pad, "FSVFM_SHADOW_MODE", False)
    monkeypatch.setattr(fsvfm_pad, "FSVFM_CHECKPOINT", "")
    monkeypatch.setattr(fsvfm_pad, "_provider", None)
    monkeypatch.setattr(fsvfm_pad, "_provider_error", "")
    with pytest.raises(RuntimeError, match="FSVFM_CHECKPOINT"):
        fsvfm_pad.initialize_fsvfm_for_startup()
    assert fsvfm_pad.fsvfm_status()["status"] == "not_ready"


def test_shadow_fsvfm_degradation_keeps_readiness_http_200(monkeypatch):
    configure_ready_components(monkeypatch, fsvfm_status="degraded")
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_missing_arcface_is_not_ready(monkeypatch):
    configure_ready_components(monkeypatch)
    monkeypatch.setattr(
        camera_service,
        "identity_model_status",
        lambda: {"status": "not_ready", "error": "model unavailable"},
    )
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["components"]["identity_model"]["status"] == "not_ready"


def test_database_unavailable_is_not_ready(monkeypatch):
    configure_ready_components(monkeypatch)
    monkeypatch.setattr(
        health, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("secret-url"))
    )
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["components"]["database"] == {
        "status": "not_ready", "error": "RuntimeError"
    }
    assert "secret-url" not in response.text


def test_migration_behind_head_is_not_ready(monkeypatch):
    configure_ready_components(monkeypatch)
    monkeypatch.setattr(
        migrations,
        "migration_status",
        lambda: {"status": "not_ready", "current": ["old"], "head": ["new"]},
    )
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["components"]["migrations"]["status"] == "not_ready"


def test_production_api_startup_does_not_run_alembic_upgrade(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(main, "validate_runtime_settings", lambda: None)
    monkeypatch.setattr(main, "AUTO_MIGRATE", False)
    monkeypatch.setattr(main, "init_db", lambda: calls.append("upgrade"))
    monkeypatch.setattr(main, "FACE_DIR", str(tmp_path / "faces"))
    monkeypatch.setattr(main, "SCREENSHOTS_DIR", str(tmp_path / "screens"))
    monkeypatch.setattr(main, "CAMERA_INDEXES", {})
    monkeypatch.setattr(main, "WARM_UP_RECOGNITION", False)
    monkeypatch.setattr(settings, "ANTI_SPOOFING_MODEL_PATH", "")
    monkeypatch.setattr(
        "school_face_id.repositories.students.delete_invalid_face_embeddings", lambda: 0
    )
    monkeypatch.setattr(fsvfm_pad, "initialize_fsvfm_for_startup", lambda: {"status": "disabled"})
    monkeypatch.setattr(camera_service, "start_camera_workers", lambda _sources: None)
    monkeypatch.setattr(camera_service, "shutdown_camera_workers", lambda: None)

    async def run_lifespan():
        async with main.lifespan(None):
            pass

    asyncio.run(run_lifespan())
    assert calls == []
