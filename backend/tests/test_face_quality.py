from types import SimpleNamespace

import numpy as np
import pytest

from school_face_id.db.connection import get_connection
from school_face_id.recognition import camera_service
from school_face_id.repositories import students as student_repository
from school_face_id.services import students as student_service


def sharp_image():
    pattern = np.indices((400, 400)).sum(axis=0) % 2
    gray = (pattern * 150 + 50).astype(np.uint8)
    return np.repeat(gray[:, :, None], 3, axis=2)


def face():
    return SimpleNamespace(
        bbox=np.asarray([80, 70, 320, 330], dtype=np.float32),
        embedding=np.ones(512, dtype=np.float32),
    )


def test_enrollment_rejects_multiple_faces(monkeypatch):
    monkeypatch.setattr(
        camera_service, "analyze_faces", lambda image, profile_name="fast": [face(), face()]
    )
    with pytest.raises(student_service.FaceQualityError, match="exactly one"):
        student_service.embedding_for(sharp_image())


def test_enrollment_accepts_one_clear_well_lit_face(monkeypatch):
    monkeypatch.setattr(
        camera_service, "analyze_faces", lambda image, profile_name="fast": [face()]
    )
    embedding = student_service.embedding_for(sharp_image())
    assert embedding.shape == (512,)
    assert np.linalg.norm(embedding) == pytest.approx(1.0)


def test_invalid_embeddings_are_removed():
    student = student_repository.save_student("Invalid Embedding Student", "Quality", "", None)
    conn = get_connection()
    conn.execute(
        "INSERT INTO face_embeddings (student_id, angle_label, embedding) VALUES (?, ?, ?)",
        (student["id"], "broken", b""),
    )
    conn.commit()
    conn.close()

    assert student_repository.delete_invalid_face_embeddings() == 1
    assert student_repository.count_face_embeddings(student["id"]) == 0
