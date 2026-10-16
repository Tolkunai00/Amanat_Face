import os
import logging
import threading
import time
import zlib
from dataclasses import dataclass

os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    # TCP is much less likely than UDP to leave an RTSP preview frozen when
    # packets are lost on a Wi-Fi/local network.
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;0",
)

import cv2
import numpy as np
from insightface.app import FaceAnalysis

from ..repositories.recognition import load_recognition_embeddings
from ..services import attendance as attendance_service
from .liveness import TemporalLiveness
from .anti_spoof import evaluate_anti_spoof
from .fsvfm_pad import evaluate_fsvfm
from .embedding import (
    InvalidEmbeddingError, embedding_from_face, extract_embedding,
    normalize_embedding, select_largest_face,
)
from .matcher import match_identity, match_identity_details
from .models import FaceDecision, RecognitionResult, RejectionReason
from .tracker import FaceTracker

from ..config.settings import (
    ANTI_SPOOFING_CACHE_TTL_SECONDS,
    CAMERA_FPS,
    CAMERA_OFFLINE_TIMEOUT_SECONDS,
    CAMERA_READ_MAX_SECONDS,
    CAMERA_READ_SLEEP_SECONDS,
    CAMERA_STALE_CONSECUTIVE_FRAMES,
    CAMERA_STALE_ENABLED,
    CAMERA_STALE_TIMEOUT_SECONDS,
    CAMERA_STREAM_WIDTH,
    FACE_DETECTION_PROFILES,
    FACE_DETECTION_FALLBACK_ENABLED,
    FACE_DETECTION_MIN_CONFIDENCE,
    FACE_MODEL_NAME,
    FACE_TRACK_MAX_MISSED_FRAMES,
    FACE_TRACK_TTL_SECONDS,
    FSVFM_ENABLED,
    FSVFM_SHADOW_MODE,
    JPEG_QUALITY,
    LIVENESS_MAX_FACE_MOTION,
    LIVENESS_MIN_FACE_RATIO,
    LIVENESS_MIN_TEXTURE_SCORE,
    LIVENESS_MIN_POSE_CHANGE,
    LIVENESS_REQUIRED_SAMPLES,
    LIVENESS_WINDOW_SECONDS,
    LOW_LIGHT_GAMMA,
    LOW_LIGHT_MEAN_THRESHOLD,
    RECOGNITION_MARGIN,
    RECOGNITION_THRESHOLD,
    RECOGNITION_INTERVAL_SECONDS,
    RECOGNITION_WIDTH,
    UNKNOWN_NAME,
)
from ..observability import metrics as runtime_metrics

logger = logging.getLogger(__name__)
face_lock = threading.Lock()
known_cache_lock = threading.Lock()
workers_lock = threading.Lock()
camera_workers = {}
_face_apps = {}
_face_inference_locks = {}
_identity_model_state = {"status": "not_loaded", "error": ""}

CAMERA_HEALTHY = "HEALTHY"
CAMERA_DEGRADED = "DEGRADED"
CAMERA_OFFLINE = "OFFLINE"

KNOWN_FACE_COLOR = (0, 220, 0)  # Жашыл рамка
UNKNOWN_FACE_COLOR = (0, 0, 255)  # Кызыл рамка
LOW_LIGHT_LUT = np.array(
    [((value / 255.0) ** max(LOW_LIGHT_GAMMA, 0.1)) * 255 for value in range(256)],
    dtype=np.uint8,
)

known_cache = {
    "loaded_at": 0.0,
    "student_ids": [],
    "names": [],
    "parent_ids": [],
    "embeddings": np.empty((0, 512), dtype=np.float32),
    "classes": [],
}


class LivenessState:
    def __init__(self):
        self.temporal = TemporalLiveness(
            required_samples=LIVENESS_REQUIRED_SAMPLES,
            window_seconds=LIVENESS_WINDOW_SECONDS,
            min_face_ratio=LIVENESS_MIN_FACE_RATIO,
            min_texture_score=LIVENESS_MIN_TEXTURE_SCORE,
            max_position_motion=LIVENESS_MAX_FACE_MOTION,
            min_pose_change=LIVENESS_MIN_POSE_CHANGE,
        )

    def _face_texture_score(self, frame, box):
        x1, y1, x2, y2 = clamp_box(box, frame.shape)
        if x2 <= x1 or y2 <= y1:
            return 0.0
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return 0.0
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    def _position_signature(self, box, frame_shape):
        height, width = frame_shape[:2]
        x1, y1, x2, y2 = [float(value) for value in box]
        return np.asarray([
            ((x1 + x2) / 2.0) / max(1.0, width),
            ((y1 + y2) / 2.0) / max(1.0, height),
            (x2 - x1) / max(1.0, width),
            (y2 - y1) / max(1.0, height),
        ], dtype=np.float32)

    def _pose_signature(self, face):
        keypoints = np.asarray(getattr(face, "kps", []), dtype=np.float32)
        if keypoints.shape[0] < 5 or keypoints.shape[1] < 2:
            return None
        left_eye, right_eye, nose, left_mouth, right_mouth = keypoints[:5, :2]
        eye_mid = (left_eye + right_eye) / 2.0
        mouth_mid = (left_mouth + right_mouth) / 2.0
        eye_distance = max(float(np.linalg.norm(right_eye - left_eye)), 1.0)
        return np.asarray([
            (nose[0] - eye_mid[0]) / eye_distance,
            (nose[1] - eye_mid[1]) / eye_distance,
            (mouth_mid[0] - nose[0]) / eye_distance,
        ], dtype=np.float32)

    def is_live(self, track_id, frame, face, box):
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = clamp_box(box, frame.shape)
        face_ratio = box_area((x1, y1, x2, y2)) / max(1, width * height)
        return self.temporal.observe(
            track_id=track_id,
            now=time.monotonic(),
            face_ratio=face_ratio,
            texture_score=self._face_texture_score(frame, (x1, y1, x2, y2)),
            position_signature=self._position_signature((x1, y1, x2, y2), frame.shape),
            pose_signature=self._pose_signature(face),
        )

    def remove(self, track_id):
        self.temporal.remove(track_id)


