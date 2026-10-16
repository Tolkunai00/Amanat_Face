from dataclasses import dataclass
from enum import Enum


class RejectionReason(str, Enum):
    NO_FACE = "NO_FACE"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    LOW_QUALITY = "LOW_QUALITY"
    BLUR = "BLUR"
    TOO_DARK = "TOO_DARK"
    LIVENESS_PENDING = "LIVENESS_PENDING"
    LIVENESS_FAILED = "LIVENESS_FAILED"
    PAD_FAILED = "PAD_FAILED"
    UNKNOWN_FACE = "UNKNOWN_FACE"
    LOW_SIMILARITY = "LOW_SIMILARITY"
    LOW_MARGIN = "LOW_MARGIN"
    DEBOUNCED = "DEBOUNCED"


@dataclass(slots=True)
class FaceDecision:
    track_id: str
    bbox: tuple[int, int, int, int]
    student_id: str | None = None
    detection_profile: str = "fast"
    detection_confidence: float | None = None
    quality_passed: bool = False
    quality_reason: str = ""
    liveness_passed: bool = False
    anti_spoof_passed: bool = False
    anti_spoof_bypassed: bool = False
    anti_spoof_score: float | None = None
    pad_passed: bool | None = None
    pad_enforced: bool = False
    pad_live_score: float | None = None
    pad_class: str = ""
    pad_probabilities: dict[str, float] | None = None
    pad_inference_ms: float | None = None
    pad_threshold: float | None = None
    pad_model_name: str = ""
    pad_error: str = ""
    recognition_score: float | None = None
    recognition_top2_score: float | None = None
    recognition_margin: float | None = None
    recognition_passed: bool = False
    identity_matched: bool = False
    temporal_liveness_passed: bool = False
    attendance_allowed: bool = False
    rejection_reason: str = ""

    def finalize(self) -> "FaceDecision":
        production_pad_allowed = self.anti_spoof_passed or self.anti_spoof_bypassed
        experimental_pad_allowed = not self.pad_enforced or self.pad_passed is True
        self.temporal_liveness_passed = bool(
            self.temporal_liveness_passed or self.liveness_passed
        )
        self.liveness_passed = self.temporal_liveness_passed
        self.identity_matched = bool(self.identity_matched or self.recognition_passed)
        self.recognition_passed = self.identity_matched
        self.attendance_allowed = bool(
            self.quality_passed
            and production_pad_allowed
            and experimental_pad_allowed
            and self.temporal_liveness_passed
            and self.identity_matched
            and self.student_id
        )
        if self.attendance_allowed:
            self.rejection_reason = ""
        elif not self.quality_passed:
            self.rejection_reason = self.quality_reason or RejectionReason.LOW_QUALITY.value
        elif (not production_pad_allowed or not experimental_pad_allowed) and not self.rejection_reason:
            self.rejection_reason = RejectionReason.PAD_FAILED.value
        elif not self.liveness_passed and not self.rejection_reason:
            self.rejection_reason = RejectionReason.LIVENESS_PENDING.value
        elif not self.recognition_passed and not self.rejection_reason:
            self.rejection_reason = RejectionReason.UNKNOWN_FACE.value
        return self


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    name: str
    class_name: str
    box: tuple[int, int, int, int]
    is_fake: bool
    is_live: bool
    student_id: str = ""
    track_id: str = ""
    decision: FaceDecision | None = None
