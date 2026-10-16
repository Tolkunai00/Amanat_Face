"""Class persistence helpers."""

import hashlib

from ..db.connection import get_connection, is_integrity_error


def class_id_for_name(class_name):
    normalized = (class_name or "").strip()
    return hashlib.sha256(f"class:{normalized}".encode("utf-8")).hexdigest()[:32]


def ensure_class(cursor, class_name):
    class_name = (class_name or "").strip()
    if not class_name:
        return None
    class_id = class_id_for_name(class_name)
    cursor.execute("INSERT OR IGNORE INTO classes (id, name) VALUES (?, ?)", (class_id, class_name))
    return class_id


def list_classes(include_inactive=False):
    conn = get_connection()
    sql = "SELECT id, name, is_active FROM classes"
    params = ()
    if not include_inactive:
        sql += " WHERE is_active = ?"
        params = (True,)
    rows = conn.execute(sql + " ORDER BY name", params).fetchall()
    conn.close()
    return [{"id": row[0], "name": row[1], "is_active": bool(row[2])} for row in rows]


def get_class(class_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, name, is_active FROM classes WHERE id = ?", (class_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "is_active": bool(row[2])}


def get_classes_by_ids(class_ids):
    return [item for class_id in class_ids if (item := get_class(class_id)) is not None]


def create_class(name):
    normalized = (name or "").strip()
    class_id = class_id_for_name(normalized)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO classes (id, name, is_active) VALUES (?, ?, ?)",
            (class_id, normalized, True),
        )
        conn.commit()
        return get_class(class_id)
    except Exception as exc:
        conn.rollback()
        if is_integrity_error(exc):
            return None
        raise
    finally:
        conn.close()


def rename_class(class_id, name):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE classes SET name = ? WHERE id = ?", ((name or "").strip(), class_id)
        )
        conn.commit()
        return get_class(class_id) if cursor.rowcount else None
    except Exception as exc:
        conn.rollback()
        if is_integrity_error(exc):
            return False
        raise
    finally:
        conn.close()


def set_class_active(class_id, is_active):
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE classes SET is_active = ? WHERE id = ?", (bool(is_active), class_id)
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0