@dataclass(frozen=True, slots=True)
class DetectionBatch:
    faces: tuple
    profile_name: str
    elapsed_ms: float
    fallback_used: bool

class CameraWorker:
    """Own one camera, one recognition loop, and one shared encoded frame."""

    def __init__(self, status, source):
        self.status_name = status
        self.source = source
        self.is_rtsp = isinstance(source, str) and source.lower().startswith("rtsp://")
        self.cap = None
        self.running = False
        self.thread = None
        self.watchdog_thread = None
        self.recognition_thread = None
        self._pending_recognition = None
        self._recognition_generation = 0
        self.recognition_error = ""
        self._watchdog_reported_at = 0.0
        self.condition = threading.Condition()
        self.reconnect_lock = threading.Lock()
        self.jpeg = None
        self.version = 0
        self.state = "disconnected"
        self.error = ""
        self.last_frame_at = 0.0
        self.last_error_at = 0.0
        self.last_reconnect_at = 0.0
        self.frames_read_total = 0
        self.frames_failed_total = 0
        self.reconnects_total = 0
        self.consecutive_failures = 0
        self.stale_total = 0
        self._read_started_at = 0.0
        self._last_fingerprint = None
        self._identical_frames = 0
        self._identical_since = 0.0
        self._last_capture_position = None
        self._position_streak = 0
        self.reconnect_delay = 1.0
        self.next_reconnect_at = 0.0
        self.liveness_state = LivenessState()
        self.face_tracker = FaceTracker(
            camera_id=status,
            max_missed_frames=FACE_TRACK_MAX_MISSED_FRAMES,
            ttl_seconds=FACE_TRACK_TTL_SECONDS,
        )
        self.current_people = []
        self.last_recognition = 0.0

    def _open_camera(self):
        source = self.source
        if self.is_rtsp:
            params = []
            if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
                params.extend((cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000))
            if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
                params.extend((cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000))
            camera = cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)
        else:
            backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
            camera = cv2.VideoCapture(source, backend)
            if not camera.isOpened() and backend != cv2.CAP_ANY:
                camera.release()
                camera = cv2.VideoCapture(source, cv2.CAP_ANY)
            camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            camera.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        camera_name = "RTSP" if self.is_rtsp else str(source)
        logger.info("camera opened=%s", camera.isOpened(), extra={"event": "camera_open", "camera_id": camera_name})
        return camera

    def start(self):
        with self.condition:
            if self.thread is not None and self.thread.is_alive():
                return
            self.running = True
            if self.recognition_thread is None or not self.recognition_thread.is_alive():
                self.recognition_thread = threading.Thread(
                    target=self._recognition_loop,
                    name=f"camera-recognition-{self.status_name}",
                    daemon=True,
                )
                self.recognition_thread.start()
            self.thread = threading.Thread(
                target=self._run,
                name=f"camera-{self.status_name}",
                daemon=True,
            )
            self.thread.start()
            if self.watchdog_thread is None or not self.watchdog_thread.is_alive():
                self.watchdog_thread = threading.Thread(
                    target=self._watchdog_loop,
                    name=f"camera-watchdog-{self.status_name}",
                    daemon=True,
                )
                self.watchdog_thread.start()

    def _set_state(self, state, error=""):
        with self.condition:
            self.state = state
            self.error = str(error or "")

    def _publish(self, jpeg):
        with self.condition:
            self.jpeg = jpeg
            self.version += 1
            self.condition.notify_all()

    def _publish_unavailable(self, message):
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(
            frame, message, (34, 165), cv2.FONT_HERSHEY_SIMPLEX, .78,
            (255, 255, 255), 2, cv2.LINE_AA,
        )
        ok, buffer = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
        )
        if ok:
            self._publish(buffer.tobytes())

    def _record_failure(self, error):
        self.frames_failed_total += 1
        self.consecutive_failures += 1
        self.last_error_at = time.time()
        self.error = str(error or "Camera read failed")

    def _schedule_reconnect(self, error, state="disconnected", expected_capture=None):
        with self.reconnect_lock:
            if expected_capture is not None and self.cap is not expected_capture:
                return False
            self._record_failure(error)
            with self.condition:
                self._recognition_generation += 1
                self._pending_recognition = None
                self.current_people = []
            self._release_capture()
            self._set_state(state, error)
            self._publish_unavailable("Camera unavailable")
            self.last_reconnect_at = time.time()
            self.reconnects_total += 1
            self.next_reconnect_at = time.monotonic() + self.reconnect_delay
            self.reconnect_delay = min(self.reconnect_delay * 2.0, 60.0)
            return True

    def _watchdog_loop(self):
        while self.running:
            self._watchdog_check()
            time.sleep(0.1)

    def _watchdog_check(self, now=None):
        capture = self.cap
        started = self._read_started_at
        now = time.monotonic() if now is None else now
        if capture is not None and started and now - started > CAMERA_READ_MAX_SECONDS:
            # OpenCV captures are not thread safe: release during read can
            # deadlock or crash the process. The reader reconnects on return.
            if self._watchdog_reported_at != started:
                self._watchdog_reported_at = started
                self._set_state("error", f"Camera read exceeded {CAMERA_READ_MAX_SECONDS:g}s")
                self._publish_unavailable("Camera read timeout")
                return True
        return False

    @staticmethod
    def _frame_fingerprint(frame):
        height, width = frame.shape[:2]
        sample = frame[::max(1, height // 16), ::max(1, width // 16)]
        return zlib.adler32(np.ascontiguousarray(sample).tobytes())

    def _frame_is_stale(self, frame, capture, now):
        if not CAMERA_STALE_ENABLED:
            return False
        fingerprint = self._frame_fingerprint(frame)
        try:
            position = float(capture.get(cv2.CAP_PROP_POS_MSEC))
        except Exception:
            position = 0.0
        position_stuck = (
            position > 0
            and self._last_capture_position is not None
            and position <= self._last_capture_position
        )
        self._position_streak = self._position_streak + 1 if position_stuck else 0
        if position > 0:
            self._last_capture_position = position
        identical = fingerprint == self._last_fingerprint
        self._last_fingerprint = fingerprint
        # Live backends may report a fixed/non-monotonic POS_MSEC even while
        # delivering fresh images. A timestamp alone must not force reconnects.
        if identical:
            if self._identical_frames == 0:
                self._identical_since = now
            self._identical_frames += 1
        else:
            self._identical_frames = 0
            self._identical_since = 0.0
        return (
            self._identical_frames >= CAMERA_STALE_CONSECUTIVE_FRAMES
            and now - self._identical_since >= CAMERA_STALE_TIMEOUT_SECONDS
        )

    def _connect(self):
        try:
            camera = self._open_camera()
            if not camera.isOpened():
                camera.release()
                self._schedule_reconnect("Unable to connect to camera")
                return False
            self.cap = camera
            self._last_fingerprint = None
            self._identical_frames = 0
            self._identical_since = 0.0
            self._last_capture_position = None
            self._position_streak = 0
            with self.condition:
                self._recognition_generation += 1
                self._pending_recognition = None
                self.current_people = []
            self._set_state("reconnecting")
            return True
        except Exception as exc:
            logger.exception("camera connect error", extra={"event": "camera_connect_error", "camera_id": self.status_name, "error_type": type(exc).__name__})
            self._schedule_reconnect(exc, "error")
            return False

    def _run(self):
        try:
            self._capture_loop()
        except Exception as exc:
            logger.exception("camera worker failed", extra={"event": "camera_worker_error", "camera_id": self.status_name})
            self._set_state("error", exc)
            self._publish_unavailable("Camera processing error")
        finally:
            self.running = False
            self._release_capture()
            with self.condition:
                self.condition.notify_all()

    def _recognition_loop(self):
        generation = -1
        while self.running:
            with self.condition:
                self.condition.wait_for(lambda: self._pending_recognition is not None or not self.running)
                if not self.running:
                    return
                frame, frame_generation = self._pending_recognition
                self._pending_recognition = None
            if generation != frame_generation:
                self.liveness_state = LivenessState()
                self.face_tracker = FaceTracker(
                    camera_id=self.status_name,
                    max_missed_frames=FACE_TRACK_MAX_MISSED_FRAMES,
                    ttl_seconds=FACE_TRACK_TTL_SECONDS,
                )
                generation = frame_generation
            try:
                people = recognize_frame(frame, self.status_name, self.liveness_state, [], self.face_tracker)
                with self.condition:
                    if self.running and frame_generation == self._recognition_generation:
                        self.current_people = people
                        self.recognition_error = ""
            except Exception as exc:
                logger.exception("recognition error", extra={"event": "recognition_error", "camera_id": self.status_name, "error_type": type(exc).__name__})
                runtime_metrics.increment("recognition_errors_total")
                with self.condition:
                    self.current_people = []
                    self.recognition_error = str(exc)

    def _capture_loop(self):
        while self.running:
            if self.cap is None:
                wait = self.next_reconnect_at - time.monotonic()
                if wait > 0:
                    time.sleep(min(wait, .25))
                    continue
                if not self._connect():
                    continue
            capture = self.cap
            read_started_at = time.monotonic()
            self._read_started_at = read_started_at
            try:
                ok, frame = capture.read()
            except Exception as exc:
                logger.exception("camera read error", extra={"event": "camera_read_error", "camera_id": self.status_name, "error_type": type(exc).__name__})
                ok, frame = False, None
            finally:
                read_finished_at = time.monotonic()
                self._read_started_at = 0.0
            if capture is not self.cap:
                continue
            if read_finished_at - read_started_at > CAMERA_READ_MAX_SECONDS:
                self._schedule_reconnect("Camera read timeout", "error", capture)
                continue
            if not ok or frame is None:
                self._schedule_reconnect("Camera read failed", expected_capture=capture)
                continue

            self.frames_read_total += 1
            if self._frame_is_stale(frame, capture, read_finished_at):
                self.stale_total += 1
                self._schedule_reconnect("Stale camera stream", "error", capture)
                continue
            self.last_frame_at = time.time()
            self.consecutive_failures = 0
            self.reconnect_delay = 1.0
            self._set_state("working")
            now = time.monotonic()
            if now - self.last_recognition >= RECOGNITION_INTERVAL_SECONDS:
                self.last_recognition = now
                with self.condition:
                    # One latest frame, never an unbounded inference backlog.
                    self._pending_recognition = (frame.copy(), self._recognition_generation)
                    self.condition.notify_all()

            for result in self.current_people:
                if result.is_fake:
                    draw_face_box(frame, result.box, UNKNOWN_FACE_COLOR, "Fake Face")
                else:
                    color = KNOWN_FACE_COLOR if result.name != UNKNOWN_NAME else UNKNOWN_FACE_COLOR
                    draw_face_box(frame, result.box, color)

            height, width = frame.shape[:2]
            if CAMERA_STREAM_WIDTH > 0 and width > CAMERA_STREAM_WIDTH:
                output_height = max(1, round(height * CAMERA_STREAM_WIDTH / width))
                frame = cv2.resize(
                    frame, (CAMERA_STREAM_WIDTH, output_height), interpolation=cv2.INTER_AREA
                )
            encoded, buffer = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
            )
            if encoded:
                self._publish(buffer.tobytes())
            if CAMERA_READ_SLEEP_SECONDS > 0:
                time.sleep(CAMERA_READ_SLEEP_SECONDS)
            else:
                time.sleep(max(.001, 1 / max(1, CAMERA_FPS)))
        self._release_capture()

    def wait_for_frame(self, last_version, timeout=5.0):
        with self.condition:
            self.condition.wait_for(
                lambda: self.version != last_version or not self.running,
                timeout=timeout,
            )
            return self.version, self.jpeg

    def public_status(self):
        with self.condition:
            return {
                "camera": self.status_name,
                "camera_id": self.status_name,
                "state": self.state,
                "status": self._health_status(),
                "last_frame_at": self.last_frame_at or None,
                "last_error_at": self.last_error_at or None,
                "last_reconnect_at": self.last_reconnect_at or None,
                "frames_read_total": self.frames_read_total,
                "frames_failed_total": self.frames_failed_total,
                "reconnects_total": self.reconnects_total,
                "consecutive_failures": self.consecutive_failures,
                "stale_total": self.stale_total,
                "reconnect_in_seconds": max(0, round(self.next_reconnect_at - time.monotonic(), 1))
                if self.cap is None else 0,
                "error": self.error,
                "recognition_error": self.recognition_error,
            }

    def _health_status(self, now=None):
        now = time.time() if now is None else now
        if not self.last_frame_at or now - self.last_frame_at > CAMERA_OFFLINE_TIMEOUT_SECONDS:
            return CAMERA_OFFLINE
        stale_approaching = self._identical_frames >= max(
            1, CAMERA_STALE_CONSECUTIVE_FRAMES // 2
        )
        if self.cap is None or self.consecutive_failures or stale_approaching or self.recognition_error or self.state == "error":
            return CAMERA_DEGRADED
        return CAMERA_HEALTHY

    def _release_capture(self):
        capture, self.cap = self.cap, None
        if capture is None:
            return
        try:
            capture.release()
        except Exception:
            pass

    def stop(self):
        self.running = False
        with self.condition:
            self.condition.notify_all()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=6.0)
        if self.watchdog_thread and self.watchdog_thread.is_alive():
            self.watchdog_thread.join(timeout=1.0)
        if self.recognition_thread and self.recognition_thread.is_alive():
            self.recognition_thread.join(timeout=1.0)


