"""Persistence for schedules, school calendar and manual attendance decisions."""

import secrets

from ..db.connection import get_connection, is_integrity_error


def _rows(sql, params=()):
    conn = get_connection()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def list_shifts():
    return [
        {"id": r[0], "name": r[1], "starts_at": r[2], "ends_at": r[3],
         "late_after": r[4], "is_active": bool(r[5])}
        for r in _rows(
            "SELECT id,name,starts_at,ends_at,late_after,is_active "
            "FROM school_shifts ORDER BY starts_at,name"
        )
    ]


def create_shift(name, starts_at, ends_at, late_after):
    shift_id = secrets.token_hex(16)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO school_shifts(id,name,starts_at,ends_at,late_after) VALUES(?,?,?,?,?)",
            (shift_id, name, starts_at, ends_at, late_after),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if is_integrity_error(exc):
            return None
        raise
    finally:
        conn.close()
    return next(item for item in list_shifts() if item["id"] == shift_id)


def list_schedules():
    return [
        {"id": r[0], "class_id": r[1], "class_name": r[2], "weekday": r[3],
         "shift_id": r[4], "shift_name": r[5], "starts_at": r[6],
         "ends_at": r[7], "late_after": r[8]}
        for r in _rows(
            "SELECT cs.id,cs.class_id,c.name,cs.weekday,cs.shift_id,sh.name,"
            "sh.starts_at,sh.ends_at,sh.late_after FROM class_schedules cs "
            "JOIN classes c ON c.id=cs.class_id JOIN school_shifts sh ON sh.id=cs.shift_id "
            "ORDER BY c.name,cs.weekday"
        )
    ]


def set_schedule(class_id, weekday, shift_id):
    schedule_id = secrets.token_hex(16)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO class_schedules(id,class_id,weekday,shift_id) VALUES(?,?,?,?) "
            "ON CONFLICT(class_id,weekday) DO UPDATE SET shift_id=excluded.shift_id",
            (schedule_id, class_id, weekday, shift_id),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if is_integrity_error(exc):
            return None
        raise
    finally:
        conn.close()
    return next(
        (item for item in list_schedules()
         if item["class_id"] == class_id and item["weekday"] == weekday), None
    )


def set_calendar_day(calendar_date, is_school_day, title):
    conn = get_connection()
    conn.execute(
        "INSERT INTO school_calendar(calendar_date,is_school_day,title) VALUES(?,?,?) "
        "ON CONFLICT(calendar_date) DO UPDATE SET "
        "is_school_day=excluded.is_school_day,title=excluded.title",
        (calendar_date, bool(is_school_day), title),
    )
    conn.commit()
    conn.close()


def list_calendar(date_from, date_to):
    return [
        {"date": r[0], "is_school_day": bool(r[1]), "title": r[2]}
        for r in _rows(
            "SELECT calendar_date,is_school_day,title FROM school_calendar "
            "WHERE calendar_date>=? AND calendar_date<=? ORDER BY calendar_date",
            (date_from, date_to),
        )
    ]


def set_correction(student_id, attendance_date, status, arrived_at, left_at, reason, actor):
    correction_id = secrets.token_hex(16)
    conn = get_connection()
    conn.execute(
        "INSERT INTO attendance_corrections"
        "(id,student_id,attendance_date,status,arrived_at,left_at,reason,actor_login) "
        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(student_id,attendance_date) DO UPDATE SET "
        "status=excluded.status,arrived_at=excluded.arrived_at,left_at=excluded.left_at,"
        "reason=excluded.reason,actor_login=excluded.actor_login,created_at=CURRENT_TIMESTAMP",
        (correction_id, student_id, attendance_date, status, arrived_at, left_at, reason, actor),
    )
    conn.commit()
    conn.close()


def report_inputs(date_from, date_to, class_id=None, student_id=None):
    clauses, params = [], []
    if class_id:
        clauses.append("s.class_id=?")
        params.append(class_id)
    if student_id:
        clauses.append("s.id=?")
        params.append(student_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    students = _rows(
        "SELECT s.id,s.student_number,s.name,s.class_id,COALESCE(c.name,'') "
        "FROM students s LEFT JOIN classes c ON c.id=s.class_id" + where + " ORDER BY s.name",
        params,
    )
    events = _rows(
        "SELECT e.student_id,e.event_type,e.occurred_at FROM attendance_events e "
        "WHERE substr(e.occurred_at,1,10)>=? AND substr(e.occurred_at,1,10)<=? "
        "ORDER BY e.occurred_at",
        (date_from, date_to),
    )
    corrections = _rows(
        "SELECT student_id,attendance_date,status,arrived_at,left_at,reason,actor_login "
        "FROM attendance_corrections WHERE attendance_date>=? AND attendance_date<=?",
        (date_from, date_to),
    )
    return students, events, corrections
