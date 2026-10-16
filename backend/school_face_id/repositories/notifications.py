"""Database operations for persistent in-app notifications."""

import secrets

from ..db.connection import get_connection


NOTIFICATION_SELECT = """
    SELECT n.id, n.recipient_type, n.recipient_id, n.notification_type, n.message,
           n.channel, n.is_read, n.read_at, n.created_at,
           s.id, s.student_number, s.name, COALESCE(c.name, ''),
           e.id, e.event_type, e.occurred_at
    FROM notifications n
    JOIN students s ON s.id=n.student_id
    LEFT JOIN classes c ON c.id=s.class_id
    JOIN attendance_events e ON e.id=n.attendance_event_id
"""


def _notification(row):
    if row is None:
        return None
    return {
        "id": row[0], "recipient_type": row[1], "recipient_id": row[2],
        "notification_type": row[3], "message": row[4], "channel": row[5],
        "is_read": bool(row[6]), "read_at": row[7], "created_at": row[8],
        "student": {"id": row[9], "student_number": row[10], "name": row[11],
                    "class_name": row[12]},
        "event": {"id": row[13], "event_type": row[14], "occurred_at": row[15]},
    }


def create_notification(
    recipient_type, recipient_id, student_id, attendance_event_id,
    notification_type, message, created_at, channel="in_app",
):
    notification_id = secrets.token_hex(16)
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO notifications
           (id, recipient_type, recipient_id, student_id, attendance_event_id,
            notification_type, message, channel, is_read, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(recipient_type, recipient_id, attendance_event_id, notification_type)
           DO NOTHING""",
        (notification_id, recipient_type, recipient_id, student_id, attendance_event_id,
         notification_type, message, channel, False, created_at),
    )
    created = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return get_notification_by_id(notification_id) if created else None


def get_notification_by_id(notification_id):
    conn = get_connection()
    row = conn.execute(
        NOTIFICATION_SELECT + " WHERE n.id=?", (notification_id,)
    ).fetchone()
    conn.close()
    return _notification(row)


def _list_notifications(recipient_type=None, recipient_id=None, unread_only=False, limit=50):
    clauses, params = [], []
    if recipient_type is not None:
        clauses.append("n.recipient_type=?")
        params.append(recipient_type)
    if recipient_id is not None:
        clauses.append("n.recipient_id=?")
        params.append(recipient_id)
    if unread_only:
        clauses.append("n.is_read=?")
        params.append(False)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    conn = get_connection()
    rows = conn.execute(
        NOTIFICATION_SELECT + where + " ORDER BY n.created_at DESC, n.id DESC LIMIT ?", params
    ).fetchall()
    conn.close()
    return [_notification(row) for row in rows]


def list_notifications_for_parent(parent_id, unread_only=False, limit=50):
    return _list_notifications("parent", parent_id, unread_only, limit)


def list_notifications_for_teacher(teacher_id, unread_only=False, limit=50):
    return _list_notifications("teacher", teacher_id, unread_only, limit)


def list_all_notifications(unread_only=False, limit=50):
    return _list_notifications(unread_only=unread_only, limit=limit)


def mark_notification_read(notification_id, recipient_type, recipient_id, read_at):
    conn = get_connection()
    cursor = conn.execute(
        """UPDATE notifications SET is_read=?, read_at=?
           WHERE id=? AND recipient_type=? AND recipient_id=?""",
        (True, read_at, notification_id, recipient_type, recipient_id),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def mark_all_read(recipient_type, recipient_id, read_at):
    conn = get_connection()
    cursor = conn.execute(
        """UPDATE notifications SET is_read=?, read_at=?
           WHERE recipient_type=? AND recipient_id=? AND is_read=?""",
        (True, read_at, recipient_type, recipient_id, False),
    )
    conn.commit()
    count = cursor.rowcount
    conn.close()
    return count


def count_unread(recipient_type=None, recipient_id=None):
    clauses, params = ["is_read=?"], [False]
    if recipient_type is not None:
        clauses.append("recipient_type=?")
        params.append(recipient_type)
    if recipient_id is not None:
        clauses.append("recipient_id=?")
        params.append(recipient_id)
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE " + " AND ".join(clauses), params
    ).fetchone()[0]
    conn.close()
    return count


def delete_notifications_for_event(event_id):
    conn = get_connection()
    cursor = conn.execute("DELETE FROM notifications WHERE attendance_event_id=?", (event_id,))
    conn.commit()
    count = cursor.rowcount
    conn.close()
    return count