def get_camera_worker(status, source):
    with workers_lock:
        worker = camera_workers.get(status)
        if worker is None:
            worker = CameraWorker(status, source)
            camera_workers[status] = worker
            worker.start()
        elif worker.source != source:
            worker.stop()
            worker = CameraWorker(status, source)
            camera_workers[status] = worker
            worker.start()
        elif not worker.running and not (worker.thread and worker.thread.is_alive()):
            worker.start()
        return worker


def start_camera_workers(camera_sources):
    for status, source in camera_sources.items():
        get_camera_worker(status, source)


def shutdown_camera_workers():
    with workers_lock:
        workers = list(camera_workers.values())
        camera_workers.clear()
    for worker in workers:
        worker.stop()


def get_face_app(profile_name="fast"):
    profiles = dict(FACE_DETECTION_PROFILES)
    if profile_name not in profiles:
        raise ValueError(f"Unknown face detection profile: {profile_name}")
    app = _face_apps.get(profile_name)
    if app is not None:
        return app
    with face_lock:
        app = _face_apps.get(profile_name)
        if app is None:
            _identity_model_state.update(status="loading", error="")
            try:
                det_size = profiles[profile_name]
                app = FaceAnalysis(
                    name=FACE_MODEL_NAME,
                    allowed_modules=["detection", "recognition"],
                    providers=["CPUExecutionProvider"],
                )
                app.prepare(ctx_id=0, det_size=(det_size, det_size))
                _face_apps[profile_name] = app
                _face_inference_locks[profile_name] = threading.Lock()
                _identity_model_state.update(status="ready", error="")
            except Exception as exc:
                _identity_model_state.update(
                    status="not_ready", error=f"{type(exc).__name__}: {exc}"
                )
                raise
    return app


