"""Small per-camera face tracker with no external runtime dependency."""

from __future__ import annotations

from dataclasses import dataclass
import math
import logging
import threading


BBox = tuple[int, int, int, int]
logger = logging.getLogger(__name__)


def _iou(left: BBox, right: BBox) -> float:
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    left_area = max(0, left[2] - left[0]) * max(0, left[3] - left[1])
    right_area = max(0, right[2] - right[0]) * max(0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _center(box: BBox) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _diagonal(box: BBox) -> float:
    return max(1.0, math.hypot(box[2] - box[0], box[3] - box[1]))


@dataclass
class FaceTrack:
    track_id: str
    bbox: BBox
    last_seen: float
    missed_frames: int = 0
    recognized_student_id: str | None = None
    liveness_passed: bool = False
    anti_spoof_passed: bool = False
    anti_spoof_bypassed: bool = False
    anti_spoof_score: float | None = None
    anti_spoof_checked_at: float | None = None
    anti_spoof_class: str = ""
    anti_spoof_probabilities: dict[str, float] | None = None
    anti_spoof_inference_ms: float | None = None
    fsvfm_checked_at: float | None = None
    fsvfm_live_score: float | None = None
    fsvfm_passed: bool | None = None
    fsvfm_threshold: float | None = None
    fsvfm_model_name: str = ""
    fsvfm_latency_ms: float | None = None
    fsvfm_error: str = ""


@dataclass(frozen=True)
class TrackerUpdate:
    tracks: tuple[FaceTrack, ...]
    expired_track_ids: tuple[str, ...]


class FaceTracker:
    """Assign stable IDs using IoU, normalized center distance and a short TTL.

    One instance belongs to exactly one camera worker. Recognition identity is
    optional track metadata and never participates in detection association.
    """

    def __init__(
        self,
        camera_id: str,
        max_missed_frames: int = 8,
        ttl_seconds: float = 3.0,
        min_iou: float = 0.10,
        max_center_distance: float = 0.85,
    ):
        self.camera_id = str(camera_id)
        self.max_missed_frames = max(0, int(max_missed_frames))
        self.ttl_seconds = max(0.1, float(ttl_seconds))
        self.min_iou = float(min_iou)
        self.max_center_distance = float(max_center_distance)
        self._tracks: dict[str, FaceTrack] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    @property
    def tracks(self) -> dict[str, FaceTrack]:
        return self._tracks

    def _new_track(self, bbox: BBox, now: float) -> FaceTrack:
        track_id = f"track-{self._next_id:06d}"
        self._next_id += 1
        track = FaceTrack(track_id=track_id, bbox=bbox, last_seen=now)
        self._tracks[track_id] = track
        logger.debug("face_track_created camera_id=%s track_id=%s", self.camera_id, track_id)
        return track

    def _cost(self, track: FaceTrack, bbox: BBox) -> float | None:
        overlap = _iou(track.bbox, bbox)
        old_center, new_center = _center(track.bbox), _center(bbox)
        distance = math.dist(old_center, new_center) / max(
            _diagonal(track.bbox), _diagonal(bbox)
        )
        if overlap < self.min_iou and distance > self.max_center_distance:
            return None
        old_area = max(1, (track.bbox[2] - track.bbox[0]) * (track.bbox[3] - track.bbox[1]))
        new_area = max(1, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        size_penalty = abs(math.log(new_area / old_area))
        return (1.0 - overlap) + distance + 0.20 * size_penalty

    def update(self, detections: list[BBox] | tuple[BBox, ...], now: float) -> TrackerUpdate:
        boxes = [tuple(int(value) for value in box) for box in detections]
        with self._lock:
            expired = [
                track_id for track_id, track in self._tracks.items()
                if now - track.last_seen > self.ttl_seconds
            ]
            for track_id in expired:
                del self._tracks[track_id]
                logger.debug(
                    "face_track_expired camera_id=%s track_id=%s", self.camera_id, track_id
                )
            candidates = []
            for track_id, track in self._tracks.items():
                for detection_index, bbox in enumerate(boxes):
                    cost = self._cost(track, bbox)
                    if cost is not None:
                        candidates.append((cost, track_id, detection_index))

            assigned_tracks: set[str] = set()
            assigned_detections: set[int] = set()
            detection_tracks: dict[int, FaceTrack] = {}
            for _, track_id, detection_index in sorted(candidates):
                if track_id in assigned_tracks or detection_index in assigned_detections:
                    continue
                track = self._tracks[track_id]
                track.bbox = boxes[detection_index]
                track.last_seen = now
                track.missed_frames = 0
                assigned_tracks.add(track_id)
                assigned_detections.add(detection_index)
                detection_tracks[detection_index] = track

            for index, bbox in enumerate(boxes):
                if index not in assigned_detections:
                    detection_tracks[index] = self._new_track(bbox, now)

            for track_id, track in self._tracks.items():
                if track_id not in assigned_tracks and track not in detection_tracks.values():
                    track.missed_frames += 1

            missed_expired = [
                track_id for track_id, track in self._tracks.items()
                if track.missed_frames > self.max_missed_frames
                or now - track.last_seen > self.ttl_seconds
            ]
            for track_id in missed_expired:
                del self._tracks[track_id]
                logger.debug(
                    "face_track_expired camera_id=%s track_id=%s", self.camera_id, track_id
                )
            expired.extend(missed_expired)
            return TrackerUpdate(
                tracks=tuple(detection_tracks[index] for index in range(len(boxes))),
                expired_track_ids=tuple(expired),
            )

    def clear(self) -> tuple[str, ...]:
        with self._lock:
            expired = tuple(self._tracks)
            self._tracks.clear()
            return expired
