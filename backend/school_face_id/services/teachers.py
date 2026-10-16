"""Teacher account validation and lifecycle orchestration."""

from ..config.passwords import validate_password
from ..repositories import classes as class_repository
from ..repositories import parents as parent_repository
from ..repositories import teachers as teacher_repository


class TeacherValidationError(ValueError):
    pass


class TeacherConflictError(ValueError):
    pass


class TeacherNotFoundError(LookupError):
    pass


def _login(value):
    login = (value or "").strip()
    if not login:
        raise TeacherValidationError("Teacher login is required")
    if login.casefold() == "admin":
        raise TeacherConflictError("Login is reserved")
    return login


def _password(value):
    try:
        return validate_password(value)
    except ValueError as exc:
        raise TeacherValidationError(str(exc)) from exc


def _class_ids(values):
    class_ids = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if not class_ids:
        raise TeacherValidationError("At least one class is required")
    classes = class_repository.get_classes_by_ids(class_ids)
    if len(classes) != len(class_ids) or any(not item["is_active"] for item in classes):
        raise TeacherValidationError("Every class must exist and be active")
    return class_ids


def require_teacher(teacher_id):
    teacher = teacher_repository.get_teacher_by_id(teacher_id)
    if teacher is None:
        raise TeacherNotFoundError("Teacher not found")
    return teacher


def create_teacher(login, password, class_ids, full_name="", phone=None):
    login = _login(login)
    password = _password(password)
    class_ids = _class_ids(class_ids)
    if teacher_repository.login_exists(login) or parent_repository.get_parent_by_login(login):
        raise TeacherConflictError("Login is already used")
    teacher = teacher_repository.create_teacher(
        login, password, class_ids, (full_name or "").strip(), (phone or "").strip() or None
    )
    if teacher is None:
        raise TeacherConflictError("Login is already used")
    return {**teacher, "class_ids": class_ids}


def update_teacher(teacher_id, login, class_ids, full_name=None, phone=None):
    current = require_teacher(teacher_id)
    login = _login(login)
    class_ids = _class_ids(class_ids)
    if teacher_repository.login_exists(login, exclude_id=teacher_id):
        raise TeacherConflictError("Login is already used")
    parent = parent_repository.get_parent_by_login(login)
    if parent is not None:
        raise TeacherConflictError("Login is already used")
    teacher = teacher_repository.update_teacher(
        teacher_id, login, class_ids,
        None if full_name is None else full_name.strip(), (phone or "").strip() or None,
    )
    if teacher is False:
        raise TeacherConflictError("Login is already used")
    if teacher is None:
        raise TeacherNotFoundError("Teacher not found")
    return {**teacher, "class_ids": class_ids, "previous_login": current["login"]}


def change_password(teacher_id, password):
    teacher = require_teacher(teacher_id)
    _password(password)
    teacher_repository.change_teacher_password(teacher_id, password)
    return teacher


def set_active(teacher_id, is_active):
    teacher = require_teacher(teacher_id)
    teacher_repository.set_teacher_active(teacher_id, is_active)
    return {**teacher, "is_active": bool(is_active)}
