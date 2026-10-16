import csv
import io

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..dependencies import require_admin, require_staff
from ...repositories import classes as class_repository
from ...repositories import school_attendance as repository
from ...services import school_attendance as service


router = APIRouter(prefix="/school-attendance", tags=["school-attendance"])


class ShiftPayload(BaseModel):
    name: str
    starts_at: str
    ends_at: str
    late_after: str


class SchedulePayload(BaseModel):
    class_id: str
    weekday: int
    shift_id: str


class CalendarPayload(BaseModel):
    date: str
    is_school_day: bool = False
    title: str = ""


class CorrectionPayload(BaseModel):
    student_id: str
    date: str
    status: str
    arrived_at: str | None = None
    left_at: str | None = None
    reason: str


@router.get("/shifts")
def shifts(request: Request):
    require_staff(request)
    return repository.list_shifts()


@router.post("/shifts", status_code=201)
def create_shift(payload: ShiftPayload, request: Request):
    require_admin(request)
    try:
        return service.create_shift(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/schedules")
def schedules(request: Request):
    require_staff(request)
    return repository.list_schedules()


@router.put("/schedules")
def set_schedule(payload: SchedulePayload, request: Request):
    require_admin(request)
    if not 0 <= payload.weekday <= 6:
        raise HTTPException(status_code=400, detail="weekday must be 0..6")
    result = repository.set_schedule(payload.class_id, payload.weekday, payload.shift_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Class or shift not found")
    return result


@router.put("/calendar")
def set_calendar(payload: CalendarPayload, request: Request):
    require_admin(request)
    try:
        day = service.parse_date(payload.date).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repository.set_calendar_day(day, payload.is_school_day, payload.title.strip())
    return {"status": "saved", "date": day}


@router.get("/calendar")
def calendar(request: Request, date_from: str, date_to: str):
    require_staff(request)
    try:
        start = service.parse_date(date_from)
        end = service.parse_date(date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if end < start or (end - start).days > 730:
        raise HTTPException(status_code=400, detail="Calendar range must be at most 731 days")
    return repository.list_calendar(start.isoformat(), end.isoformat())


@router.put("/corrections")
def correct(payload: CorrectionPayload, request: Request):
    session = require_admin(request)
    try:
        return service.correction(
            payload.student_id, payload.date, payload.status, payload.arrived_at,
            payload.left_at, payload.reason, session["login"],
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _report(request, date_from, date_to, class_id, student_id):
    session = require_staff(request)
    if session["role"] == "teacher":
        if not class_id:
            raise HTTPException(status_code=400, detail="Teachers must select one of their classes")
        school_class = class_repository.get_class(class_id)
        if school_class is None:
            raise HTTPException(status_code=404, detail="Class not found")
        if school_class["name"] not in session.get("classes", []):
            raise HTTPException(status_code=403, detail="Access denied")
    try:
        return service.report(date_from, date_to, class_id or None, student_id or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reports")
def report(request: Request, date_from: str, date_to: str, class_id: str = "", student_id: str = ""):
    return _report(request, date_from, date_to, class_id, student_id)


@router.get("/reports.csv")
def report_csv(request: Request, date_from: str, date_to: str, class_id: str = "", student_id: str = ""):
    rows = _report(request, date_from, date_to, class_id, student_id)
    output = io.StringIO()
    fields = ["date", "student_number", "student_name", "class_name", "shift", "status", "arrived_at", "left_at", "manual", "reason"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    data = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    return StreamingResponse(
        data, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=attendance-report.csv"},
    )


@router.get("/reports.xlsx")
def report_xlsx(request: Request, date_from: str, date_to: str, class_id: str = "", student_id: str = ""):
    rows = _report(request, date_from, date_to, class_id, student_id)
    from openpyxl import Workbook

    fields = ["date", "student_number", "student_name", "class_name", "shift", "status", "arrived_at", "left_at", "manual", "reason"]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Attendance"
    sheet.append(fields)
    for row in rows:
        sheet.append([row.get(field) for field in fields])
    for cell in sheet[1]:
        cell.font = cell.font.copy(bold=True)
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=attendance-report.xlsx"},
    )