def identity_model_status():
    """Return diagnostic model state without exposing model-cache paths."""
    return dict(_identity_model_state)


def _run_face_app(profile_name, frame):
    app = get_face_app(profile_name)
    with _face_inference_locks[profile_name]:
        return app.get(frame)


def analyze_faces(frame, profile_name="fast"):
    """Thread-safe access to a cached shared FaceAnalysis profile."""
    return _run_face_app(profile_name, frame)


def _face_detection_acceptable(face, frame_shape):
    score = float(getattr(face, "det_score", 1.0))
    return score >= FACE_DETECTION_MIN_CONFIDENCE and is_reasonable_face_box(
        face.bbox, frame_shape
    )


def detect_faces(frame, allow_fallback=FACE_DETECTION_FALLBACK_ENABLED):
    """Try larger detector profiles only when the previous result is unusable."""
    started = time.perf_counter()
    profiles = list(FACE_DETECTION_PROFILES)
    if not allow_fallback:
        profiles = profiles[:2]
    for index, (profile_name, _) in enumerate(profiles):
        faces = tuple(_run_face_app(profile_name, frame))
        accepted = tuple(face for face in faces if _face_detection_acceptable(face, frame.shape))
        if accepted:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            fallback_used = index > 0
            if fallback_used:
                logger.debug(
                    "detection_fallback_used profile=%s elapsed_ms=%.2f", profile_name, elapsed_ms
                )
            else:
                logger.debug("face_detection profile=%s elapsed_ms=%.2f", profile_name, elapsed_ms)
            return DetectionBatch(accepted, profile_name, elapsed_ms, fallback_used)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    profile_name = profiles[-1][0]
    logger.debug(
        "face_detection_no_face profile=%s fallback_used=%s elapsed_ms=%.2f",
        profile_name, len(profiles) > 1, elapsed_ms,
    )
    return DetectionBatch((), profile_name, elapsed_ms, len(profiles) > 1)


