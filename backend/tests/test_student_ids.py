import sqlite3

import numpy as np

from school_face_id.data import database
from school_face_id.db import connection, migrations
from school_face_id.repositories import students


def embedding():
    return np.ones(512, dtype=np.float32)


def test_legacy_student_relations_receive_stable_id(tmp_path, monkeypatch):
    database_name = str(tmp_path / "legacy-student.db")
    monkeypatch.setattr(database, "DB_NAME", database_name)
    monkeypatch.setattr(connection, "DB_NAME", database_name)
    monkeypatch.setattr(migrations, "DB_NAME", database_name)
    conn = sqlite3.connect(database.DB_NAME)
    conn.execute("CREATE TABLE students (name TEXT PRIMARY KEY, class_name TEXT)")
    conn.execute("""
        CREATE TABLE attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, class_name TEXT,
            status TEXT, timestamp TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE face_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            angle_label TEXT, embedding BLOB NOT NULL, created_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO students (name, class_name) VALUES (?, ?)",
        ("Legacy Without Id", "9A"),
    )
    conn.execute(
        "INSERT INTO attendance (name, class_name, status, timestamp) VALUES (?, ?, ?, ?)",
        ("Legacy Without Id", "9A", "keldi", "2026-01-01 08:00:00"),
    )
    conn.execute(
        "INSERT INTO face_embeddings (name, angle_label, embedding) VALUES (?, ?, ?)",
        ("Legacy Without Id", "front", embedding().tobytes()),
    )
    conn.commit()
    conn.close()

    database.init_db()

    conn = sqlite3.connect(database.DB_NAME)
    student_id = conn.execute(
        "SELECT id FROM students WHERE name = ?", ("Legacy Without Id",)
    ).fetchone()[0]
    student_number = conn.execute(
        "SELECT student_number FROM students WHERE id = ?", (student_id,)
    ).fetchone()[0]
    attendance_id = conn.execute(
        "SELECT student_id FROM attendance WHERE student_id = ?", (student_id,)
    ).fetchone()[0]
    face_id = conn.execute(
        "SELECT student_id FROM face_embeddings WHERE student_id = ?", (student_id,)
    ).fetchone()[0]
    conn.close()

    assert student_id
    assert student_number == "AMANAT-000001"
    assert attendance_id == student_id
    assert face_id == student_id


def test_student_id_survives_rename_and_owns_relations():
    created = students.save_student(
        "Stable Id Student", "7A", "face_database/stable.jpg", embedding()
    )
    assert created
    student_id = created["id"]
    student_number = created["student_number"]
    assert student_id

    students.add_face_embedding(student_id, embedding(), "left")
    assert database.log_attendance(student_id, "Stable Id Student", "7A", "keldi")
    assert students.update_student_by_id(
        student_id, "Renamed Student", "7B"
    )

    student = students.get_student_by_id(student_id)
    assert student[0] == student_id
    assert student[1] == student_number
    assert student[2:4] == ("Renamed Student", "7B")

    conn = sqlite3.connect(database.DB_NAME)
    attendance = conn.execute(
        "SELECT student_id FROM attendance_events WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    face = conn.execute(
        "SELECT student_id FROM face_embeddings WHERE student_id = ? AND angle_label = 'left'",
        (student_id,),
    ).fetchone()
    conn.close()

    assert attendance == (student_id,)
    assert face == (student_id,)
