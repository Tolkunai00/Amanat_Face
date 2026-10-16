"""Create explicit local test accounts without embedded credentials."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from school_face_id.data.database import (  # noqa: E402
    authenticate_teacher,
    init_db,
    save_teacher,
)
from school_face_id.repositories.auth import authenticate_parent  # noqa: E402
from school_face_id.repositories.students import save_student  # noqa: E402
from school_face_id.services import parents as parent_service  # noqa: E402


def strong_secret(value: str) -> str:
    if len(value) < 12:
        raise argparse.ArgumentTypeError("password must contain at least 12 characters")
    return value


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--teacher-login", default="teacher_test")
    parser.add_argument("--teacher-password", required=True, type=strong_secret)
    parser.add_argument("--parent-login", default="parent_test")
    parser.add_argument("--parent-password", required=True, type=strong_secret)
    parser.add_argument("--class-name", default="4_A")
    parser.add_argument("--student-name", default="Тестовый ученик")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_db()
    if not save_teacher(args.teacher_login, args.teacher_password, [args.class_name]):
        raise RuntimeError("Не удалось создать тестового учителя")
    student = save_student(
        args.student_name, args.class_name, "", np.zeros(512, dtype=np.float32)
    )
    parent = parent_service.resolve_parent_for_student(
        args.parent_login, args.parent_login, None, args.parent_password
    )
    parent_service.ensure_parent_link(parent["id"], student["id"], "guardian")
    if authenticate_teacher(args.teacher_login, args.teacher_password) is None:
        raise RuntimeError("Проверка учителя не прошла")
    if authenticate_parent(args.parent_login, args.parent_password) is None:
        raise RuntimeError("Проверка родителя не прошла")
    print("Тестовые пользователи созданы; пароли не выводятся.")


if __name__ == "__main__":
    main()
