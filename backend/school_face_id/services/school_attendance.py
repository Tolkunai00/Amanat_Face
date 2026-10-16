"""Derive school-day attendance from immutable entrance/exit events."""

from datetime import date, timedelta

from ..repositories import school_attendance as repository
from ..repositories import students as student_repository


STATUSES = {"present", "late", "absent", "excused"}


def parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Date must use YYYY-MM-DD") from exc


def validate_time(value, required=True):
    value = (value or "").strip()
    if not value and not required:
        return None
    parts = value.split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        raise ValueError("Time must use HH:MM or HH:MM:SS")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError("Invalid time")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def create_shift(name, starts_at, ends_at, late_after):
    name = (name or "").strip()
    if not name:
        raise ValueError("Shift name is required")
    result = repository.create_shift(
        name, validate_time(starts_at), validate_time(ends_at), validate_time(late_after)
    )
    if result is None:
        raise ValueError("Shift name already exists")
    return result


def correction(student_id, attendance_date, status, arrived_at, left_at, reason, actor):
    if student_repository.get_student_by_id(student_id) is None:
        raise LookupError("Student not found")
    day = parse_date(attendance_date).isoformat()
    status = (status or "").strip().lower()
    reason = (reason or "").strip()
    if status not in STATUSES:
        raise ValueError("Invalid attendance status")
    if len(reason) < 3:
        raise ValueError("Correction reason is required")
    repository.set_correction(
        student_id, day, status, validate_time(arrived_at, False),
        validate_time(left_at, False), reason, actor,
    )
    return {"status": "corrected"}


def report(date_from, date_to, class_id=None, student_id=None):
    start, end = parse_date(date_from), parse_date(date_to)
    if end < start or (end - start).days > 366:
        raise ValueError("Report range must be between 1 and 367 days")
    students, raw_events, raw_corrections = repository.report_inputs(
        start.isoformat(), end.isoformat(), class_id, student_id
    )
    schedules = {(x["class_id"], x["weekday"]): x for x in repository.list_schedules()}
    calendar = {
        x["date"]: x for x in repository.list_calendar(start.isoformat(), end.isoformat())
    }
    events = {}
    for sid, event_type, occurred_at in raw_events:
        key = (sid, occurred_at[:10])
        record = events.setdefault(key, {"enter": [], "exit": []})
        record[event_type].append(occurred_at[11:19])
    corrections = {
        (r[0], r[1]): {"status": r[2], "arrived_at": r[3], "left_at": r[4],
                       "reason": r[5], "actor_login": r[6]}
        for r in raw_corrections
    }
    result = []
    current = start
    while current <= end:
        day = current.isoformat()
        for sid, number, name, school_class_id, class_name in students:
            schedule = schedules.get((school_class_id, current.weekday()))
            calendar_day = calendar.get(day)
            is_school_day = calendar_day["is_school_day"] if calendar_day else bool(schedule)
            if not is_school_day and not ((sid, day) in corrections):
                continue
            event = events.get((sid, day), {"enter": [], "exit": []})
            arrived = min(event["enter"], default=None)
            left = max(event["exit"], default=None)
            status = "absent"
            if arrived:
                status = "late" if schedule and arrived > schedule["late_after"] else "present"
            manual = corrections.get((sid, day))
            if manual:
                status = manual["status"]
                arrived = manual["arrived_at"] if manual["arrived_at"] is not None else arrived
                left = manual["left_at"] if manual["left_at"] is not None else left
            result.append({
                "date": day, "student_id": sid, "student_number": number,
                "student_name": name, "class_id": school_class_id,
                "class_name": class_name, "shift": schedule["shift_name"] if schedule else None,
                "status": status, "arrived_at": arrived, "left_at": left,
                "calendar_title": calendar_day["title"] if calendar_day else "",
                "manual": bool(manual), "reason": manual["reason"] if manual else "",
                "corrected_by": manual["actor_login"] if manual else None,
            })
        current += timedelta(days=1)
    return result
