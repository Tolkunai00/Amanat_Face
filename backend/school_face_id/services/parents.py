"""Parent-domain validation and orchestration."""

from ..repositories import parents as parent_repository
from ..repositories import students as student_repository
from ..repositories import teachers as teacher_repository
from ..config.passwords import validate_password


RELATIONSHIPS = {"mother", "father", "guardian", "other"}


class ParentValidationError(ValueError):
    pass


class ParentConflictError(ValueError):
    pass


class ParentNotFoundError(LookupError):
    pass


class StudentNotFoundError(LookupError):
    pass


def normalize_phone(phone):
    value = (phone or "").strip()
    for character in " -()":
        value = value.replace(character, "")
    return value or None


def _profile(login, display_name, phone):
    normalized_login = (login or "").strip()
    normalized_name = (display_name or "").strip()
    if not normalized_login:
        raise ParentValidationError("Parent login is required")
    if not normalized_name:
        raise ParentValidationError("Parent display name is required")
    return normalized_login, normalized_name, normalize_phone(phone)


def _relationship(value):
    normalized = (value or "guardian").strip().lower()
    if normalized not in RELATIONSHIPS:
        raise ParentValidationError("Invalid parent relationship")
    return normalized


def require_parent(parent_id):
    parent = parent_repository.get_parent_by_id(parent_id)
    if parent is None:
        raise ParentNotFoundError("Parent not found")
    return parent


def create_parent(login, display_name, phone, password):
    login, display_name, phone = _profile(login, display_name, phone)
    if (parent_repository.get_parent_by_login(login) is not None
            or teacher_repository.get_teacher_by_login(login) is not None):
        raise ParentConflictError("Login is already used")
    try:
        validate_password(password)
    except ValueError as exc:
        raise ParentValidationError(str(exc)) from exc
    parent = parent_repository.create_parent(login, display_name, phone, password)
    if parent is None:
        raise ParentConflictError("Parent login already exists")
    return parent


def get_parent(parent_id, include_students=True):
    parent = require_parent(parent_id)
    if include_students:
        parent = {**parent, "students": parent_repository.get_students_for_parent(parent_id)}
    return parent


def list_parents():
    return [
        {**parent, "students": parent_repository.get_students_for_parent(parent["id"])}
        for parent in parent_repository.list_parents()
    ]


def update_profile(parent_id, login, display_name, phone):
    current = require_parent(parent_id)
    login, display_name, phone = _profile(login, display_name, phone)
    conflict = parent_repository.get_parent_by_login(login)
    if conflict is not None and conflict["id"] != current["id"]:
        raise ParentConflictError("Login is already used")
    if teacher_repository.get_teacher_by_login(login) is not None:
        raise ParentConflictError("Login is already used")
    updated = parent_repository.update_parent(parent_id, login, display_name, phone)
    if updated is False:
        raise ParentConflictError("Parent login already exists")
    return updated


def change_password(parent_id, new_password):
    require_parent(parent_id)
    try:
        validate_password(new_password)
    except ValueError as exc:
        raise ParentValidationError(str(exc)) from exc
    parent_repository.change_parent_password(parent_id, new_password)
    return {"status": "password_changed"}


def set_active(parent_id, is_active):
    parent = require_parent(parent_id)
    parent_repository.set_parent_active(parent_id, is_active)
    return {**parent, "is_active": bool(is_active)}


def delete_parent(parent_id):
    parent = require_parent(parent_id)
    parent_repository.delete_parent(parent_id)
    return parent


def link_parent_to_student(parent_id, student_id, relationship="guardian"):
    require_parent(parent_id)
    if student_repository.get_student_by_id(student_id) is None:
        raise StudentNotFoundError("Student not found")
    relationship = _relationship(relationship)
    if not parent_repository.link_parent_to_student(parent_id, student_id, relationship):
        raise ParentConflictError("Parent is already linked to this student")
    return {"status": "linked", "relationship": relationship}


def ensure_parent_link(parent_id, student_id, relationship="guardian"):
    relationship = _relationship(relationship)
    existing = parent_repository.get_parents_for_student(student_id)
    if any(parent["id"] == parent_id for parent in existing):
        return {"status": "linked", "relationship": relationship}
    return link_parent_to_student(parent_id, student_id, relationship)


def unlink_parent_from_student(parent_id, student_id):
    require_parent(parent_id)
    if student_repository.get_student_by_id(student_id) is None:
        raise StudentNotFoundError("Student not found")
    if not parent_repository.unlink_parent_from_student(parent_id, student_id):
        raise ParentNotFoundError("Parent/student link not found")
    return {"status": "unlinked"}


def get_students_for_parent(parent_id):
    require_parent(parent_id)
    return parent_repository.get_students_for_parent(parent_id)


def get_parents_for_student(student_id):
    if student_repository.get_student_by_id(student_id) is None:
        raise StudentNotFoundError("Student not found")
    return parent_repository.get_parents_for_student(student_id)


def resolve_parent_for_student(login, display_name, phone, password):
    """Return an existing account unchanged, or explicitly create a new one."""
    login, display_name, phone = _profile(login, display_name, phone)
    existing = parent_repository.get_parent_by_login(login)
    if existing is None:
        return create_parent(login, display_name, phone, password)
    if display_name != existing["display_name"]:
        raise ParentConflictError("Parent display name conflicts with the existing account")
    if phone is not None and phone != existing["phone"]:
        raise ParentConflictError("Parent phone conflicts with the existing account")
    # Deliberately ignore password when linking an existing account. Password
    # changes are only allowed through change_password().
    return existing
