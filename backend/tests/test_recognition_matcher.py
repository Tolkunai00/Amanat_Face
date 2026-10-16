import numpy as np

from school_face_id.recognition.matcher import match_identity
from school_face_id.recognition.models import RecognitionResult


def normalized(values):
    values = np.asarray(values, dtype=np.float32)
    return values / np.linalg.norm(values)


def test_multiple_angles_of_same_student_do_not_fail_margin():
    query = normalized([1.0, 0.0])
    matrix = np.asarray([
        normalized([1.0, 0.0]),
        normalized([0.99, 0.01]),
        normalized([0.75, 0.66]),
    ])
    match = match_identity(
        query,
        ["student-a", "student-a", "student-b"],
        ["A", "A", "B"],
        ["parent-a", "parent-a", "parent-b"],
        ["7A", "7A", "8B"],
        matrix,
        threshold=0.5,
        margin=0.1,
    )
    assert match["name"] == "A"
    assert match["class_name"] == "7A"


def test_close_competing_identity_is_rejected():
    query = normalized([1.0, 0.0])
    matrix = np.asarray([normalized([1.0, 0.0]), normalized([0.999, 0.02])])
    assert match_identity(
        query, ["student-a", "student-b"], ["A", "B"], ["", ""],
        ["7A", "7A"], matrix, 0.5, 0.05
    ) is None


def test_recognition_result_has_explicit_fields():
    result = RecognitionResult("A", "7A", (1, 2, 3, 4), False, True)
    assert result.class_name == "7A"
    assert result.box == (1, 2, 3, 4)
    assert result.is_live is True
