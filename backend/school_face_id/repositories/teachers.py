"""Persistence for teacher identities and their class assignments."""

import secrets

from ..config.passwords import hash_password
from ..db.connection import get_connection, is_integrity_error


def _teacher(row):
    if row is None:
        return None
    return {
        "id": row[0], "login": row[1], "is_active": bool(row[2]),
        "full_name": row[3] or "", "phone": row[4], "last_login_at": row[5],
        "created_at": row[6],
    }


def login_exists(login, exclude_id=None):
    conn = get_connection()
    sql = "SELECT id FROM teachers WHERE login = ? COLLATE NOCASE"
    params = [(login or "").strip()]
    if exclude_id:
        sql += " AND id <> ?"
        params.append(exclude_id)
    row = conn.execute(sql, tuple(params)).fetchone()
    conn.close()
    return row is not None


def create_teacher(login, password, class_ids, full_name="", phone=None):
    teacher_id = secrets.token_hex(16)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO teachers (id, login, password_hash, is_active, full_name, phone) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (teacher_id, login, hash_password(password), True, full_name, phone),
        )
        _replace_classes(cursor, teacher_id, class_ids)
        conn.commit()
        return get_teacher_by_id(teacher_id)
    except Exception as exc:
        conn.rollback()
        if is_integrity_error(exc):
            return None
        raise
    finally:
        conn.close()


def update_teacher(teacher_id, login, class_ids, full_name=None, phone=None):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        changed = cursor.execute(
            "UPDATE teachers SET login = ?, full_name = COALESCE(?, full_name), "
            "phone = ? WHERE id = ?", (login, full_name, phone, teacher_id)
        ).rowcount
        if not changed:
            conn.rollback()
            return None
        _replace_classes(cursor, teacher_id, class_ids)
        conn.commit()
        return get_teacher_by_id(teacher_id)
    except Exception as exc:
        conn.rollback()
        if is_integrity_error(exc):
            return False
        raise
    finally:
        conn.close()


def _replace_classes(cursor, teacher_id, class_ids):
    cursor.execute("DELETE FROM teacher_classes WHERE teacher_id = ?", (teacher_id,))
    for class_id in class_ids:
        cursor.execute(
            "INSERT INTO teacher_classes (teacher_id, class_id) VALUES (?, ?)",
            (teacher_id, class_id),
        )


def change_teacher_password(teacher_id, password):
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE teachers SET password_hash = ? WHERE id = ?",
        (hash_password(password), teacher_id),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def set_teacher_active(teacher_id, is_active):
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE teachers SET is_active = ? WHERE id = ?", (bool(is_active), teacher_id)
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def get_teacher_by_id(teacher_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT id,login,is_active,full_name,phone,last_login_at,created_at "
        "FROM teachers WHERE id = ?", (teacher_id,)
    ).fetchone()
    conn.close()
    return _teacher(row)


def get_teacher_by_login(login):
    conn = get_connection()
    row = conn.execute(
        "SELECT id,login,is_active,full_name,phone,last_login_at,created_at "
        "FROM teachers WHERE login = ? COLLATE NOCASE",
        ((login or "").strip(),),
    ).fetchone()
    conn.close()
    return _teacher(row)


def get_teacher_credentials_by_login(login):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, login, password_hash FROM teachers "
        "WHERE login = ? COLLATE NOCASE AND is_active = ?",
        ((login or "").strip(), True),
    ).fetchone()
    conn.close()
    return row


def mark_teacher_login(teacher_id, occurred_at):
    conn = get_connection()
    conn.execute("UPDATE teachers SET last_login_at=? WHERE id=?", (occurred_at, teacher_id))
    conn.commit()
    conn.close()


def get_classes_for_teacher_id(teacher_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT c.name FROM teacher_classes tc JOIN classes c ON c.id=tc.class_id
           WHERE tc.teacher_id=? AND c.is_active=? ORDER BY c.name""",
        (teacher_id, True),
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_class_ids_for_teacher(teacher_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT class_id FROM teacher_classes WHERE teacher_id=? ORDER BY class_id",
        (teacher_id,),
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_teachers(include_inactive=True):
    conn = get_connection()
    sql = "SELECT id,login,is_active,full_name,phone,last_login_at,created_at FROM teachers"
    params = ()
    if not include_inactive:
        sql += " WHERE is_active = ?"
        params = (True,)
    rows = conn.execute(sql + " ORDER BY login", params).fetchall()
    result = []
    for row in rows:
        teacher = _teacher(row)
        teacher["class_ids"] = [item[0] for item in conn.execute(
            "SELECT class_id FROM teacher_classes WHERE teacher_id=? ORDER BY class_id",
            (teacher["id"],),
        ).fetchall()]
        teacher["class_names"] = [item[0] for item in conn.execute(
            """SELECT c.name FROM teacher_classes tc JOIN classes c ON c.id=tc.class_id
               WHERE tc.teacher_id=? ORDER BY c.name""", (teacher["id"],)
        ).fetchall()]
        result.append(teacher)
    conn.close()
    return result


def get_teachers_for_class(class_name):
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.id, t.login FROM teacher_classes tc
           JOIN teachers t ON t.id=tc.teacher_id
           JOIN classes c ON c.id=tc.class_id
           WHERE c.name=? AND t.is_active=? AND c.is_active=? ORDER BY t.login""",
        (class_name, True, True),
    ).fetchall()
    conn.close()
    return [{"id": row[0], "login": row[1]} for row in rows]


def delete_teacher(teacher_id):
    """Hard deletion is internal-only; APIs block accounts instead."""
    conn = get_connection()
    conn.execute(
        "DELETE FROM notifications WHERE recipient_type = ? AND recipient_id = ?",
        ("teacher", teacher_id),
    )
    cursor = conn.execute("DELETE FROM teachers WHERE id = ?", (teacher_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0
