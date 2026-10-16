import sqlite3

from fastapi.testclient import TestClient

from school_face_id.config.security import SESSION_COOKIE, create_session, delete_session
from school_face_id.data import database
from school_face_id.main import app
from school_face_id.repositories import auth, parents, students
from school_face_id.services import parents as parent_service


def make_student(name, class_name="10A"):
    return students.save_student(name, class_name, "", None)


def admin_client():
    token = create_session("admin", login="parent-admin", csrf_token="parent-csrf")
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    client.headers["X-CSRF-Token"] = "parent-csrf"
    return client, token


def test_parent_profile_uniqueness_and_phone_normalization():
    first = parent_service.create_parent(
        "kunduz01", "Кундуз Жолдошалиева", "+996 700-123 (456)", "kunduz-secret"
    )
    second = parent_service.create_parent(
        "kunduz02", "Кундуз Жолдошалиева", None, "second-secret"
    )

    assert first["login"] != first["display_name"]
    assert first["phone"] == "+996700123456"
    assert first["display_name"] == second["display_name"]
    try:
        parent_service.create_parent("kunduz01", "Other Name", None, "duplicate")
    except parent_service.ParentConflictError:
        pass
    else:
        raise AssertionError("duplicate login was accepted")


def test_many_to_many_and_existing_parent_password_is_unchanged():
    parent_a = parent_service.create_parent("family-a", "Family A", None, "original-secret")
    parent_b = parent_service.create_parent("family-b", "Family B", None, "family-b-secret")
    student_a = make_student("Family Student A")
    student_b = make_student("Family Student B")

    parent_service.link_parent_to_student(parent_a["id"], student_a["id"], "mother")
    parent_service.link_parent_to_student(parent_a["id"], student_b["id"], "guardian")
    parent_service.link_parent_to_student(parent_b["id"], student_a["id"], "father")
    resolved = parent_service.resolve_parent_for_student(
        "family-a", "Family A", None, "must-not-replace-password"
    )
    parent_service.ensure_parent_link(resolved["id"], student_b["id"], "guardian")

    assert len(parent_service.get_students_for_parent(parent_a["id"])) == 2
    linked_parents = parent_service.get_parents_for_student(student_a["id"])
    assert {item["id"] for item in linked_parents} == {parent_a["id"], parent_b["id"]}
    assert {item["relationship"] for item in linked_parents} == {"mother", "father"}
    assert auth.authenticate_parent("family-a", "original-secret") == parent_a["id"]
    assert auth.authenticate_parent("family-a", "must-not-replace-password") is None


def test_parent_attendance_is_scoped_to_all_linked_children():
    parent_a = parent_service.create_parent("report-a", "Report A", None, "report-secret")
    parent_b = parent_service.create_parent("report-b", "Report B", None, "report-secret")
    first = make_student("Report Child One", "11A")
    second = make_student("Report Child Two", "11B")
    other = make_student("Other Parent Child", "11C")
    parent_service.link_parent_to_student(parent_a["id"], first["id"], "mother")
    parent_service.link_parent_to_student(parent_a["id"], second["id"], "mother")
    parent_service.link_parent_to_student(parent_b["id"], other["id"], "father")

    rows = database.get_parent_daily_report_by_id(parent_a["id"])
    assert {row["student_name"] for row in rows} == {"Report Child One", "Report Child Two"}
    assert "Other Parent Child" not in {row["student_name"] for row in rows}


def test_parent_api_access_control_and_never_returns_password_hash():
    parent = parent_service.create_parent("api-parent", "API Parent", None, "api-secret-123")
    child = make_student("API Parent Child")
    parent_service.link_parent_to_student(parent["id"], child["id"], "other")

    parent_token = create_session(
        "parent", login=parent["login"], parent_id=parent["id"], csrf_token="parent-me-csrf"
    )
    parent_client = TestClient(app)
    parent_client.cookies.set(SESSION_COOKIE, parent_token)
    teacher_token = create_session("teacher", login="parent-test-teacher", classes=["10A"], csrf_token="t")
    teacher_client = TestClient(app)
    teacher_client.cookies.set(SESSION_COOKIE, teacher_token)
    teacher_client.headers["X-CSRF-Token"] = "t"
    try:
        own = parent_client.get("/api/v1/parents/me")
        assert own.status_code == 200
        assert own.json()["students"][0]["id"] == child["id"]
        assert "password_hash" not in str(own.json())
        assert parent_client.get(f"/api/v1/parents/{parent['id']}").status_code == 403
        assert teacher_client.get("/api/v1/parents").status_code == 403
        assert teacher_client.put(
            f"/api/v1/parents/{parent['id']}",
            json={"login": "x", "display_name": "x"},
        ).status_code == 403
    finally:
        delete_session(parent_token)
        delete_session(teacher_token)


def test_deletes_preserve_the_other_side_of_relation():
    parent = parent_service.create_parent("delete-parent", "Delete Parent", None, "delete-secret")
    first = make_student("Delete Child One")
    second = make_student("Delete Child Two")
    parent_service.link_parent_to_student(parent["id"], first["id"], "guardian")
    parent_service.link_parent_to_student(parent["id"], second["id"], "guardian")

    students.delete_student_by_id(first["id"])
    assert parents.get_parent_by_id(parent["id"]) is not None
    assert [row["id"] for row in parents.get_students_for_parent(parent["id"])] == [second["id"]]

    parent_service.delete_parent(parent["id"])
    assert students.get_student_by_id(second["id"]) is not None


def test_parent_admin_crud_response_has_no_hash():
    client, token = admin_client()
    try:
        created = client.post(
            "/api/v1/parents",
            json={"login": "crud-parent", "display_name": "CRUD Parent",
                  "phone": "+996 555 000 111", "password": "crud-secret-123"},
        )
        assert created.status_code == 201
        parent_id = created.json()["id"]
        assert "password_hash" not in created.text
        listed = client.get("/api/v1/parents")
        assert listed.status_code == 200
        assert "password_hash" not in listed.text
        assert client.delete(f"/api/v1/parents/{parent_id}").status_code == 200
    finally:
        delete_session(token)
