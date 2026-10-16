import numpy as np

from school_face_id.recognition.liveness import TemporalLiveness


def tracker():
    return TemporalLiveness(
        required_samples=3, window_seconds=2.0, min_face_ratio=0.01,
        min_texture_score=5.0, max_position_motion=0.22, min_pose_change=0.02,
    )


def observe(gate, track, now, pose, position=(0.5, 0.5, 0.2, 0.2), texture=20.0):
    return gate.observe(track, now, 0.04, texture, position, pose)


def test_single_sharp_frame_is_never_live():
    assert observe(tracker(), "student-a", 1.0, (0.0, 0.0, 0.0)) is False


def test_head_pose_change_after_multiple_samples_is_live():
    gate = tracker()
    assert not observe(gate, "student-a", 1.0, (0.0, 0.0, 0.0))
    assert not observe(gate, "student-a", 1.3, (0.005, 0.0, 0.0))
    assert observe(gate, "student-a", 1.6, (0.04, 0.0, 0.0))


def test_tracks_do_not_mix_two_people():
    gate = tracker()
    observe(gate, "student-a", 1.0, (0.0, 0.0, 0.0))
    observe(gate, "student-b", 1.1, (0.5, 0.0, 0.0))
    observe(gate, "student-a", 1.2, (0.04, 0.0, 0.0))
    assert not observe(gate, "student-b", 1.3, (0.54, 0.0, 0.0))


def test_low_texture_or_missing_landmarks_resets_track():
    gate = tracker()
    observe(gate, "student-a", 1.0, (0.0, 0.0, 0.0))
    assert not observe(gate, "student-a", 1.2, (0.03, 0.0, 0.0), texture=1.0)
    assert not gate.observe("student-a", 1.4, 0.04, 20.0, np.ones(4), None)


def test_liveness_state_is_removed_with_track_id():
    gate = tracker()
    observe(gate, "track-000001", 1.0, (0.0, 0.0, 0.0))
    assert "track-000001" in gate.tracks
    gate.remove("track-000001")
    assert "track-000001" not in gate.tracks
