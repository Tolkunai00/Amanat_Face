from school_face_id.recognition.tracker import FaceTracker


def test_small_motion_keeps_track_id():
    tracker = FaceTracker("camera-a")
    first = tracker.update([(10, 10, 110, 110)], 1.0).tracks[0]
    second = tracker.update([(18, 12, 118, 112)], 1.1).tracks[0]
    assert first.track_id == second.track_id


def test_track_expires_after_ttl():
    tracker = FaceTracker("camera-a", max_missed_frames=99, ttl_seconds=1.0)
    track_id = tracker.update([(10, 10, 110, 110)], 1.0).tracks[0].track_id
    update = tracker.update([], 2.1)
    assert update.expired_track_ids == (track_id,)
    assert not tracker.tracks


def test_detection_after_ttl_gets_new_track_id():
    tracker = FaceTracker("camera-a", ttl_seconds=1.0)
    first = tracker.update([(10, 10, 110, 110)], 1.0).tracks[0]
    update = tracker.update([(12, 12, 112, 112)], 2.1)
    assert first.track_id in update.expired_track_ids
    assert update.tracks[0].track_id != first.track_id


def test_two_nearby_faces_keep_identity_when_order_changes():
    tracker = FaceTracker("camera-a")
    first = tracker.update([(10, 10, 90, 100), (120, 10, 200, 100)], 1.0).tracks
    second = tracker.update([(125, 12, 205, 102), (15, 12, 95, 102)], 1.1).tracks
    assert second[0].track_id == first[1].track_id
    assert second[1].track_id == first[0].track_id


def test_recognition_identity_does_not_define_track_id():
    tracker = FaceTracker("camera-a")
    track = tracker.update([(10, 10, 110, 110)], 1.0).tracks[0]
    track.recognized_student_id = "student-1"
    same = tracker.update([(12, 12, 112, 112)], 1.1).tracks[0]
    assert same.track_id.startswith("track-")
    assert same.track_id != same.recognized_student_id


def test_track_state_is_isolated_by_camera_instance():
    camera_a = FaceTracker("camera-a")
    camera_b = FaceTracker("camera-b")
    track_a = camera_a.update([(10, 10, 110, 110)], 1.0).tracks[0]
    camera_b.update([(300, 300, 400, 400)], 1.0)
    track_a.recognized_student_id = "student-a"
    assert all(track.recognized_student_id is None for track in camera_b.tracks.values())


def test_tracker_expiration_removes_cached_pad_state():
    tracker = FaceTracker("camera-a", ttl_seconds=1.0)
    old = tracker.update([(10, 10, 110, 110)], 1.0).tracks[0]
    old.anti_spoof_passed = True
    old.anti_spoof_checked_at = 1.0
    tracker.update([], 2.1)
    new = tracker.update([(10, 10, 110, 110)], 2.2).tracks[0]
    assert new.track_id != old.track_id
    assert new.anti_spoof_passed is False
    assert new.anti_spoof_checked_at is None
