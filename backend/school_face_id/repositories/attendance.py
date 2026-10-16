"""SQL persistence and projections for immutable attendance events."""

import secrets

from ..db.connection import get_connection


EVENT_SELECT = """
    SELECT e.id, e.student_id, s.student_number, s.name, COALESCE(c.name, ''),
           e.event_type, e.occurred_at, e.source, e.photo_path, e.photo_expires_at,
           e.confidence, e.created_at,
           COALESCE((SELECT p.display_name FROM parent_students ps
                     JOIN parent_accounts p ON p.id=ps.parent_id
                     WHERE ps.student_id=s.id ORDER BY ps.created_at, p.id LIMIT 1), '')
    FROM attendance_events e JOIN students s ON s.id=e.student_id
    LEFT JOIN classes c ON c.id=s.class_id
"""


def _event(row):
    if row is None:
        return None
    return {
        "id": row[0], "student_id": row[1], "student_number": row[2],
        "name": row[3], "class_name": row[4], "event_type": row[5],
        "occurred_at": row[6], "source": row[7], "photo_path": row[8],
        "photo_expires_at": row[9], "confidence": row[10],
        "created_at": row[11], "parent_name": row[12],
    }


def create_event(
    student_id, event_type, occurred_at, source="manual", photo_path=None,
    confidence=None, created_at=None,
):
    event_id = secrets.token_hex(16)
    conn = get_connection()
    conn.execute(
        "INSERT INTO attendance_events "
        "(id, student_id, event_type, occurred_at, source, photo_path, confidence, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, student_id, event_type, occurred_at, source, photo_path, confidence, created_at),
    )
    conn.commit()
    conn.close()
    return get_event_by_id(event_id)


def get_event_by_id(event_id):
    conn = get_connection()
    row = conn.execute(EVENT_SELECT + " WHERE e.id = ?", (event_id,)).fetchone()
    conn.close()
    return _event(row)


def set_event_photo(event_id, photo_path, photo_expires_at):
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE attendance_events SET photo_path = ?, photo_expires_at = ? WHERE id = ?",
        (photo_path, photo_expires_at, event_id),
    )
    conn.commit()
    changed = cursor.rowcount
    conn.close()
    return changed == 1


def clear_event_photo(event_id, expected_path=None):
    conn = get_connection()
    if expected_path is None:
        cursor = conn.execute(
            "UPDATE attendance_events SET photo_path = NULL, photo_expires_at = NULL "
            "WHERE id = ?",
            (event_id,),
        )
    else:
        cursor = conn.execute(
            "UPDATE attendance_events SET photo_path = NULL, photo_expires_at = NULL "
            "WHERE id = ? AND photo_path = ?",
            (event_id, expected_path),
        )
    conn.commit()
    changed = cursor.rowcount
    conn.close()
    return changed == 1


def list_expired_event_photos(now):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, photo_path, photo_expires_at FROM attendance_events "
        "WHERE photo_path IS NOT NULL AND photo_path <> '' "
        "AND photo_expires_at IS NOT NULL AND photo_expires_at <= ? "
        "ORDER BY photo_expires_at, id",
        (now,),
    ).fetchall()
    conn.close()
    return [
        {"id": row[0], "photo_path": row[1], "photo_expires_at": row[2]}
        for row in rows
    ]


def list_event_photo_references():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, photo_path FROM attendance_events "
        "WHERE photo_path IS NOT NULL AND photo_path <> ''"
    ).fetchall()
    conn.close()
    return [{"id": row[0], "photo_path": row[1]} for row in rows]


def get_recent_events(limit=50, student_id=None, class_name=None, event_date=None, event_type=None):
    clauses, params = [], []
    if student_id:
        clauses.append("e.student_id = ?")
        params.append(student_id)
    if class_name:
        clauses.append("c.name = ?")
        params.append(class_name)
    if event_date:
        clauses.append("substr(e.occurred_at, 1, 10) = ?")
        params.append(event_date)
    if event_type:
        clauses.append("e.event_type = ?")
        params.append(event_type)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    conn = get_connection()
    rows = conn.execute(
        EVENT_SELECT + where + " ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?", params
    ).fetchall()
    conn.close()
    return [_event(row) for row in rows]


def get_student_events(student_id, limit=500):
    return get_recent_events(limit=limit, student_id=student_id)


def get_student_events_for_day(student_id, event_date):
    return get_recent_events(limit=1000, student_id=student_id, event_date=event_date)


def get_class_events_for_day(class_name, event_date, limit=5000):
    return get_recent_events(limit=limit, class_name=class_name, event_date=event_date)


def get_parent_children_events(parent_id, limit=500, event_date=None):
    params = [parent_id]
    date_clause = ""
    if event_date:
        date_clause = " AND substr(e.occurred_at, 1, 10) = ?"
        params.append(event_date)
    params.append(limit)
    conn = get_connection()
    rows = conn.execute(
        EVENT_SELECT
        + " JOIN parent_students ps ON ps.student_id=e.student_id "
        + "WHERE ps.parent_id = ?" + date_clause
        + " ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?",
        params,
    ).fetchall()
    conn.close()
    return [_event(row) for row in rows]


