from datetime import date

from school_face_id.repositories import school_attendance as repository
from school_face_id.repositories import students
from school_face_id.repositories.classes import class_id_for_name
from school_face_id.services import attendance
from school_face_id.services import school_attendance


def test_schedule_derives_late_and_absent_and_manual_reason():
    class_name = "Schedule Test Class"
    first = students.save_student("Schedule Present Student", class_name, "", None)
    second = students.save_student("Schedule Absent Student", class_name, "", None)
    school_day = date(2026, 9, 28)  # Monday
    shift = school_attendance.create_shift("Schedule Test Morning", "08:00", "15:00", "08:15")
    repository.set_schedule(class_id_for_name(class_name), school_day.weekday(), shift["id"])
    attendance.register_event(
        first["id"], "enter", "2026-09-28T08:16:00+05:00", debounce_seconds=0
    )

    rows = school_attendance.report("2026-09-28", "2026-09-28")
    relevant = {row["student_id"]: row for row in rows if row["class_name"] == class_name}
    assert relevant[first["id"]]["status"] == "late"
    assert relevant[second["id"]]["status"] == "absent"

    school_attendance.correction(
        second["id"], "2026-09-28", "excused", None, None,
        "Medical certificate", "admin",
    )
    corrected = next(
        row for row in school_attendance.report("2026-09-28", "2026-09-28")
        if row["student_id"] == second["id"]
    )
    assert corrected["status"] == "excused"
    assert corrected["manual"] is True
    assert corrected["reason"] == "Medical certificate"


def test_correction_requires_a_reason():
    student = students.save_student("Reason Required Student", "Reason Class", "", None)
    try:
        school_attendance.correction(
            student["id"], "2026-09-28", "present", "08:00", None, "", "admin"
        )
    except ValueError as exc:
        assert "reason" in str(exc).lower()
    else:
        raise AssertionError("correction without a reason was accepted")
