from school_face_id.api.routers import health


def test_metrics_include_database_cameras_and_verified_backup(tmp_path, monkeypatch):
    status_file = tmp_path / "backup-status.log"
    status_file.write_text(
        "20260929T112649Z verified /backups/amanat.dump\n", encoding="utf-8"
    )
    monkeypatch.setattr(health, "BACKUP_STATUS_FILE", str(status_file))
    monkeypatch.setattr(health, "CAMERA_INDEXES", {})
    output = health.metrics()
    assert "amanat_database_up 1" in output
    assert "amanat_backup_last_verified_timestamp_seconds 0" not in output
    assert "amanat_backup_age_seconds" in output
    assert "amanat_recognition_total" in output
    assert "amanat_attendance_created_total" in output
    assert "amanat_face_detection_latency_ms" in output
