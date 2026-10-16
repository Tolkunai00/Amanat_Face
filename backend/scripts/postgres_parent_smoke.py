"""Destructive smoke test guarded to run only against a disposable smoke DB."""

from urllib.parse import urlparse

from school_face_id.config.settings import DATABASE_URL
from school_face_id.repositories import auth, parents, students
from school_face_id.services import parents as parent_service


database_name = urlparse(DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)).path.lstrip("/")
if not database_name.startswith("amanat_parent_smoke_"):
    raise RuntimeError("Refusing to run outside an amanat_parent_smoke_* database")

parent_a = parent_service.create_parent("smoke-a", "Smoke Parent A", None, "smoke-a-secret")
parent_b = parent_service.create_parent("smoke-b", "Smoke Parent B", None, "smoke-b-secret")
student_a = students.save_student("Smoke Student A", "Smoke Class", "", None)
student_b = students.save_student("Smoke Student B", "Smoke Class", "", None)

try:
    parent_service.link_parent_to_student(parent_a["id"], student_a["id"], "mother")
    parent_service.link_parent_to_student(parent_a["id"], student_b["id"], "mother")
    parent_service.link_parent_to_student(parent_b["id"], student_a["id"], "father")

    assert len(parents.get_students_for_parent(parent_a["id"])) == 2
    assert len(parents.get_parents_for_student(student_a["id"])) == 2
    assert auth.authenticate_parent("smoke-a", "smoke-a-secret") == parent_a["id"]
    print("PostgreSQL parent many-to-many smoke test: OK")
finally:
    students.delete_student_by_id(student_a["id"])
    students.delete_student_by_id(student_b["id"])
    parents.delete_parent(parent_a["id"])
    parents.delete_parent(parent_b["id"])