def get_last_event_for_student(student_id):
    conn = get_connection()
    row = conn.execute(
        EVENT_SELECT + " WHERE e.student_id = ? ORDER BY e.occurred_at DESC, e.id DESC LIMIT 1",
        (student_id,),
    ).fetchone()
    conn.close()
    return _event(row)


def get_class_daily_report(class_name, today):
    conn = get_connection()
    rows = conn.execute(
        """
        WITH report_days AS (
            SELECT s.id AS student_id, ? AS report_date
            FROM students s JOIN classes c ON c.id=s.class_id WHERE c.name = ?
            UNION
            SELECT s.id, substr(e.occurred_at, 1, 10) FROM students s
            JOIN classes c ON c.id=s.class_id
            JOIN attendance_events e ON e.student_id=s.id WHERE c.name = ?
        )
        SELECT s.id, s.student_number, s.name, c.name, d.report_date,
               MIN(CASE WHEN e.event_type='enter' THEN e.occurred_at END),
               MAX(CASE WHEN e.event_type='exit' THEN e.occurred_at END),
               COALESCE((SELECT p.display_name FROM parent_students ps
                         JOIN parent_accounts p ON p.id=ps.parent_id
                         WHERE ps.student_id=s.id ORDER BY ps.created_at, p.id LIMIT 1), '')
        FROM report_days d JOIN students s ON s.id=d.student_id
        JOIN classes c ON c.id=s.class_id
        LEFT JOIN attendance_events e ON e.student_id=d.student_id
             AND substr(e.occurred_at, 1, 10)=d.report_date
        GROUP BY s.id, s.student_number, s.name, c.name, d.report_date
        ORDER BY d.report_date DESC, s.name, s.id
        """,
        (today, class_name, class_name),
    ).fetchall()
    conn.close()
    return rows


def get_parent_daily_report(parent_id, today):
    conn = get_connection()
    rows = conn.execute(
        """
        WITH report_days AS (
            SELECT s.id AS student_id, ? AS report_date
            FROM parent_students ps JOIN students s ON s.id=ps.student_id
            WHERE ps.parent_id = ?
            UNION
            SELECT s.id, substr(e.occurred_at, 1, 10) FROM parent_students ps
            JOIN students s ON s.id=ps.student_id
            JOIN attendance_events e ON e.student_id=s.id WHERE ps.parent_id = ?
        )
        SELECT s.id, s.student_number, s.name, COALESCE(c.name, ''), d.report_date,
               MIN(CASE WHEN e.event_type='enter' THEN e.occurred_at END),
               MAX(CASE WHEN e.event_type='exit' THEN e.occurred_at END)
        FROM report_days d JOIN students s ON s.id=d.student_id
        LEFT JOIN classes c ON c.id=s.class_id
        LEFT JOIN attendance_events e ON e.student_id=d.student_id
             AND substr(e.occurred_at, 1, 10)=d.report_date
        GROUP BY s.id, s.student_number, s.name, c.name, d.report_date
        ORDER BY d.report_date DESC, s.name, s.id
        """,
        (today, parent_id, parent_id),
    ).fetchall()
    conn.close()
    return rows


def get_classes_summary(today):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT c.name, COUNT(DISTINCT s.id),
               COUNT(DISTINCT CASE WHEN e.event_type='enter' THEN e.student_id END),
               COUNT(DISTINCT CASE WHEN e.event_type='exit' THEN e.student_id END)
        FROM classes c LEFT JOIN students s ON s.class_id=c.id
        LEFT JOIN attendance_events e ON e.student_id=s.id
             AND substr(e.occurred_at, 1, 10)=?
        GROUP BY c.id, c.name ORDER BY c.name
        """,
        (today,),
    ).fetchall()
    conn.close()
    return rows


def delete_events(class_name=None):
    conn = get_connection()
    if class_name:
        cursor = conn.execute(
            "DELETE FROM attendance_events WHERE student_id IN "
            "(SELECT s.id FROM students s JOIN classes c ON c.id=s.class_id WHERE c.name = ?)",
            (class_name,),
        )
    else:
        cursor = conn.execute("DELETE FROM attendance_events")
    conn.commit()
    count = cursor.rowcount
    conn.close()
    return count


def list_event_ids_for_range(date_from, date_to=None, limit=100000):
    clauses, params = ["substr(occurred_at, 1, 10) >= ?"], [date_from]
    if date_to:
        clauses.append("substr(occurred_at, 1, 10) <= ?")
        params.append(date_to)
    params.append(limit)
    conn = get_connection()
    rows = conn.execute(
        "SELECT id FROM attendance_events WHERE " + " AND ".join(clauses)
        + " ORDER BY occurred_at, id LIMIT ?",
        params,
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]
