import sqlite3

import numpy as np
from alembic import command
from alembic.config import Config

from school_face_id.config.settings import BACKEND_ROOT
from school_face_id.data import database
from school_face_id.db import connection, migrations


def test_parent_relation_migration_preserves_all_stable_ids_and_data(tmp_path, monkeypatch):
    database_name = str(tmp_path / "parent-relation.db")
    monkeypatch.setattr(database, "DB_NAME", database_name)
    monkeypatch.setattr(connection, "DB_NAME", database_name)
    monkeypatch.setattr(migrations, "DB_NAME", database_name)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_name}")
    command.upgrade(config, "20260927_03")

    parent_id = "stable-parent-id"
    student_id = "stable-student-id"
    password_hash = "preserved-salt:preserved-digest"
    embedding = np.ones(512, dtype=np.float32).tobytes()
    conn = sqlite3.connect(database_name)
    conn.execute(
        "INSERT INTO parent_accounts (id, login, display_name, password_hash) VALUES (?, ?, ?, ?)",
        (parent_id, "legacy-login", "Legacy Display", password_hash),
    )
    conn.execute("INSERT INTO classes (id, name) VALUES ('class-id', '12A')")
    conn.execute(
        "INSERT INTO students (id, student_number, name, class_id, parent_id) VALUES (?, ?, ?, ?, ?)",
        (student_id, "AMANAT-000001", "Legacy Linked Student", "class-id", parent_id),
    )
    conn.execute(
        "INSERT INTO face_embeddings (student_id, angle_label, embedding) VALUES (?, 'primary', ?)",
        (student_id, embedding),
    )
    conn.execute(
        "INSERT INTO attendance (student_id, status, timestamp) VALUES (?, 'keldi', '2026-09-28 08:00:00')",
        (student_id,),
    )
    conn.commit()
    conn.close()

    command.upgrade(config, "head")

    conn = sqlite3.connect(database_name)
    account = conn.execute(
        "SELECT id, password_hash, phone FROM parent_accounts WHERE id = ?", (parent_id,)
    ).fetchone()
    student = conn.execute("SELECT id FROM students WHERE id = ?", (student_id,)).fetchone()
    link = conn.execute(
        "SELECT parent_id, student_id, relationship FROM parent_students"
    ).fetchone()
    face = conn.execute("SELECT student_id, embedding FROM face_embeddings").fetchone()
    attendance = conn.execute("SELECT student_id FROM attendance").fetchone()
    conn.close()

    assert account == (parent_id, password_hash, None)
    assert student == (student_id,)
    assert link == (parent_id, student_id, "guardian")
    assert face == (student_id, embedding)
    assert attendance == (student_id,)
