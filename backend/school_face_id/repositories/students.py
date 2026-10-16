"""Student and face-gallery persistence operations."""

import secrets

import numpy as np

from ..db.connection import PostgresCursor, get_connection, is_integrity_error
from .classes import ensure_class


def generate_student_number(cursor) -> str:
    if isinstance(cursor, PostgresCursor):
        number = cursor.execute("SELECT nextval('student_number_seq')").fetchone()[0]
    else:
        cursor.execute(
            "UPDATE student_number_counters SET next_value = next_value + 1 "
            "WHERE counter_name = 'student'"
        )
        row = cursor.execute(
            "SELECT next_value - 1 FROM student_number_counters "
            "WHERE counter_name = 'student'"
        ).fetchone()
        if row is None:
            raise RuntimeError("Student number counter is not initialized")
        number = row[0]
    return f"AMANAT-{number:06d}"


def save_student(name, class_name, photo_path, embedding):
    conn = get_connection()
    try:
        cur = conn.cursor()
        student_id = secrets.token_hex(16)
        student_number = generate_student_number(cur)
        cur.execute(
            "INSERT INTO students (id, student_number, name, class_id, photo_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (student_id, student_number, name, ensure_class(cur, class_name), photo_path),
        )
        if embedding is not None:
            cur.execute(
                "INSERT INTO face_embeddings (student_id, angle_label, embedding) VALUES (?, 'primary', ?)",
                (student_id, np.asarray(embedding, dtype=np.float32).tobytes()),
            )
        conn.commit()
        return {"id": student_id, "student_number": student_number}
    except Exception as exc:
        conn.rollback()
        if not isinstance(exc, ValueError) and not is_integrity_error(exc):
            raise
        return False
    finally:
        conn.close()


def add_face_embedding(student_id, embedding, angle_label="front"):
    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM students WHERE id = ?", (student_id,)).fetchone() is None:
            raise ValueError("Student not found")
        conn.execute(
            "INSERT INTO face_embeddings (student_id, angle_label, embedding) VALUES (?, ?, ?)",
            (student_id, angle_label, np.asarray(embedding, dtype=np.float32).tobytes()),
        )
        conn.commit()
    finally:
        conn.close()


def delete_face_embeddings(student_id):
    conn = get_connection()
    conn.execute("DELETE FROM face_embeddings WHERE student_id = ?", (student_id,))
    conn.commit()
    conn.close()


def count_face_embeddings(student_id):
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM face_embeddings WHERE student_id = ?", (student_id,)
    ).fetchone()[0]
    conn.close()
    return count


def delete_invalid_face_embeddings():
    """Remove empty, malformed, non-finite, and zero-length recognition vectors."""
    conn = get_connection()
    rows = conn.execute("SELECT id, embedding FROM face_embeddings").fetchall()
    invalid_ids = []
    for embedding_id, blob in rows:
        try:
            vector = np.frombuffer(bytes(blob), dtype=np.float32)
            valid = vector.size == 512 and np.isfinite(vector).all() and np.linalg.norm(vector) > 1e-6
        except (TypeError, ValueError):
            valid = False
        if not valid:
            invalid_ids.append(embedding_id)
    for embedding_id in invalid_ids:
        conn.execute("DELETE FROM face_embeddings WHERE id = ?", (embedding_id,))
    conn.commit()
    conn.close()
    return len(invalid_ids)


def _student_select(where="", params=()):
    conn = get_connection()
    rows = conn.execute(f"""
        SELECT s.id, s.student_number, s.name, COALESCE(c.name, ''), s.photo_path
        FROM students s
        LEFT JOIN classes c ON c.id = s.class_id
        {where}
        ORDER BY c.name, s.name
    """, params).fetchall()
    conn.close()
    return rows


def get_all_students_with_ids():
    return _student_select()


def get_all_students_list():
    return [(name, class_name, photo) for _, _, name, class_name, photo in _student_select()]


def get_student_id_by_name(name):
    rows = get_students_by_name(name)
    return rows[0][0] if len(rows) == 1 else None


def get_students_by_name(name):
    return _student_select("WHERE s.name = ?", (name,))


def _primary_embedding(conn, student_id):
    row = conn.execute(
        """SELECT embedding FROM face_embeddings WHERE student_id = ?
           ORDER BY CASE angle_label WHEN 'primary' THEN 0 ELSE 1 END, id LIMIT 1""",
        (student_id,),
    ).fetchone()
    return row[0] if row else None


def get_student_by_id(student_id):
    rows = _student_select("WHERE s.id = ?", (student_id,))
    if not rows:
        return None
    conn = get_connection()
    embedding = _primary_embedding(conn, student_id)
    conn.close()
    return (*rows[0], embedding)


def get_student_by_number(student_number):
    rows = _student_select("WHERE s.student_number = ?", (student_number,))
    if not rows:
        return None
    conn = get_connection()
    embedding = _primary_embedding(conn, rows[0][0])
    conn.close()
    return (*rows[0], embedding)


def get_student_by_name(name):
    rows = get_students_by_name(name)
    if len(rows) != 1:
        return None
    student_id, _, student_name, class_name, photo = rows[0]
    conn = get_connection()
    embedding = _primary_embedding(conn, student_id)
    conn.close()
    return student_name, class_name, photo, embedding


def update_imported_student(student_id, class_name, photo_path, embedding):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE students SET class_id = ?, photo_path = ?, updated_at = datetime('now') WHERE id = ?",
            (ensure_class(cur, class_name), photo_path, student_id),
        )
        if cur.rowcount == 0:
            return False
        cur.execute("DELETE FROM face_embeddings WHERE student_id = ? AND angle_label = 'primary'", (student_id,))
        cur.execute(
            "INSERT INTO face_embeddings (student_id, angle_label, embedding) VALUES (?, 'primary', ?)",
            (student_id, np.asarray(embedding, dtype=np.float32).tobytes()),
        )
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        if not is_integrity_error(exc):
            raise
        return False
    finally:
        conn.close()


def update_student_by_id(student_id, name, class_name, photo_path=None, embedding=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        current = cur.execute("SELECT id FROM students WHERE id = ?", (student_id,)).fetchone()
        if current is None:
            return False
        fields = ["name = ?", "class_id = ?", "updated_at = datetime('now')"]
        values = [name, ensure_class(cur, class_name)]
        if photo_path is not None:
            fields.append("photo_path = ?")
            values.append(photo_path)
        values.append(student_id)
        cur.execute(f"UPDATE students SET {', '.join(fields)} WHERE id = ?", values)
        if embedding is not None:
            cur.execute("DELETE FROM face_embeddings WHERE student_id = ? AND angle_label = 'primary'", (student_id,))
            cur.execute(
                "INSERT INTO face_embeddings (student_id, angle_label, embedding) VALUES (?, 'primary', ?)",
                (student_id, np.asarray(embedding, dtype=np.float32).tobytes()),
            )
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        if not isinstance(exc, ValueError) and not is_integrity_error(exc):
            raise
        return False
    finally:
        conn.close()


def delete_student_by_id(student_id):
    student = get_student_by_id(student_id)
    if student is None:
        return None
    conn = get_connection()
    conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
    conn.commit()
    conn.close()
    return student
