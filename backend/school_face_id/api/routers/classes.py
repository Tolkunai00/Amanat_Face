from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..dependencies import require_admin, require_staff
from ...repositories import classes as class_repository


router = APIRouter(prefix="/classes", tags=["classes"])


class ClassPayload(BaseModel):
    name: str


class ClassStatusPayload(BaseModel):
    is_active: bool


@router.get("")
def list_classes(request: Request, include_inactive: bool = False):
    session = require_staff(request)
    include_inactive = include_inactive and session["role"] == "admin"
    classes = class_repository.list_classes(include_inactive=include_inactive)
    if session["role"] == "teacher":
        allowed = set(session.get("classes", []))
        classes = [item for item in classes if item["name"] in allowed]
    return classes


@router.post("", status_code=201)
def create_class(payload: ClassPayload, request: Request):
    require_admin(request)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Class name is required")
    created = class_repository.create_class(name)
    if created is None:
        raise HTTPException(status_code=409, detail="Class name already exists")
    return created


@router.put("/{class_id}")
def rename_class(class_id: str, payload: ClassPayload, request: Request):
    require_admin(request)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Class name is required")
    updated = class_repository.rename_class(class_id, name)
    if updated is False:
        raise HTTPException(status_code=409, detail="Class name already exists")
    if updated is None:
        raise HTTPException(status_code=404, detail="Class not found")
    return updated


@router.put("/{class_id}/status")
def set_class_status(class_id: str, payload: ClassStatusPayload, request: Request):
    require_admin(request)
    if not class_repository.set_class_active(class_id, payload.is_active):
        raise HTTPException(status_code=404, detail="Class not found")
    return class_repository.get_class(class_id)


@router.delete("/{class_id}")
def block_class(class_id: str, request: Request):
    require_admin(request)
    if not class_repository.set_class_active(class_id, False):
        raise HTTPException(status_code=404, detail="Class not found")
    return {"status": "blocked", "id": class_id}
