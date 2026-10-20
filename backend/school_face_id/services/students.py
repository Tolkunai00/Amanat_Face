"""Student application logic shared by HTTP and other entry points."""

from pathlib import Path
import secrets

import numpy as np

from ..config.settings import (
    FACE_DIR, FACE_MAX_ENROLLMENT_SAMPLES, FACE_MIN_ENROLLMENT_SAMPLES,
    PROJECT_ROOT,
)
from ..repositories import students as student_repository
from . import parents as parent_service


class StudentConflictError(ValueError):
    pass


class StudentNotFoundError(LookupError):
    pass


class FaceNotFoundError(ValueError):
    pass


class FaceQualityError(FaceNotFoundError):
    pass


class PhotoSaveError(RuntimeError):
    pass


def serialize_student(row) -> dict:
    student_id, student_number, name, class_name, _ = row
    parents = parent_service.get_parents_for_student(student_id)
    face_count = student_repository.count_face_embeddings(student_id)
    return {
        "id": student_id,
        "student_number": student_number,
        "name": name,
        "class_name": class_name,
        "parents": parents,
        "parent_name": parents[0]["display_name"] if parents else "",
        "photo_url": f"/api/v1/students/{student_id}/photo",
        "face_samples": face_count,
        "recognition_ready": FACE_MIN_ENROLLMENT_SAMPLES <= face_count <= FACE_MAX_ENROLLMENT_SAMPLES,
    }


def list_students() -> list[dict]:
    return [serialize_student(row) for row in student_repository.get_all_students_with_ids()]


def embedding_for(image):
    from ..recognition.camera_service import analyze_faces
    from ..recognition.embedding import InvalidEmbeddingError, embedding_from_face

    if image is None or image.size == 0:
        raise FaceQualityError("Image is empty or damaged")
    faces = analyze_faces(image, "accurate")
    if not faces:
        raise FaceNotFoundError("Face was not found")
    if len(faces) != 1:
        raise FaceQualityError("Image must contain exactly one face")
    face = faces[0]
    try:
        embedding = embedding_from_face(face)
    except InvalidEmbeddingError as exc:
        raise FaceQualityError("Face embedding is empty or damaged") from exc
    return embedding


def face_quality(image):
    import cv2
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    embedding_for(image)
    return {
        "valid": True,
        "blur_score": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1),
        "brightness": round(float(np.mean(gray)), 1),
    }


def safe_filename(name: str) -> str:
    cleaned = "".join(ch for ch in name.strip().replace(" ", "_") if ch.isalnum() or ch in "_-")
    return cleaned or "student"


def save_photo(image, name: str) -> str:
    import cv2

    path = Path(FACE_DIR) / f"{safe_filename(name)}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise PhotoSaveError("Cannot save student photo")
    return path.relative_to(PROJECT_ROOT).as_posix()


def add_face_samples(student_id, images):
    import cv2

    require_student(student_id)
    existing = student_repository.count_face_embeddings(student_id)
    if existing + len(images) > FACE_MAX_ENROLLMENT_SAMPLES:
        raise FaceQualityError(
            f"A student can have at most {FACE_MAX_ENROLLMENT_SAMPLES} face samples"
        )
    embeddings = [(image, embedding_for(image)) for image in images]
    folder = Path(FACE_DIR) / "enrollments" / student_id
    folder.mkdir(parents=True, exist_ok=True)
    for image, embedding in embeddings:
        sample_id = secrets.token_hex(8)
        path = folder / f"{sample_id}.jpg"
        if not cv2.imwrite(str(path), image):
            raise PhotoSaveError("Cannot save enrollment photo")
        student_repository.add_face_embedding(student_id, embedding, f"sample:{sample_id}")
    refresh_known_faces()
    count = student_repository.count_face_embeddings(student_id)
    return {
        "face_samples": count,
        "recognition_ready": FACE_MIN_ENROLLMENT_SAMPLES <= count <= FACE_MAX_ENROLLMENT_SAMPLES,
        "required": FACE_MIN_ENROLLMENT_SAMPLES,
    }


def refresh_known_faces() -> None:
    from ..recognition.camera_service import load_known_faces

    load_known_faces(force=True)