def get_fast_face_app():
    return get_face_app(FACE_DETECTION_PROFILES[0][0])


def warm_up_face_models():
    def worker():
        try:
            get_face_app(FACE_DETECTION_PROFILES[0][0])
        except Exception as exc:
            logger.exception("face model warmup error", extra={"event": "face_model_warmup_error", "error_type": type(exc).__name__})

    threading.Thread(target=worker, daemon=True).start()


def rotate_frame_with_matrix(frame, angle):
    height, width = frame.shape[:2]
    center = (width / 2, height / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        frame,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )
    return rotated, matrix


def rotate_frame(frame, angle):
    return rotate_frame_with_matrix(frame, angle)[0]


def enrollment_variants(frame):
    yield frame
    yield enhance_low_light_frame(frame)
    yield sharpen_frame(frame)
    for angle in (-12, 12, -20, 20):
        yield rotate_frame(frame, angle)
    flipped = cv2.flip(frame, 1)
    yield flipped
    yield sharpen_frame(flipped)


def extract_face_embedding(frame, app=None, use_variants=False):
    """Canonical extraction; variant averaging is opt-in for legacy imports."""
    if frame is None:
        return None
    detect = app.get if app is not None else lambda image: analyze_faces(image, "accurate")
    if not use_variants:
        faces = detect(frame)
        return embedding_from_face(select_largest_face(faces)) if faces else None
    embeddings = []
    for variant in enrollment_variants(frame):
        faces = detect(variant)
        if not faces:
            continue
        try:
            embeddings.append(embedding_from_face(select_largest_face(faces)))
        except InvalidEmbeddingError:
            continue
    if not embeddings:
        return None
    return normalize_embedding(np.mean(embeddings, axis=0)).astype(np.float32)


