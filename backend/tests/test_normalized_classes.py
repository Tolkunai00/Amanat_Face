import sqlite3

from school_face_id.data import database
from school_face_id.repositories.classes import class_id_for_name


def test_teacher_assignments_use_relation_table():
    assert database.save_teacher("normalized-teacher", "unique-password-123", ["10A", "10B"])
    assert database.authenticate_teacher("normalized-teacher", "unique-password-123") == ["10A", "10B"]

    assert database.save_teacher("normalized-teacher", "", ["10B"])
    assert database.authenticate_teacher("normalized-teacher", "unique-password-123") == ["10B"]

    conn = sqlite3.connect(database.DB_NAME)
    rows = conn.execute(
        """
        SELECT c.name FROM teacher_classes tc
        JOIN classes c ON c.id = tc.class_id
        WHERE tc.teacher_id = (SELECT id FROM teachers WHERE login = ?)
        """,
        ("normalized-teacher",),
    ).fetchall()
    conn.close()
    assert rows == [("10B",)]


def test_student_stores_normalized_class_id():
    class_name = "11C"
    class_id = class_id_for_name(class_name)
    cursor = database.get_connection()
    cursor.execute("INSERT OR IGNORE INTO classes (id, name) VALUES (?, ?)", (class_id, class_name))
    cursor.execute(
        "INSERT INTO students (id, student_number, name, class_id) VALUES (?, ?, ?, ?)",
        ("normalized-student-id", "AMANAT-900001", "Normalized Student", class_id),
    )
    cursor.commit()
    row = cursor.execute(
        """
        SELECT c.name FROM students s JOIN classes c ON c.id = s.class_id
        WHERE s.id = ?
        """,
        ("normalized-student-id",),
    ).fetchone()
    cursor.close()
    assert row == (class_name,)
