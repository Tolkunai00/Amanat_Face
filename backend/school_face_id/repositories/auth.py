from ..config.passwords import verify_password
from ..data.database import (
    authenticate_teacher, clear_auth_failures,
    delete_auth_session, delete_auth_sessions_for_login,
    delete_expired_auth_sessions, get_auth_rate_limit, get_auth_session,
    record_auth_failure, save_auth_session,
)
from .parents import get_parent_credentials_by_login
from .teachers import get_classes_for_teacher_id, get_teacher_credentials_by_login
from .teachers import mark_teacher_login


def authenticate_parent(login, password):
    row = get_parent_credentials_by_login((login or "").strip())
    if row is None or not verify_password(password or "", row[1]):
        return None
    return row[0]


def authenticate_teacher_identity(login, password):
    row = get_teacher_credentials_by_login(login)
    if row is None or not verify_password(password or "", row[2]):
        return None
    from datetime import datetime
    from zoneinfo import ZoneInfo
    mark_teacher_login(row[0], datetime.now(ZoneInfo("Asia/Bishkek")).isoformat(timespec="seconds"))
    return {
        "teacher_id": row[0],
        "login": row[1],
        "classes": get_classes_for_teacher_id(row[0]),
    }


def identity_is_active(role, login, claims):
    if role == "teacher":
        from .teachers import get_teacher_by_id
        teacher = get_teacher_by_id(claims.get("teacher_id", ""))
        return bool(teacher and teacher["is_active"])
    if role == "parent":
        from .parents import get_parent_by_id
        parent = get_parent_by_id(claims.get("parent_id", ""))
        return bool(parent and parent["is_active"])
    return role == "admin"

__all__ = [name for name in globals() if not name.startswith("_")]
