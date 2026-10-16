import sqlite3

import numpy as np

from school_face_id.data import database
from school_face_id.db import connection, migrations
from school_face_id.repositories import auth, parents, students
from school_face_id.services import parents as parent_service


def test_legacy_plaintext_parent_code_is_migrated(tmp_path, monkeypatch):
    database_name = str(tmp_path / "legacy-parent.db")
    monkeypatch.setattr(database, "DB_NAME", database_name)
    monkeypatch.setattr(connection, "DB_NAME", database_name)
    monkeypatch.setattr(migrations, "DB_NAME", database_name)
    conn = sqlite3.connect(database.DB_NAME)
    conn.execute("""
        CREATE TABLE students (
            name TEXT PRIMARY KEY, class_name TEXT, parent_name TEXT,
            parent_code TEXT, role TEXT
        )
    """)
    conn.execute("CREATE TABLE parents (code TEXT PRIMARY KEY, name TEXT, role TEXT)")
    conn.execute(
        """
        INSERT INTO students (name, class_name, parent_name, parent_code, role)
        VALUES ('Legacy Student', '7A', 'Legacy Parent', 'legacy-secret', 'student')
        """
    )
    conn.execute("INSERT INTO parents (code, name, role) VALUES ('legacy-secret', 'Legacy Parent', 'parent')")
    conn.commit()
    conn.close()

    database.init_db()

    conn = sqlite3.connect(database.DB_NAME)
    student = conn.execute(
        "SELECT parent_id FROM students WHERE name = 'Legacy Student'"
    ).fetchone()
    link = conn.execute(
        "SELECT parent_id, relationship FROM parent_students WHERE student_id = "
        "(SELECT id FROM students WHERE name = 'Legacy Student')"
    ).fetchone()
    student_columns = {row[1] for row in conn.execute("PRAGMA table_info(students)")}
    account = conn.execute(
        "SELECT id, password_hash FROM parent_accounts WHERE login = 'Legacy Parent'"
    ).fetchone()
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()

    assert student[0] == account[0]
    assert link == (account[0], "guardian")
    assert "parent_code" not in student_columns
    assert account[1] != "legacy-secret"
    assert ":" in account[1]
    assert "parents" not in tables
    assert auth.authenticate_parent("Legacy Parent", "legacy-secret") == account[0]
    assert auth.authenticate_parent("Legacy Parent", "wrong") is None


def test_new_parent_password_is_hashed_and_not_returned_with_student():
    created = students.save_student(
        "New Student",
        "8B",
        "face_database/new.jpg",
        np.zeros(512, dtype=np.float32),
    )
    assert created
    parent = parent_service.create_parent(
        "new-parent", "New Parent", "+996 700 123 456", "new-parent-secret"
    )
    parent_service.link_parent_to_student(parent["id"], created["id"], "guardian")
    parent_id = parent["id"]
    assert parent_id

    conn = sqlite3.connect(database.DB_NAME)
    password_hash = conn.execute(
        "SELECT password_hash FROM parent_accounts WHERE id = ?", (parent_id,)
    ).fetchone()[0]
    student_columns = {row[1] for row in conn.execute("PRAGMA table_info(students)")}
    conn.close()

    assert password_hash != "new-parent-secret"
    assert "parent_code" not in student_columns
    assert auth.authenticate_parent("new-parent", "new-parent-secret") == parent_id
    assert parents.get_parent_by_id(parent_id)["phone"] == "+996700123456"
