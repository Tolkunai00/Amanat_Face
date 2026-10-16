from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..dependencies import require_admin
from ...repositories import classes as class_repository
from ...repositories.auth import delete_auth_sessions_for_login
from ...repositories.teachers import get_teachers
from ...services import teachers as teacher_service


router = APIRouter(prefix="/teachers", tags=["teachers"])


class TeacherCreatePayload(BaseModel):
    login: str
    password: str
    class_ids: list[str] = Field(default_factory=list)
    class_names: list[str] = Field(default_factory=list)
    full_name: str = ""
    phone: str | None = None


class TeacherUpdatePayload(BaseModel):
    login: str
    class_ids: list[str] = Field(default_factory=list)
    class_names: list[str] = Field(default_factory=list)
    full_name: str | None = None
    phone: str | None = None


class TeacherPasswordPayload(BaseModel):
    new_password: str


class TeacherStatusPayload(BaseModel):
    is_active: bool


def _class_ids(payload):
    if payload.class_ids:
        return payload.class_ids
    requested = {name.strip() for name in payload.class_names if name.strip()}
    classes = class_repository.list_classes(include_inactive=True)
    return [item["id"] for item in classes if item["name"] in requested]


def _raise_service_error(exc):
    if isinstance(exc, teacher_service.TeacherNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, teacher_service.TeacherConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_teachers(request: Request):
    require_admin(request)
    return get_teachers()


@router.post("", status_code=201)
def create_teacher(payload: TeacherCreatePayload, request: Request):
    require_admin(request)
    try:
        return teacher_service.create_teacher(
            payload.login, payload.password, _class_ids(payload), payload.full_name, payload.phone
        )
    except (teacher_service.TeacherValidationError,
            teacher_service.TeacherConflictError) as exc:
        _raise_service_error(exc)


@router.put("/{teacher_id}")
def update_teacher(teacher_id: str, payload: TeacherUpdatePayload, request: Request):
    require_admin(request)
    try:
        updated = teacher_service.update_teacher(
            teacher_id, payload.login, _class_ids(payload), payload.full_name, payload.phone
        )
    except (teacher_service.TeacherValidationError, teacher_service.TeacherConflictError,
            teacher_service.TeacherNotFoundError) as exc:
        _raise_service_error(exc)
    delete_auth_sessions_for_login("teacher", updated["previous_login"])
    return {key: value for key, value in updated.items() if key != "previous_login"}


@router.put("/{teacher_id}/password")
def change_teacher_password(
    teacher_id: str, payload: TeacherPasswordPayload, request: Request,
):
    require_admin(request)
    try:
        teacher = teacher_service.change_password(teacher_id, payload.new_password)
    except (teacher_service.TeacherValidationError,
            teacher_service.TeacherNotFoundError) as exc:
        _raise_service_error(exc)
    delete_auth_sessions_for_login("teacher", teacher["login"])
    return {"status": "password_changed"}


@router.put("/{teacher_id}/status")
def set_teacher_status(
    teacher_id: str, payload: TeacherStatusPayload, request: Request,
):
    require_admin(request)
    try:
        teacher = teacher_service.set_active(teacher_id, payload.is_active)
    except teacher_service.TeacherNotFoundError as exc:
        _raise_service_error(exc)
    delete_auth_sessions_for_login("teacher", teacher["login"])
    return teacher


@router.delete("/{teacher_id}")
def block_teacher(teacher_id: str, request: Request):
    require_admin(request)
    try:
        teacher = teacher_service.set_active(teacher_id, False)
    except teacher_service.TeacherNotFoundError as exc:
        _raise_service_error(exc)
    delete_auth_sessions_for_login("teacher", teacher["login"])
    return {"status": "blocked", "id": teacher_id}
