from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..dependencies import require_admin
from ...config.security import require_session
from ...repositories.auth import delete_auth_sessions_for_login
from ...services import parents as parent_service


router = APIRouter(prefix="/parents", tags=["parents"])


class ParentCreatePayload(BaseModel):
    login: str
    display_name: str
    phone: str | None = None
    password: str


class ParentUpdatePayload(BaseModel):
    login: str
    display_name: str
    phone: str | None = None


class ParentPasswordPayload(BaseModel):
    new_password: str


class ParentStatusPayload(BaseModel):
    is_active: bool


class ParentLinkPayload(BaseModel):
    relationship: str = "guardian"


def _raise_service_error(exc):
    if isinstance(exc, (parent_service.ParentNotFoundError, parent_service.StudentNotFoundError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, parent_service.ParentConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/me")
def parent_me(request: Request):
    session = require_session(request, "parent")
    try:
        return parent_service.get_parent(session["parent_id"])
    except parent_service.ParentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("")
def list_parents(request: Request):
    require_admin(request)
    return parent_service.list_parents()


@router.post("", status_code=201)
def create_parent(payload: ParentCreatePayload, request: Request):
    require_admin(request)
    try:
        return parent_service.create_parent(
            payload.login, payload.display_name, payload.phone, payload.password
        )
    except (parent_service.ParentValidationError, parent_service.ParentConflictError) as exc:
        _raise_service_error(exc)


@router.get("/{parent_id}")
def get_parent(parent_id: str, request: Request):
    require_admin(request)
    try:
        return parent_service.get_parent(parent_id)
    except parent_service.ParentNotFoundError as exc:
        _raise_service_error(exc)


@router.put("/{parent_id}")
def update_parent(parent_id: str, payload: ParentUpdatePayload, request: Request):
    require_admin(request)
    try:
        current = parent_service.require_parent(parent_id)
        updated = parent_service.update_profile(
            parent_id, payload.login, payload.display_name, payload.phone
        )
    except (parent_service.ParentValidationError, parent_service.ParentConflictError,
            parent_service.ParentNotFoundError) as exc:
        _raise_service_error(exc)
    if current["login"] != updated["login"]:
        delete_auth_sessions_for_login("parent", current["login"])
    return updated


@router.put("/{parent_id}/password")
def change_parent_password(parent_id: str, payload: ParentPasswordPayload, request: Request):
    require_admin(request)
    try:
        parent = parent_service.require_parent(parent_id)
        result = parent_service.change_password(parent_id, payload.new_password)
    except (parent_service.ParentValidationError, parent_service.ParentNotFoundError) as exc:
        _raise_service_error(exc)
    delete_auth_sessions_for_login("parent", parent["login"])
    return result


@router.put("/{parent_id}/status")
def set_parent_status(parent_id: str, payload: ParentStatusPayload, request: Request):
    require_admin(request)
    try:
        parent = parent_service.set_active(parent_id, payload.is_active)
    except parent_service.ParentNotFoundError as exc:
        _raise_service_error(exc)
    delete_auth_sessions_for_login("parent", parent["login"])
    return parent


@router.delete("/{parent_id}")
def delete_parent(parent_id: str, request: Request):
    require_admin(request)
    try:
        parent = parent_service.set_active(parent_id, False)
    except parent_service.ParentNotFoundError as exc:
        _raise_service_error(exc)
    delete_auth_sessions_for_login("parent", parent["login"])
    return {"status": "blocked", "id": parent_id}


@router.post("/{parent_id}/students/{student_id}")
def link_parent(parent_id: str, student_id: str, payload: ParentLinkPayload, request: Request):
    require_admin(request)
    try:
        return parent_service.link_parent_to_student(parent_id, student_id, payload.relationship)
    except (parent_service.ParentValidationError, parent_service.ParentConflictError,
            parent_service.ParentNotFoundError, parent_service.StudentNotFoundError) as exc:
        _raise_service_error(exc)


@router.delete("/{parent_id}/students/{student_id}")
def unlink_parent(parent_id: str, student_id: str, request: Request):
    require_admin(request)
    try:
        return parent_service.unlink_parent_from_student(parent_id, student_id)
    except (parent_service.ParentNotFoundError, parent_service.StudentNotFoundError) as exc:
        _raise_service_error(exc)
