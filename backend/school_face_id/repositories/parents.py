"""Persistence operations for parent accounts and student links."""

import secrets

from ..config.passwords import hash_password
from ..db.connection import get_connection, is_integrity_error


def _parent(row):
    if row is None:
        return None
    return {
        "id": row[0], "login": row[1], "display_name": row[2], "phone": row[3],
        "is_active": bool(row[4]),
    }


def create_parent(login, display_name, phone, password):
    parent_id = secrets.token_hex(16)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO parent_accounts "
            "(id, login, display_name, phone, password_hash, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (parent_id, login, display_name, phone or None, hash_password(password), True),
        )
        conn.commit()
        return get_parent_by_id(parent_id)
    except Exception as exc:
        conn.rollback()
        if is_integrity_error(exc):
            return None
        raise
    finally:
        conn.close()


def get_parent_by_id(parent_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, login, display_name, phone, is_active FROM parent_accounts WHERE id = ?",
        (parent_id,),
    ).fetchone()
    conn.close()
    return _parent(row)


def get_parent_by_login(login):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, login, display_name, phone, is_active FROM parent_accounts "
        "WHERE login = ? COLLATE NOCASE",
        (login,),
    ).fetchone()
    conn.close()
    return _parent(row)


def get_parent_credentials_by_login(login):
    """Internal authentication projection; never expose this through an API."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, password_hash FROM parent_accounts "
        "WHERE login = ? COLLATE NOCASE AND is_active = ?", (login, True)
    ).fetchone()
    conn.close()
    return row


def list_parents():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, login, display_name, phone, is_active FROM parent_accounts "
        "ORDER BY display_name, login"
    ).fetchall()
    conn.close()
    return [_parent(row) for row in rows]


def update_parent(parent_id, login, display_name, phone):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE parent_accounts SET login = ?, display_name = ?, phone = ? WHERE id = ?",
            (login, display_name, phone or None, parent_id),
        )
        conn.commit()
        return get_parent_by_id(parent_id) if cursor.rowcount else None
    except Exception as exc:
        conn.rollback()
        if is_integrity_error(exc):
            return False
        raise
    finally:
        conn.close()


def change_parent_password(parent_id, new_password):
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE parent_accounts SET password_hash = ? WHERE id = ?",
        (hash_password(new_password), parent_id),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def set_parent_active(parent_id, is_active):
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE parent_accounts SET is_active = ? WHERE id = ?",
        (bool(is_active), parent_id),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def delete_parent(parent_id):
    conn = get_connection()
    conn.execute(
        "DELETE FROM notifications WHERE recipient_type = ? AND recipient_id = ?",
        ("parent", parent_id),
    )
    cursor = conn.execute("DELETE FROM parent_accounts WHERE id = ?", (parent_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def link_parent_to_student(parent_id, student_id, relationship):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO parent_students (parent_id, student_id, relationship) VALUES (?, ?, ?)",
            (parent_id, student_id, relationship),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as exc:
        conn.rollback()
        if is_integrity_error(exc):
            return False
        raise
    finally:
        conn.close()


def unlink_parent_from_student(parent_id, student_id):
    conn = get_connection()
    cursor = conn.execute(
        "DELETE FROM parent_students WHERE parent_id = ? AND student_id = ?", (parent_id, student_id)
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def get_students_for_parent(parent_id):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT s.id, s.student_number, s.name, COALESCE(c.name, ''), ps.relationship
        FROM parent_students ps JOIN students s ON s.id = ps.student_id
        LEFT JOIN classes c ON c.id = s.class_id
        WHERE ps.parent_id = ? ORDER BY c.name, s.name, s.id
        """, (parent_id,)
    ).fetchall()
    conn.close()
    return [
        {"id": row[0], "student_number": row[1], "name": row[2],
         "class_name": row[3], "relationship": row[4]}
        for row in rows
    ]


def get_parents_for_student(student_id):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT p.id, p.login, p.display_name, p.phone, ps.relationship
        FROM parent_students ps JOIN parent_accounts p ON p.id = ps.parent_id
        WHERE ps.student_id = ? ORDER BY p.display_name, p.login, p.id
        """, (student_id,)
    ).fetchall()
    conn.close()
    return [
        {"id": row[0], "login": row[1], "display_name": row[2],
         "phone": row[3], "relationship": row[4]}
        for row in rows
    ]
