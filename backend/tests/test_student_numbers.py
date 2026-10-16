import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from fastapi.testclient import TestClient

from school_face_id.api.routers import students as student_router
from school_face_id.config.security import SESSION_COOKIE, create_session, delete_session
from school_face_id.main import app
from school_face_id.repositories import students
from school_face_id.services import students as student_service
from school_face_id.services.students import serialize_student


def create_student(name):
    return students.save_student(name, "3A", "", None)


def numeric_part(student_number):
    return int(student_number.removeprefix("AMANAT-"))


def test_same_name_students_have_distinct_ids_and_numbers():
    first = create_student("Same Name Student")
    second = create_student("Same Name Student")

    assert first and second
    assert first["id"] != second["id"]
    assert first["student_number"] != second["student_number"]

    matches = students.get_students_by_name("Same Name Student")
    assert {row[0] for row in matches} == {first["id"], second["id"]}
    assert students.get_student_id_by_name("Same Name Student") is None


def test_student_number_format_lookup_and_serialization():
    created = create_student("Numbered Student")
    assert re.fullmatch(r"AMANAT-\d{6,}", created["student_number"])

    student = students.get_student_by_number(created["student_number"])
    assert student[0] == created["id"]
    assert student[1] == created["student_number"]

    payload = serialize_student(student[:5])
    assert payload["id"] == created["id"]
    assert payload["student_number"] == created["student_number"]


def test_deleted_student_number_is_not_reused():
    deleted = create_student("Student Number To Delete")
    students.delete_student_by_id(deleted["id"])
    replacement = create_student("Student Number Replacement")

    assert numeric_part(replacement["student_number"]) > numeric_part(deleted["student_number"])


def test_concurrent_sqlite_creation_allocates_unique_numbers():
    names = [f"Concurrent Number Student {index}" for index in range(6)]
    with ThreadPoolExecutor(max_workers=6) as executor:
        created = list(executor.map(create_student, names))

    ids = {result["id"] for result in created}
    numbers = {result["student_number"] for result in created}
    assert len(ids) == len(names)
    assert len(numbers) == len(names)


def test_student_api_create_list_rename_and_delete_with_duplicate_names(monkeypatch):
    async def fake_decode_image(_upload):
        return object()

    monkeypatch.setattr(student_router, "decode_image", fake_decode_image)
    monkeypatch.setattr(
        student_service, "embedding_for", lambda _image: np.ones(512, dtype=np.float32)
    )
    monkeypatch.setattr(
        student_service,
        "save_photo",
        lambda _image, name: f"face_database/{student_service.safe_filename(name)}.jpg",
    )
    monkeypatch.setattr(student_service, "refresh_known_faces", lambda: None)

    token = create_session("admin", login="student-number-admin", csrf_token="student-number-csrf")
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    client.headers["X-CSRF-Token"] = "student-number-csrf"
    form = {
        "name": "API Same Name",
        "class_name": "2A",
        "parent_name": "API Parent",
        "parent_password": "api-parent-secret",
    }
    try:
        first = client.post(
            "/api/v1/students", data=form, files={"photo": ("face.jpg", b"face", "image/jpeg")}
        )
        second = client.post(
            "/api/v1/students", data=form, files={"photo": ("face.jpg", b"face", "image/jpeg")}
        )
        assert first.status_code == 201
        assert second.status_code == 201
        first_data = first.json()
        second_data = second.json()
        assert first_data["id"] != second_data["id"]
        assert first_data["student_number"] != second_data["student_number"]

        listed = client.get("/api/v1/students")
        api_students = [row for row in listed.json() if row["name"] == "API Same Name"]
        assert {row["id"] for row in api_students} == {first_data["id"], second_data["id"]}
        assert all(row["student_number"].startswith("AMANAT-") for row in api_students)

        renamed = client.put(
            f"/api/v1/students/{first_data['id']}",
            data={**form, "name": "API Renamed Student", "parent_password": ""},
        )
        assert renamed.status_code == 200
        stored = students.get_student_by_id(first_data["id"])
        assert stored[1] == first_data["student_number"]
        assert stored[2] == "API Renamed Student"

        assert client.delete(f"/api/v1/students/{first_data['id']}").status_code == 200
        assert client.delete(f"/api/v1/students/{second_data['id']}").status_code == 200
    finally:
        delete_session(token)
