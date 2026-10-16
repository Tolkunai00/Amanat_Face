import sqlite3

import numpy as np
from alembic import command
from alembic.config import Config

from school_face_id.config.settings import BACKEND_ROOT
from school_face_id.data import database
from school_face_id.db import connection, migrations


def test_daily_legacy_attendance_migrates_to_individual_events(tmp_path, monkeypatch):
    database_name = str(tmp_path / "attendance-events.db")
    monkeypatch.setattr(database, "DB_NAME", database_name)
    monkeypatch.setattr(connection, "DB_NAME", database_name)
    monkeypatch.setattr(migrations, "DB_NAME", database_name)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_name}")
    command.upgrade(config, "20260928_04")

    conn = sqlite3.connect(database_name)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE attendance")
    conn.execute(
        "CREATE TABLE attendance (id INTEGER PRIMARY KEY, student_id TEXT NOT NULL, "
        "date TEXT NOT NULL, arrived_at TEXT, left_at TEXT)"
    )
    conn.execute("INSERT INTO classes (id, name) VALUES ('legacy-class', 'Legacy Events')")
    conn.execute(
        "INSERT INTO parent_accounts (id, login, display_name, password_hash) "
        "VALUES ('legacy-parent', 'legacy-event-parent', 'Legacy Parent', 'salt:digest')"
    )
    for number, student_id, name in (
        (1, "legacy-event-a", "Legacy Event A"),
        (2, "legacy-event-b", "Legacy Event B"),
    ):
        conn.execute(
            "INSERT INTO students (id, student_number, name, class_id) VALUES (?, ?, ?, ?)",
            (student_id, f"AMANAT-{number:06d}", name, "legacy-class"),
        )
    conn.execute(
        "INSERT INTO parent_students (parent_id, student_id, relationship) "
        "VALUES ('legacy-parent', 'legacy-event-a', 'guardian')"
    )
    embedding = np.ones(512, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO face_embeddings (student_id, angle_label, embedding) "
        "VALUES ('legacy-event-a', 'primary', ?)", (embedding,)
    )
    conn.execute(
        "INSERT INTO attendance VALUES (1, 'legacy-event-a', '2026-09-28', '08:00', '15:00')"
    )
    conn.execute(
        "INSERT INTO attendance VALUES (2, 'legacy-event-b', '2026-09-28', '09:00', NULL)"
    )
    conn.commit()
    conn.close()

    command.upgrade(config, "head")

    conn = sqlite3.connect(database_name)
    events_a = conn.execute(
        "SELECT event_type, occurred_at FROM attendance_events "
        "WHERE student_id='legacy-event-a' ORDER BY occurred_at"
    ).fetchall()
    events_b = conn.execute(
        "SELECT event_type, occurred_at FROM attendance_events WHERE student_id='legacy-event-b'"
    ).fetchall()
    student_ids = {row[0] for row in conn.execute("SELECT id FROM students")}
    face = conn.execute("SELECT student_id, embedding FROM face_embeddings").fetchone()
    parent_link = conn.execute("SELECT parent_id, student_id FROM parent_students").fetchone()
    conn.close()

    assert events_a == [
        ("enter", "2026-09-28T08:00:00+06:00"),
        ("exit", "2026-09-28T15:00:00+06:00"),
    ]
    assert events_b == [("enter", "2026-09-28T09:00:00+06:00")]
    assert {"legacy-event-a", "legacy-event-b"}.issubset(student_ids)
    assert face == ("legacy-event-a", embedding)
    assert parent_link == ("legacy-parent", "legacy-event-a")
