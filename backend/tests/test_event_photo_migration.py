import sqlite3

from alembic import command
from alembic.config import Config

from school_face_id.config.settings import BACKEND_ROOT
from school_face_id.db import connection


def test_event_photo_migration_preserves_path_and_never_deletes_file(tmp_path, monkeypatch):
    database_name = str(tmp_path / "photo-retention.db")
    monkeypatch.setattr(connection, "DB_NAME", database_name)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_name}")
    command.upgrade(config, "20260928_06")

    physical_photo = tmp_path / "legacy-event.jpg"
    physical_photo.write_bytes(b"legacy-photo")
    conn = sqlite3.connect(database_name)
    conn.execute("INSERT INTO classes (id, name) VALUES ('photo-class', 'Photo Class')")
    conn.execute(
        "INSERT INTO students (id, student_number, name, class_id) VALUES "
        "('photo-student', 'AMANAT-999992', 'Photo Student', 'photo-class')"
    )
    conn.execute(
        "INSERT INTO attendance_events "
        "(id, student_id, event_type, occurred_at, source, photo_path, created_at) VALUES "
        "('legacy-photo-event', 'photo-student', 'enter', "
        "'2026-09-28T08:00:00+06:00', 'import', 'legacy-event.jpg', "
        "'2026-09-28T08:00:01+06:00')"
    )
    conn.commit()
    conn.close()

    command.upgrade(config, "head")

    conn = sqlite3.connect(database_name)
    row = conn.execute(
        "SELECT photo_path, photo_expires_at FROM attendance_events "
        "WHERE id='legacy-photo-event'"
    ).fetchone()
    indexes = {
        item[1] for item in conn.execute("PRAGMA index_list('attendance_events')").fetchall()
    }
    conn.close()

    assert row == ("legacy-event.jpg", "2026-10-05T08:00:00+06:00")
    assert "idx_attendance_events_photo_expires_at" in indexes
    assert physical_photo.read_bytes() == b"legacy-photo"
