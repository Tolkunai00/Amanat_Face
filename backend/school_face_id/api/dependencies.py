"""Shared authorization and class-scope dependencies."""

from fastapi import HTTPException, Request

from ..config.security import require_session


def require_admin(request: Request) -> dict:
    return require_session(request, "admin")


def require_staff(request: Request) -> dict:
    return require_session(request, "admin", "teacher")


def require_any_user(request: Request) -> dict:
    return require_session(request, "admin", "teacher", "parent")


def ensure_class_access(session: dict, class_name: str) -> None:
    if session["role"] == "teacher" and class_name not in session.get("classes", []):
        raise HTTPException(status_code=403, detail="Class is not assigned to this teacher")