def validate_new_student(name: str) -> str:
    student_name = name.strip()
    if not student_name:
        raise StudentConflictError("Student name is empty")
    return student_name


def require_student(student_id: str):
    student = student_repository.get_student_by_id(student_id)
    if student is None:
        raise StudentNotFoundError("Student not found")
    return student


def student_photo_path(student_id: str) -> Path:
    student = student_repository.get_student_by_id(student_id)
    if student is None or not student[4]:
        raise StudentNotFoundError("Photo not found")
    root = PROJECT_ROOT.resolve()
    path = (PROJECT_ROOT / student[4]).resolve()
    if root not in path.parents or not path.is_file():
        raise StudentNotFoundError("Photo not found")
    return path


def create_student(
    name, class_name, parent_name, parent_password, image,
    parent_login="", parent_phone="", parent_relationship="guardian",
    enrollment_images=(),
) -> dict:
    student_name = validate_new_student(name)
    embedding = embedding_for(image)
    if 1 + len(enrollment_images) > FACE_MAX_ENROLLMENT_SAMPLES:
        raise FaceQualityError("Too many enrollment photos")
    additional_embeddings = []
    for angle, sample in enrollment_images:
        try:
            additional_embeddings.append((angle, embedding_for(sample)))
        except FaceNotFoundError as exc:
            raise FaceQualityError(f"{angle}: {exc}") from exc
    photo_path = save_photo(image, student_name)
    created = student_repository.save_student(
        student_name,
        class_name.strip(),
        photo_path,
        embedding,
        additional_embeddings=additional_embeddings,
    )
    if not created:
        raise StudentConflictError("Student could not be created")
    login = (parent_login or "").strip()
    display_name = (parent_name or "").strip()
    if display_name or login:
        # TODO: remove legacy parent_name-as-login fallback after frontend migration
        login = login or display_name
        try:
            parent = parent_service.resolve_parent_for_student(
                login, display_name or login, parent_phone, parent_password
            )
            parent_service.ensure_parent_link(
                parent["id"], created["id"], parent_relationship
            )
        except (parent_service.ParentValidationError, parent_service.ParentConflictError) as exc:
            student_repository.delete_student_by_id(created["id"])
            raise StudentConflictError(str(exc)) from exc
    refresh_known_faces()
    return {"status": "created", **created, "face_samples": 1 + len(additional_embeddings)}


def update_student(
    student_id, name, class_name, parent_name, parent_password, image=None,
    parent_login="", parent_phone="", parent_relationship="guardian",
) -> dict:
    old_student = require_student(student_id)
    new_name = name.strip()
    photo_path = None
    embedding = None
    if image is not None:
        embedding = embedding_for(image)
        photo_path = save_photo(image, new_name)

    if not student_repository.update_student_by_id(
        student_id,
        new_name,
        class_name.strip(),
        photo_path,
        embedding,
    ):
        raise StudentConflictError("Student could not be updated")

    login = (parent_login or "").strip()
    display_name = (parent_name or "").strip()
    if display_name or login:
        # TODO: remove legacy parent_name-as-login fallback after frontend migration
        login = login or display_name
        try:
            parent = parent_service.resolve_parent_for_student(
                login, display_name or login, parent_phone, parent_password
            )
            parent_service.ensure_parent_link(parent["id"], student_id, parent_relationship)
        except (parent_service.ParentValidationError, parent_service.ParentConflictError) as exc:
            raise StudentConflictError(str(exc)) from exc

    old_photo = (PROJECT_ROOT / old_student[4]).resolve() if old_student[4] else None
    if photo_path and old_photo and old_student[4] != photo_path and old_photo.is_file():
        old_photo.unlink()
    refresh_known_faces()
    return {"status": "updated"}


def delete_student(student_id: str) -> dict:
    student = require_student(student_id)
    student_repository.delete_student_by_id(student_id)
    if student[4]:
        path = (PROJECT_ROOT / student[4]).resolve()
        if PROJECT_ROOT.resolve() in path.parents and path.is_file():
            path.unlink()
    refresh_known_faces()
    return {"status": "deleted"}
