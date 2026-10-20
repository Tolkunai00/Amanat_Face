import numpy as np
import pytest

from school_face_id.repositories import students as student_repository
from school_face_id.services import students as student_service


def embedding():
    return np.ones(512, dtype=np.float32)


def test_student_repository_create_update_and_delete_preserves_id_and_relations():
    created_result = student_repository.save_student(
        "Repository Student",
        "6A",
        "face_database/repository-student.jpg",
        embedding(),
    )
    assert created_result
    student_id = created_result["id"]
    student_number = created_result["student_number"]
    created = student_repository.get_student_by_id(student_id)

    assert student_id
    assert created[0] == student_id
    assert created[1] == student_number
    assert student_repository.count_face_embeddings(student_id) == 1

    assert student_repository.update_student_by_id(
        student_id,
        "Repository Student Renamed",
        "6B",
    )
    updated = student_repository.get_student_by_id(student_id)
    assert updated[0] == student_id
    assert updated[1] == student_number
    assert updated[2:4] == ("Repository Student Renamed", "6B")
    assert student_repository.count_face_embeddings(student_id) == 1

    deleted = student_repository.delete_student_by_id(student_id)
    assert deleted[0] == student_id
    assert student_repository.get_student_by_id(student_id) is None
    assert student_repository.count_face_embeddings(student_id) == 0


def test_student_service_orchestrates_create_update_and_delete(monkeypatch):
    refreshes = []
    monkeypatch.setattr(student_service, "embedding_for", lambda image: embedding())
    monkeypatch.setattr(
        student_service,
        "save_photo",
        lambda image, name: f"face_database/{student_service.safe_filename(name)}.jpg",
    )
    monkeypatch.setattr(student_service, "refresh_known_faces", lambda: refreshes.append(True))

    created = student_service.create_student(
        "Service Student",
        "5A",
        "Service Parent",
        "service-parent-secret",
        object(),
    )
    assert created["status"] == "created"
    assert created["student_number"].startswith("AMANAT-")
    student_id = created["id"]
    assert student_id

    assert student_service.update_student(
        student_id,
        "Service Student Renamed",
        "5B",
        "Service Parent",
        "",
    ) == {"status": "updated"}
    assert student_repository.get_student_id_by_name("Service Student Renamed") == student_id

    assert student_service.delete_student(student_id) == {"status": "deleted"}
    assert refreshes == [True, True, True]


def test_multiview_enrollment_validates_before_creating_student(monkeypatch):
    def validate(image):
        if image == "bad":
            raise student_service.FaceQualityError("Face was not found")
        return embedding()

    monkeypatch.setattr(student_service, "embedding_for", validate)
    monkeypatch.setattr(student_service, "save_photo", lambda *args: pytest.fail("Saved invalid enrollment"))
    with pytest.raises(student_service.FaceQualityError, match="right"):
        student_service.create_student(
            "Invalid multiview", "5A", "", "", "front",
            enrollment_images=[("left", "left"), ("right", "bad")],
        )
    assert student_repository.get_student_id_by_name("Invalid multiview") is None


def test_multiview_enrollment_saves_all_samples_and_refreshes(monkeypatch):
    monkeypatch.setattr(student_service, "embedding_for", lambda image: embedding())
    monkeypatch.setattr(student_service, "save_photo", lambda *args: "")
    refreshes = []
    monkeypatch.setattr(student_service, "refresh_known_faces", lambda: refreshes.append(True))
    created = student_service.create_student(
        "Multiview Student", "5A", "", "", object(),
        enrollment_images=[("left", object()), ("right", object()), ("distance", object())],
    )
    assert created["face_samples"] == 4
    assert student_repository.count_face_embeddings(created["id"]) == 4
    assert refreshes == [True]


def test_student_service_rejects_empty_names_and_allows_duplicates():
    with pytest.raises(student_service.StudentConflictError):
        student_service.validate_new_student("   ")

    first = student_repository.save_student(
        "Duplicate Service Student", "4A", "", None
    )
    second = student_repository.save_student(
        "Duplicate Service Student", "4A", "", None
    )
    assert first and second
    assert first["id"] != second["id"]
    assert first["student_number"] != second["student_number"]
    assert student_service.validate_new_student("Duplicate Service Student") == "Duplicate Service Student"
