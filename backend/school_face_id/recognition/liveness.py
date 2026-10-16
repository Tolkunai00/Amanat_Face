from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class _Track:
    samples: list[tuple[float, np.ndarray, np.ndarray]] = field(default_factory=list)
    last_seen: float = 0.0


class TemporalLiveness:
    """Passive liveness gate based on temporal head-pose change.

    It deliberately cannot approve a face from one sharp frame. This is still
    a passive heuristic, not a certified anti-spoofing model.
    """

    def __init__(
        self,
        required_samples: int,
        window_seconds: float,
        min_face_ratio: float,
        min_texture_score: float,
        max_position_motion: float,
        min_pose_change: float,
    ):
        self.required_samples = max(2, int(required_samples))
        self.window_seconds = max(0.5, float(window_seconds))
        self.min_face_ratio = float(min_face_ratio)
        self.min_texture_score = float(min_texture_score)
        self.max_position_motion = float(max_position_motion)
        self.min_pose_change = float(min_pose_change)
        self.tracks: dict[str, _Track] = {}

    def observe(
        self,
        track_id: str,
        now: float,
        face_ratio: float,
        texture_score: float,
        position_signature,
        pose_signature,
    ) -> bool:
        self._prune(now)
        track = self.tracks.setdefault(track_id, _Track())
        track.last_seen = now
        if (
            face_ratio < self.min_face_ratio
            or texture_score < self.min_texture_score
            or pose_signature is None
        ):
            track.samples.clear()
            return False

        position = np.asarray(position_signature, dtype=np.float32)
        pose = np.asarray(pose_signature, dtype=np.float32)
        track.samples.append((now, position, pose))
        track.samples = [
            sample for sample in track.samples[-self.required_samples * 3 :]
            if now - sample[0] <= self.window_seconds
        ]
        if len(track.samples) < self.required_samples:
            return False

        newest_position = track.samples[-1][1]
        position_motion = max(
            float(np.linalg.norm(newest_position - sample[1]))
            for sample in track.samples[:-1]
        )
        pose_change = max(
            float(np.linalg.norm(pose - sample[2]))
            for sample in track.samples[:-1]
        )
        return position_motion <= self.max_position_motion and pose_change >= self.min_pose_change

    def _prune(self, now: float) -> None:
        stale_before = now - self.window_seconds * 2
        self.tracks = {key: value for key, value in self.tracks.items() if value.last_seen >= stale_before}

    def remove(self, track_id: str) -> None:
        self.tracks.pop(track_id, None)

    def clear(self) -> None:
        self.tracks.clear()
