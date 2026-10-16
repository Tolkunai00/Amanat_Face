"""Database repository facade used by the API and recognition workers.

Schema changes belong to Alembic. All domain relations use stable identifiers;
names and class labels are presentation data only.
"""
from __future__ import annotations

import secrets

import numpy as np

from ..config.settings import DATABASE_URL, DB_NAME
from ..config.passwords import hash_password, validate_password, verify_password
from ..db.connection import get_connection, is_integrity_error
from ..db.migrations import init_db
from ..repositories.classes import class_id_for_name, ensure_class
from ..repositories.students import (
    add_face_embedding,
    count_face_embeddings,
    delete_face_embeddings,
    delete_student_by_id,
    get_all_students_list,
    get_all_students_with_ids,
    get_student_by_id,
    get_student_by_name,
    get_student_by_number,
    get_student_id_by_name,
    get_students_by_name,
    save_student,
    update_imported_student,
    update_student_by_id,
)

# Teacher repository

def save_teacher(login, password, class_names):
    """Compatibility facade; API code uses the separated teacher operations."""
    from ..repositories import teachers as teacher_repository

    login = login.strip()
    classes = list(dict.fromkeys(value.strip() for value in class_names if value.strip()))
    if not login or not classes:
        return False
    conn = get_connection()
    try:
        cur = conn.cursor()
        class_ids = [ensure_class(cur, class_name) for class_name in classes]
        conn.commit()
    finally:
        conn.close()
    existing = teacher_repository.get_teacher_by_login(login)
    if existing:
        return bool(teacher_repository.update_teacher(existing["id"], login, class_ids))
    if not password:
        return False
    try:
        validate_password(password)
    except ValueError:
        return False
    return teacher_repository.create_teacher(login, password, class_ids) is not None


def authenticate_teacher(login, password):
    from ..repositories.auth import authenticate_teacher_identity
    identity = authenticate_teacher_identity(login, password)
    return identity["classes"] if identity else None


def get_teachers():
    from ..repositories.teachers import get_teachers as list_teachers
    return [(row["login"], row["class_names"]) for row in list_teachers()]


def delete_teacher(login):
    from ..repositories import teachers as teacher_repository
    teacher = teacher_repository.get_teacher_by_login(login)
    return bool(teacher and teacher_repository.set_teacher_active(teacher["id"], False))


def load_recognition_embeddings():
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT s.id, s.name,
               COALESCE((SELECT ps.parent_id FROM parent_students ps
                         WHERE ps.student_id=s.id ORDER BY ps.created_at, ps.parent_id LIMIT 1), ''),
               COALESCE(c.name, ''), f.embedding
        FROM face_embeddings f
        JOIN students s ON s.id = f.student_id
        LEFT JOIN classes c ON c.id = s.class_id
        ORDER BY s.id, f.id
        """
    ).fetchall()
    conn.close()
    student_ids, names, parent_ids, embeddings, classes = [], [], [], [], []
    for student_id, name, parent_id, class_name, blob in rows:
        vector = np.frombuffer(blob, dtype=np.float32)
        if vector.size != 512 or not np.isfinite(vector).all():
            continue
        student_ids.append(student_id)
        names.append(name)
        parent_ids.append(parent_id)
        classes.append(class_name)
        embeddings.append(vector)
    return student_ids, names, parent_ids, embeddings, classes


# Attendance repository

def clear_attendance(class_name=None):
    from ..services.attendance import clear_attendance as clear_events
    return clear_events(class_name)


def log_attendance(student_id, name, class_name, status="keldi"):
    from ..services.attendance import log_attendance as register_legacy_status
    return register_legacy_status(student_id, name, class_name, status)


def _status_changes(rows, identity_index, status_index, timestamp_index):
    return rows


def get_recent_attendance(limit=50):
    from ..services.attendance import get_recent_attendance as recent_events
    return recent_events(limit)


def get_class_daily_report(class_name):
    from ..services.attendance import get_class_daily_report as class_report
    return class_report(class_name)


def get_parent_daily_report_by_id(parent_id):
    from ..services.attendance import get_parent_daily_report_by_id as parent_report
    return parent_report(parent_id)


def get_classes_summary():
    from ..services.attendance import get_classes_summary as summary
    return summary()


# Server-side sessions and persistent login throttling

def save_auth_session(token_hash, role, login, claims_json, created_at, expires_at):
    conn = get_connection()
    conn.execute(
        "INSERT INTO auth_sessions (token_hash, role, login, claims_json, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
        (token_hash, role, login, claims_json, int(created_at), int(expires_at)),
    )
    conn.commit(); conn.close()


def get_auth_session(token_hash):
    conn = get_connection()
    row = conn.execute(
        "SELECT role, login, claims_json, created_at, expires_at FROM auth_sessions WHERE token_hash = ?",
        (token_hash,),
    ).fetchone()
    conn.close(); return row


def delete_auth_session(token_hash):
    conn = get_connection(); conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,)); conn.commit(); conn.close()


def delete_auth_sessions_for_login(role, login):
    conn = get_connection(); conn.execute("DELETE FROM auth_sessions WHERE role = ? AND login = ?", (role, login)); conn.commit(); conn.close()


def delete_expired_auth_sessions(now):
    conn = get_connection(); conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (int(now),)); conn.commit(); conn.close()


def get_auth_rate_limit(rate_key):
    conn = get_connection()
    row = conn.execute(
        "SELECT attempts, window_started, blocked_until FROM auth_rate_limits WHERE rate_key = ?", (rate_key,)
    ).fetchone()
    conn.close(); return row


def record_auth_failure(rate_key, now, window_seconds=300, max_attempts=5, block_seconds=900):
    conn = get_connection()
    row = conn.execute(
        "SELECT attempts, window_started, blocked_until FROM auth_rate_limits WHERE rate_key = ?", (rate_key,)
    ).fetchone()
    if row is None or now - row[1] >= window_seconds:
        attempts, window_started = 1, int(now)
    else:
        attempts, window_started = row[0] + 1, row[1]
    blocked_until = int(now + block_seconds) if attempts >= max_attempts else 0
    conn.execute(
        """INSERT INTO auth_rate_limits (rate_key, attempts, window_started, blocked_until)
           VALUES (?, ?, ?, ?) ON CONFLICT(rate_key) DO UPDATE SET
           attempts=excluded.attempts, window_started=excluded.window_started,
           blocked_until=excluded.blocked_until""",
        (rate_key, attempts, window_started, blocked_until),
    )
    conn.commit(); conn.close(); return blocked_until


def clear_auth_failures(rate_key):
    conn = get_connection(); conn.execute("DELETE FROM auth_rate_limits WHERE rate_key = ?", (rate_key,)); conn.commit(); conn.close()
