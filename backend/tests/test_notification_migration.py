import sqlite3

from alembic import command
from alembic.config import Config

from school_face_id.config.settings import BACKEND_ROOT
from school_face_id.data import database
from school_face_id.db import connection, migrations


def test_notification_migration_does_not_backfill_historical_events(tmp_path, monkeypatch):
    database_name = str(tmp_path / "notifications.db")
    monkeypatch.setattr(database, "DB_NAME", database_name)
    monkeypatch.setattr(connection, "DB_NAME", database_name)
    monkeypatch.setattr(migrations, "DB_NAME", database_name)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_name}")
    command.upgrade(config, "20260928_05")

    conn = sqlite3.connect(database_name)
    conn.execute("INSERT INTO classes (id, name) VALUES ('notification-class', 'Migration Class')")
    conn.execute(
        "INSERT INTO students (id, student_number, name, class_id) VALUES "
        "('notification-student', 'AMANAT-999991', 'Historical Student', 'notification-class')"
    )
    conn.execute(
        "INSERT INTO teachers (login, password_hash) VALUES ('historical-teacher', 'salt:digest')"
    )
    conn.execute(
        "INSERT INTO attendance_events "
        "(id, student_id, event_type, occurred_at, source, created_at) VALUES "
        "('historical-event', 'notification-student', 'enter', "
        "'2026-09-01T08:00:00+06:00', 'import', '2026-09-01T08:00:00+06:00')"
    )
    conn.commit()
    conn.close()

    command.upgrade(config, "head")

    conn = sqlite3.connect(database_name)
    teacher_id = conn.execute(
        "SELECT id FROM teachers WHERE login='historical-teacher'"
    ).fetchone()[0]
    notification_count = conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    event = conn.execute(
        "SELECT id, student_id FROM attendance_events WHERE id='historical-event'"
    ).fetchone()
    conn.close()

    assert teacher_id
    assert notification_count == 0
    assert event == ("historical-event", "notification-student")