def load_known_faces(force=False):
    now = time.time()
    with known_cache_lock:
        if not force and now - known_cache["loaded_at"] < 5:
            return known_cache.copy()

    student_ids, names, parent_ids, embeddings, classes = load_recognition_embeddings()
    valid_rows = []
    for student_id, name, parent_id, embedding, class_name in zip(student_ids, names, parent_ids, embeddings, classes):
        embedding = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if embedding.size != 512 or not np.isfinite(embedding).all():
            continue
        valid_rows.append((student_id, name, parent_id, embedding, class_name))

    if valid_rows:
        student_ids, names, parent_ids, embeddings, classes = map(list, zip(*valid_rows))
        matrix = np.asarray(embeddings, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix = matrix / np.maximum(norms, 1e-6)
    else:
        student_ids, names, parent_ids, classes = [], [], [], []
        matrix = np.empty((0, 512), dtype=np.float32)

    with known_cache_lock:
        known_cache.update({
            "loaded_at": now,
            "student_ids": student_ids,
            "names": names,
            "parent_ids": parent_ids,
            "embeddings": matrix,
            "classes": classes,
        })
        return known_cache.copy()


def recognize_face(embedding, known, threshold=RECOGNITION_THRESHOLD, margin=RECOGNITION_MARGIN):
    match = match_identity(
        embedding,
        known["student_ids"],
        known["names"],
        known["parent_ids"],
        known["classes"],
        known["embeddings"],
        threshold,
        margin,
    )
    if match is None:
        return "", UNKNOWN_NAME, "", ""
    return match["student_id"], match["name"], match["parent_id"], match["class_name"]


def resize_for_recognition(frame):
    height, width = frame.shape[:2]
    if width <= RECOGNITION_WIDTH:
        return frame, 1.0, 1.0
    scale = RECOGNITION_WIDTH / width
    resized = cv2.resize(frame, (RECOGNITION_WIDTH, int(height * scale)))
    return resized, width / resized.shape[1], height / resized.shape[0]


def enhance_low_light_frame(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if float(np.mean(gray)) >= LOW_LIGHT_MEAN_THRESHOLD:
        return frame
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced_l = clahe.apply(l_channel)
    enhanced = cv2.merge((enhanced_l, a_channel, b_channel))
    enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
    enhanced = cv2.convertScaleAbs(enhanced, alpha=1.28, beta=24)
    return cv2.LUT(enhanced, LOW_LIGHT_LUT)


def sharpen_frame(frame):
    blurred = cv2.GaussianBlur(frame, (0, 0), 1.0)
    return cv2.addWeighted(frame, 1.45, blurred, -0.45, 0)


def clamp_box(box, frame_shape):
    height, width = frame_shape[:2]
    x1, y1, x2, y2 = box
    x1, x2 = sorted((int(x1), int(x2)))
    y1, y2 = sorted((int(y1), int(y2)))
    return (
        max(0, min(width - 1, x1)),
        max(0, min(height - 1, y1)),
        max(0, min(width - 1, x2)),
        max(0, min(height - 1, y2)),
    )


def box_area(box):
    x1, y1, x2, y2 = box
    return max(0, x2 - x1) * max(0, y2 - y1)


def is_reasonable_face_box(box, frame_shape):
    height, width = frame_shape[:2]
    x1, y1, x2, y2 = clamp_box(box, frame_shape)
    box_width = max(0, x2 - x1)
    box_height = max(0, y2 - y1)
    if box_width < 40 or box_height < 40:
        return False
    area_ratio = (box_width * box_height) / max(1, width * height)
    aspect_ratio = box_width / max(1, box_height)
    return 0.005 <= area_ratio <= 0.50 and 0.50 <= aspect_ratio <= 1.70


def face_box_only(box, frame_shape, scale=0.04):
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1
    pad_x = int(width * scale)
    pad_y = int(height * scale)
    return clamp_box((x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y), frame_shape)


def draw_face_box(frame, box, color, label=""):
    x1, y1, x2, y2 = clamp_box(box, frame.shape)
    if x2 <= x1 or y2 <= y1 or not is_reasonable_face_box((x1, y1, x2, y2), frame.shape):
        return

    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)
    label = (label or "").strip()
    if not label:
        return

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.6
    thickness = 2
    text_size, baseline = cv2.getTextSize(label, font, font_scale, thickness)
    text_width, text_height = text_size
    label_y1 = max(0, y1 - text_height - baseline - 8)
    label_y2 = max(text_height + baseline + 8, y1)
    label_x2 = min(frame.shape[1] - 1, x1 + text_width + 10)

    cv2.rectangle(frame, (x1, label_y1), (label_x2, label_y2), color, -1)
    cv2.putText(
        frame,
        label,
        (x1 + 5, label_y2 - baseline - 4),
        font,
        font_scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )


def log_biometric_decision(camera_id, decision):
    logger.debug(
        "biometric_decision camera_id=%s track_id=%s student_id=%s "
        "top1_similarity=%s top2_similarity=%s margin=%s fsvfm_live_score=%s "
        "temporal_liveness=%s quality_passed=%s identity_matched=%s final_decision=%s",
        camera_id,
        decision.track_id,
        decision.student_id,
        decision.recognition_score,
        decision.recognition_top2_score,
        decision.recognition_margin,
        decision.pad_live_score,
        decision.temporal_liveness_passed,
        decision.quality_passed,
        decision.identity_matched,
        "attendance_allowed" if decision.attendance_allowed else "rejected",
    )


def recognize_frame(frame, status, liveness_state, allowed_classes=None, face_tracker=None):
    """Run security gates and create attendance only from a complete decision."""
    pipeline_started = time.perf_counter()
    allowed_classes = set(allowed_classes or [])
    known = load_known_faces()
    small_frame, scale_x, scale_y = resize_for_recognition(frame)
    detection = detect_faces(small_frame)
    runtime_metrics.observe("face_detection_latency_ms", detection.elapsed_ms)
    faces = detection.faces
    tracker = face_tracker or FaceTracker(
        camera_id=status,
        max_missed_frames=FACE_TRACK_MAX_MISSED_FRAMES,
        ttl_seconds=FACE_TRACK_TTL_SECONDS,
    )
    pipeline_now = time.monotonic()
    update = tracker.update(
        [tuple(face.bbox.astype(int)) for face in faces], pipeline_now
    )
    for track_id in update.expired_track_ids:
        liveness_state.remove(track_id)
        logger.debug("face_track_expired camera_id=%s track_id=%s", status, track_id)

    people = []
    for face, track in zip(faces, update.tracks):
        x1, y1, x2, y2 = face.bbox.astype(int)
        face_box = (x1, y1, x2, y2)
        box = face_box_only(
            (int(x1 * scale_x), int(y1 * scale_y), int(x2 * scale_x), int(y2 * scale_y)),
            frame.shape,
        )
        if not is_reasonable_face_box(box, frame.shape):
            continue
        runtime_metrics.increment("recognition_total")

        decision = FaceDecision(
            track_id=track.track_id,
            bbox=box,
            detection_profile=detection.profile_name,
            detection_confidence=float(getattr(face, "det_score", 1.0)),
            quality_passed=True,
            pad_enforced=FSVFM_ENABLED and not FSVFM_SHADOW_MODE,
        )
        try:
            # InsightFace/ArcFace remains the only identity descriptor.  It is
            # validated once here and used by the matcher only after PAD and
            # temporal liveness gates complete.
            embedding = embedding_from_face(face)
        except InvalidEmbeddingError:
            decision.quality_passed = False
            decision.quality_reason = RejectionReason.LOW_QUALITY.value
            decision.finalize()
            logger.debug(
                "quality_rejected camera_id=%s track_id=%s reason=%s",
                status, track.track_id, decision.quality_reason,
            )
            log_biometric_decision(status, decision)
            runtime_metrics.increment("recognition_unknown_total")
            people.append(RecognitionResult(
                UNKNOWN_NAME, "", box, False, False, "", track.track_id, decision
            ))
            continue

        pad_cache_valid = (
            track.anti_spoof_checked_at is not None
            and pipeline_now - track.anti_spoof_checked_at <= ANTI_SPOOFING_CACHE_TTL_SECONDS
            and (track.anti_spoof_passed or track.anti_spoof_bypassed)
        )
        if not pad_cache_valid:
            pad = evaluate_anti_spoof(small_frame, face_box)
            runtime_metrics.increment("pad_total")
            if not (pad.passed or pad.bypassed):
                runtime_metrics.increment("pad_failures_total")
            track.anti_spoof_passed = pad.passed
            track.anti_spoof_bypassed = pad.bypassed
            track.anti_spoof_score = pad.score
            track.anti_spoof_checked_at = pipeline_now
            track.anti_spoof_class = pad.predicted_class
            track.anti_spoof_probabilities = pad.probabilities
            track.anti_spoof_inference_ms = pad.inference_ms
            runtime_metrics.observe("pad_latency_ms", pad.inference_ms)
            pad_event = "pad_bypass" if pad.bypassed else "pad_pass" if pad.passed else "pad_fail"
            logger.debug(
                "%s camera_id=%s track_id=%s live_score=%s pad_inference_ms=%.2f reason=%s",
                pad_event, status, track.track_id, pad.score, pad.inference_ms or 0.0,
                pad.reason or pad.error,
            )
        decision.anti_spoof_passed = track.anti_spoof_passed
        decision.anti_spoof_bypassed = track.anti_spoof_bypassed
        decision.anti_spoof_score = track.anti_spoof_score

        fsvfm_cache_valid = (
            track.fsvfm_checked_at is not None
            and pipeline_now - track.fsvfm_checked_at <= ANTI_SPOOFING_CACHE_TTL_SECONDS
        )
        if FSVFM_ENABLED and not fsvfm_cache_valid:
            fsvfm = evaluate_fsvfm(small_frame, face_box)
            track.fsvfm_checked_at = pipeline_now
            track.fsvfm_live_score = fsvfm.live_score
            track.fsvfm_passed = fsvfm.predicted_live
            track.fsvfm_threshold = fsvfm.threshold
            track.fsvfm_model_name = fsvfm.model_name
            track.fsvfm_latency_ms = fsvfm.latency_ms
            track.fsvfm_error = fsvfm.error
            logger.debug(
                "fsvfm_shadow=%s camera_id=%s track_id=%s live_score=%s "
                "predicted_live=%s threshold=%.4f latency_ms=%.2f error=%s",
                FSVFM_SHADOW_MODE,
                status,
                track.track_id,
                fsvfm.live_score,
                fsvfm.predicted_live,
                fsvfm.threshold,
                fsvfm.latency_ms,
                fsvfm.error,
            )
        decision.pad_live_score = track.fsvfm_live_score
        decision.pad_passed = track.fsvfm_passed
        decision.pad_threshold = track.fsvfm_threshold
        decision.pad_model_name = track.fsvfm_model_name
        decision.pad_inference_ms = track.fsvfm_latency_ms
        decision.pad_error = track.fsvfm_error
        decision.pad_class = (
            "live" if track.fsvfm_passed is True
            else "spoof" if track.fsvfm_passed is False and not track.fsvfm_error
            else ""
        )
        production_pad_failed = not (
            decision.anti_spoof_passed or decision.anti_spoof_bypassed
        )
        fsvfm_enforcement_failed = decision.pad_enforced and decision.pad_passed is not True
        if production_pad_failed or fsvfm_enforcement_failed:
            decision.finalize()
            log_biometric_decision(status, decision)
            runtime_metrics.increment("recognition_unknown_total")
            people.append(RecognitionResult(
                UNKNOWN_NAME, "", box, True, False, "", track.track_id, decision
            ))
            continue

        was_live = track.liveness_passed
        track.liveness_passed = track.liveness_passed or liveness_state.is_live(
            track.track_id, small_frame, face, face_box
        )
        if track.liveness_passed and not was_live:
            logger.debug("liveness_passed camera_id=%s track_id=%s", status, track.track_id)
        decision.liveness_passed = track.liveness_passed
        decision.temporal_liveness_passed = track.liveness_passed
        if not track.liveness_passed:
            decision.finalize()
            log_biometric_decision(status, decision)
            runtime_metrics.increment("recognition_unknown_total")
            people.append(RecognitionResult(
                UNKNOWN_NAME, "", box, False, False, "", track.track_id, decision
            ))
            continue

        recognition_started = time.perf_counter()
        match = match_identity_details(
            embedding, known["student_ids"], known["names"], known["parent_ids"],
            known["classes"], known["embeddings"],
            RECOGNITION_THRESHOLD, RECOGNITION_MARGIN,
        )
        runtime_metrics.observe(
            "recognition_latency_ms", (time.perf_counter() - recognition_started) * 1000.0
        )
        decision.recognition_score = match.get("score")
        decision.recognition_top2_score = match.get("top2_score")
        decision.recognition_margin = match.get("margin")
        decision.recognition_passed = bool(match["matched"])
        decision.identity_matched = bool(match["matched"])
        if not match["matched"]:
            decision.rejection_reason = match["reason"]
            decision.finalize()
            logger.debug(
                "recognition_failed camera_id=%s track_id=%s reason=%s score=%s margin=%s",
                status, track.track_id, match["reason"], match.get("score"), match.get("margin"),
            )
            log_biometric_decision(status, decision)
            runtime_metrics.increment("recognition_unknown_total")
            people.append(RecognitionResult(
                UNKNOWN_NAME, "", box, False, True, "", track.track_id, decision
            ))
            continue

        student_id = match["student_id"]
        name = match["name"]
        class_name = match["class_name"]
        if allowed_classes and class_name not in allowed_classes:
            continue
        track.recognized_student_id = student_id
        logger.debug(
            "recognition_passed camera_id=%s track_id=%s student_id=%s score=%.4f",
            status, track.track_id, student_id, decision.recognition_score,
        )
        decision.student_id = student_id
        decision.finalize()
        if decision.attendance_allowed:
            registered = attendance_service.register_camera_event(
                student_id, status, screenshot_frame=frame,
                confidence=decision.recognition_score,
            )
            if registered["status"] == "duplicate_suppressed":
                decision.attendance_allowed = False
                decision.rejection_reason = RejectionReason.DEBOUNCED.value
                logger.debug(
                    "attendance_rejected camera_id=%s track_id=%s reason=DEBOUNCED",
                    status, track.track_id,
                )
            else:
                logger.debug(
                    "attendance_allowed camera_id=%s track_id=%s student_id=%s",
                    status, track.track_id, student_id,
                )
        log_biometric_decision(status, decision)
        people.append(RecognitionResult(
            name, class_name, box, False, True, student_id, track.track_id, decision
        ))
    runtime_metrics.observe(
        "pipeline_latency_ms", (time.perf_counter() - pipeline_started) * 1000.0
    )
    return people


def camera_frames(status, camera_index, allowed_classes=None):
    """Subscribe to a worker's shared MJPEG buffer without doing recognition."""
    worker = get_camera_worker(status, camera_index)
    version = -1
    while True:
        version, jpeg = worker.wait_for_frame(version)
        if not worker.running:
            return
        if jpeg is None:
            continue
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"


def camera_statuses(camera_sources):
    return [
        get_camera_worker(status, source).public_status()
        for status, source in camera_sources.items()
    ]


def camera_health_statuses(camera_sources):
    """Read camera diagnostics without starting streams as a healthcheck side effect."""
    with workers_lock:
        current = dict(camera_workers)
    statuses = []
    for camera_id in camera_sources:
        worker = current.get(camera_id)
        if worker is not None:
            statuses.append(worker.public_status())
        else:
            statuses.append({
                "camera": camera_id,
                "camera_id": camera_id,
                "state": "disconnected",
                "status": CAMERA_OFFLINE,
                "last_frame_at": None,
                "last_error_at": None,
                "last_reconnect_at": None,
                "frames_read_total": 0,
                "frames_failed_total": 0,
                "reconnects_total": 0,
                "consecutive_failures": 0,
                "stale_total": 0,
                "reconnect_in_seconds": 0,
                "error": "worker is not running",
            })
    return statuses
