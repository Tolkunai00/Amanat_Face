import os
from pathlib import Path
from urllib.parse import quote, urlsplit

from .model_artifacts import ModelArtifactError, is_sha256, validate_model_artifact


class ProductionConfigError(RuntimeError):
    """Raised when production configuration is unsafe or incomplete."""


def env_or_file(name, default=""):
    """Read a secret from NAME_FILE when present, otherwise from NAME."""
    file_name = os.getenv(f"{name}_FILE", "").strip()
    if file_name:
        try:
            return Path(file_name).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"Cannot read {name}_FILE: {exc}") from exc
    return os.getenv(name, default).strip()


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ProductionConfigError(f"{name} must be an integer, got {value!r}") from exc


def env_nonnegative_int(name, default):
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {value!r}") from exc
    if parsed < 0:
        raise RuntimeError(f"{name} must be zero or greater, got {parsed}")
    return parsed


def env_positive_int(name, default):
    parsed = env_nonnegative_int(name, default)
    if parsed == 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return parsed


def env_camera_source(name, default):
    """Return a numeric local-device index or an RTSP/HTTP source string."""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    value = value.strip()
    try:
        index = int(value)
    except ValueError:
        return value
    if index < 0:
        raise RuntimeError(f"{name} camera index must be zero or greater")
    return index


def env_float(name, default):
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ProductionConfigError(f"{name} must be numeric, got {value!r}") from exc


BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
TMP_DIR = PROJECT_ROOT / "tmp"

FACE_DIR = str(PROJECT_ROOT / os.getenv("FACE_DIR", "face_database"))
SCREENSHOTS_DIR = str(PROJECT_ROOT / os.getenv("SCREENSHOTS_DIR", "screenshots"))
BACKUP_STATUS_FILE = os.getenv("BACKUP_STATUS_FILE", "").strip()
OFFSITE_BACKUP_STATUS_FILE = os.getenv("OFFSITE_BACKUP_STATUS_FILE", "").strip()
SCREENSHOT_RETENTION_DAYS = env_nonnegative_int("SCREENSHOT_RETENTION_DAYS", 7)
SCREENSHOT_CLEANUP_INTERVAL_MINUTES = env_positive_int(
    "SCREENSHOT_CLEANUP_INTERVAL_MINUTES", 60
)
DB_NAME = str(DATA_DIR / os.getenv("DB_NAME", "school.db"))
def database_url_from_environment():
    """Build the production URL without duplicating the database password."""
    explicit = env_or_file("DATABASE_URL")
    if explicit:
        return explicit
    password_file = os.getenv("POSTGRES_PASSWORD_FILE", "").strip()
    if not password_file:
        return ""
    try:
        password = Path(password_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Cannot read POSTGRES_PASSWORD_FILE: {exc}") from exc
    user = quote(os.getenv("POSTGRES_USER", "amanat").strip(), safe="")
    database = quote(os.getenv("POSTGRES_DB", "amanat").strip(), safe="")
    host = os.getenv("POSTGRES_HOST", "db").strip()
    port = env_int("POSTGRES_PORT", 5432)
    return f"postgresql+psycopg://{user}:{quote(password, safe='')}@{host}:{port}/{database}"


DATABASE_URL = database_url_from_environment()
SQLITE_TEST_MODE = env_bool("SQLITE_TEST_MODE", False)

APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = env_int("APP_PORT", 8080)
APP_AUTO_PORT = env_bool("APP_AUTO_PORT", True)
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
PRODUCTION = ENVIRONMENT == "production" or env_bool("PRODUCTION", False)
DEBUG = env_bool("DEBUG", False)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
AUTO_MIGRATE = env_bool("AUTO_MIGRATE", False)
ADMIN_PASSWORD = env_or_file("ADMIN_PASSWORD")
SESSION_SECRET = env_or_file("SESSION_SECRET")
RELEASE_VERSION = os.getenv("RELEASE_VERSION", "development").strip()
DISK_WARNING_PERCENT = env_int("DISK_WARNING_PERCENT", 10)
DISK_CRITICAL_PERCENT = env_int("DISK_CRITICAL_PERCENT", 5)

MAX_UPLOAD_BYTES = env_int("MAX_UPLOAD_BYTES", 5 * 1024 * 1024)
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        os.getenv(
            "ALLOWED_ORIGINS",
            "http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173,http://localhost:5173",
        ),
    ).split(",")
    if origin.strip()
]

UNKNOWN_NAME = "not_in_database"

# RECOGNITION_WIDTH = env_int("RECOGNITION_WIDTH", 640)
#
# RECOGNITION_INTERVAL_SECONDS = env_float("RECOGNITION_INTERVAL_SECONDS", 0.02)

RECOGNITION_THRESHOLD = env_float("RECOGNITION_THRESHOLD", 0.52)
RECOGNITION_MARGIN = env_float("RECOGNITION_MARGIN", 0.06)
FACE_MODEL_NAME = os.getenv("FACE_MODEL_NAME", "buffalo_l")
FACE_MODEL_LICENSE = os.getenv("FACE_MODEL_LICENSE", "non-commercial").strip().lower()
ANTI_SPOOFING_MODEL_PATH = os.getenv("ANTI_SPOOFING_MODEL_PATH", "").strip()
ANTI_SPOOFING_MODEL_TYPE = os.getenv("ANTI_SPOOFING_MODEL_TYPE", "generic").strip().lower()
ANTI_SPOOFING_MODEL_SHA256 = os.getenv("ANTI_SPOOFING_MODEL_SHA256", "").strip().lower()
ANTI_SPOOFING_THRESHOLD = env_float("ANTI_SPOOFING_THRESHOLD", 0.80)
ANTI_SPOOFING_LIVE_INDEX = env_int("ANTI_SPOOFING_LIVE_INDEX", 1)
ANTI_SPOOFING_OUTPUT = os.getenv("ANTI_SPOOFING_OUTPUT", "logits").strip().lower()
ANTI_SPOOFING_CLASS_NAMES = tuple(
    name.strip()
    for name in os.getenv("ANTI_SPOOFING_CLASS_NAMES", "print,live,replay").split(",")
    if name.strip()
)
ANTI_SPOOFING_CACHE_TTL_SECONDS = env_float("ANTI_SPOOFING_CACHE_TTL_SECONDS", 2.0)
FSVFM_ENABLED = env_bool("FSVFM_ENABLED", True)
FSVFM_SHADOW_MODE = env_bool("FSVFM_SHADOW_MODE", True)
FSVFM_CHECKPOINT = os.getenv("FSVFM_CHECKPOINT", "").strip()
FSVFM_CHECKPOINT_SHA256 = os.getenv("FSVFM_CHECKPOINT_SHA256", "").strip().lower()
FSVFM_THRESHOLD = env_float("FSVFM_THRESHOLD", 0.50)

FACE_DETECTION_PROFILES = (
    ("fast", 320),
    ("accurate", 480),
    ("fallback", 640),
)
FACE_DETECTION_FALLBACK_ENABLED = env_bool("FACE_DETECTION_FALLBACK_ENABLED", True)
FACE_DETECTION_MIN_CONFIDENCE = env_float("FACE_DETECTION_MIN_CONFIDENCE", 0.50)
FACE_TRACK_MAX_MISSED_FRAMES = env_int("FACE_TRACK_MAX_MISSED_FRAMES", 8)
FACE_TRACK_TTL_SECONDS = env_float("FACE_TRACK_TTL_SECONDS", 3.0)
# ... жогорудагы коддор ...
RECOGNITION_WIDTH = env_int("RECOGNITION_WIDTH", 640)
RECOGNITION_INTERVAL_SECONDS = env_float("RECOGNITION_INTERVAL_SECONDS", 0.1)
WARM_UP_RECOGNITION = env_bool("WARM_UP_RECOGNITION", False)
# ...
# ... төмөнкү коддор ...
LIVENESS_REQUIRED_SAMPLES = env_int("LIVENESS_REQUIRED_SAMPLES", 4)
LIVENESS_WINDOW_SECONDS = env_float("LIVENESS_WINDOW_SECONDS", 3.0)
LIVENESS_MAX_FACE_MOTION = env_float("LIVENESS_MAX_FACE_MOTION", 0.22)
LIVENESS_MIN_POSE_CHANGE = env_float("LIVENESS_MIN_POSE_CHANGE", 0.018)
LIVENESS_MIN_FACE_RATIO = 0.008
LIVENESS_MIN_TEXTURE_SCORE = 6.0
ATTENDANCE_DEBOUNCE_SECONDS = env_int("ATTENDANCE_DEBOUNCE_SECONDS", 60)
ATTENDANCE_TIMEZONE = os.getenv("ATTENDANCE_TIMEZONE", "Asia/Bishkek").strip()
LOW_LIGHT_MEAN_THRESHOLD = env_int("LOW_LIGHT_MEAN_THRESHOLD", 115)
LOW_LIGHT_GAMMA = env_float("LOW_LIGHT_GAMMA", 0.58)
CAMERA_FPS = env_int("CAMERA_FPS", 25)
CAMERA_READ_SLEEP_SECONDS = env_float("CAMERA_READ_SLEEP_SECONDS", 0.0)
CAMERA_READ_MAX_SECONDS = env_float("CAMERA_READ_MAX_SECONDS", 7.0)
CAMERA_OFFLINE_TIMEOUT_SECONDS = env_float("CAMERA_OFFLINE_TIMEOUT_SECONDS", 30.0)
CAMERA_STALE_ENABLED = env_bool("CAMERA_STALE_ENABLED", True)
CAMERA_STALE_TIMEOUT_SECONDS = env_float("CAMERA_STALE_TIMEOUT_SECONDS", 10.0)
CAMERA_STALE_CONSECUTIVE_FRAMES = env_positive_int("CAMERA_STALE_CONSECUTIVE_FRAMES", 30)
CAMERA_STREAM_WIDTH = env_int("CAMERA_STREAM_WIDTH", 1280)
JPEG_QUALITY = env_int("JPEG_QUALITY", 78)
FACE_MIN_BLUR_SCORE = env_float("FACE_MIN_BLUR_SCORE", 60.0)
FACE_MIN_BRIGHTNESS = env_float("FACE_MIN_BRIGHTNESS", 45.0)
FACE_MAX_BRIGHTNESS = env_float("FACE_MAX_BRIGHTNESS", 220.0)
FACE_MIN_ENROLLMENT_SAMPLES = env_int("FACE_MIN_ENROLLMENT_SAMPLES", 3)
FACE_MAX_ENROLLMENT_SAMPLES = env_int("FACE_MAX_ENROLLMENT_SAMPLES", 5)

CAMERA_INDEXES = {
    "classroom": env_camera_source("CAMERA_CLASSROOM_SOURCE", ""),
    "keldi": env_camera_source("CAMERA_KELDI_SOURCE", ""),
    # Both directions accept either a local device index (0, 1, ...) or a
    # complete RTSP URL kept in the private runtime environment.
    "ketti": env_camera_source("CAMERA_KETTI_SOURCE", 0),
}


_INSECURE_SECRET_VALUES = {
    "admin2026",
    "mektep2026",
    "change-this-admin-password",
    "change-this-teacher-password",
    "change_me_different_admin_password_32",
    "change_this_admin_password_123456",
    "password",
}


def _valid_production_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.path.rstrip("/")
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _placeholder_secret(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        normalized in _INSECURE_SECRET_VALUES
        or normalized.startswith(("change_me", "change-this", "change_this", "replace_"))
        or "placeholder" in normalized
    )


def _valid_database_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in {"postgresql", "postgresql+psycopg"}
            and bool(parsed.hostname)
            and bool(parsed.username)
            and bool(parsed.password)
            and not _placeholder_secret(parsed.password)
        )
    except ValueError:
        return False


def validate_runtime_settings(check_model_files=True):
    """Reject unsafe production configuration before the API starts."""
    if SCREENSHOT_RETENTION_DAYS < 0:
        raise ProductionConfigError("SCREENSHOT_RETENTION_DAYS must be zero or greater")
    if SCREENSHOT_CLEANUP_INTERVAL_MINUTES <= 0:
        raise ProductionConfigError("SCREENSHOT_CLEANUP_INTERVAL_MINUTES must be greater than zero")
    if ANTI_SPOOFING_CACHE_TTL_SECONDS <= 0:
        raise ProductionConfigError("ANTI_SPOOFING_CACHE_TTL_SECONDS must be greater than zero")
    if not 0 < DISK_CRITICAL_PERCENT < DISK_WARNING_PERCENT < 100:
        raise ProductionConfigError(
            "disk thresholds must satisfy 0 < DISK_CRITICAL_PERCENT < DISK_WARNING_PERCENT < 100"
        )
    for name, value in (
        ("CAMERA_READ_MAX_SECONDS", CAMERA_READ_MAX_SECONDS),
        ("CAMERA_OFFLINE_TIMEOUT_SECONDS", CAMERA_OFFLINE_TIMEOUT_SECONDS),
        ("CAMERA_STALE_TIMEOUT_SECONDS", CAMERA_STALE_TIMEOUT_SECONDS),
    ):
        if value <= 0:
            raise ProductionConfigError(f"{name} must be greater than zero")
    if ANTI_SPOOFING_MODEL_SHA256 and not is_sha256(ANTI_SPOOFING_MODEL_SHA256):
        raise ProductionConfigError("ANTI_SPOOFING_MODEL_SHA256 must be a 64-character hexadecimal digest")
    if FSVFM_CHECKPOINT_SHA256 and not is_sha256(FSVFM_CHECKPOINT_SHA256):
        raise ProductionConfigError("FSVFM_CHECKPOINT_SHA256 must be a 64-character hexadecimal digest")
    if not 0 <= FSVFM_THRESHOLD <= 1:
        raise ProductionConfigError("FSVFM_THRESHOLD must be between zero and one")
    for name, value in (
        ("RECOGNITION_THRESHOLD", RECOGNITION_THRESHOLD),
        ("RECOGNITION_MARGIN", RECOGNITION_MARGIN),
        ("ANTI_SPOOFING_THRESHOLD", ANTI_SPOOFING_THRESHOLD),
        ("FACE_DETECTION_MIN_CONFIDENCE", FACE_DETECTION_MIN_CONFIDENCE),
    ):
        if not 0 <= value <= 1:
            raise ProductionConfigError(f"{name} must be between zero and one")
    if FSVFM_ENABLED and not FSVFM_SHADOW_MODE:
        if not FSVFM_CHECKPOINT:
            raise ProductionConfigError("FSVFM_CHECKPOINT is required when FS-VFM enforcement is enabled")
        if not FSVFM_CHECKPOINT_SHA256:
            raise ProductionConfigError("FSVFM_CHECKPOINT_SHA256 is required when FS-VFM enforcement is enabled")
    if not PRODUCTION:
        return

    errors = []
    for secret_name in ("ADMIN_PASSWORD", "SESSION_SECRET"):
        if os.getenv(secret_name) and os.getenv(f"{secret_name}_FILE"):
            errors.append(f"{secret_name} must have exactly one source; use the Docker secret file")
    if not ADMIN_PASSWORD or len(ADMIN_PASSWORD) < 12 or _placeholder_secret(ADMIN_PASSWORD):
        errors.append("ADMIN_PASSWORD must be a unique value with at least 12 characters")
    if not SESSION_SECRET or len(SESSION_SECRET) < 32 or _placeholder_secret(SESSION_SECRET):
        errors.append("SESSION_SECRET must be a unique value with at least 32 characters")
    if not ALLOWED_ORIGINS or any(not _valid_production_origin(origin) for origin in ALLOWED_ORIGINS):
        errors.append("CORS_ALLOWED_ORIGINS must contain explicit HTTPS frontend origins")
    if DEBUG:
        errors.append("DEBUG must be false in production")
    if AUTO_MIGRATE:
        errors.append("AUTO_MIGRATE must be false in production")
    if not _valid_database_url(DATABASE_URL):
        errors.append("DATABASE_URL must be a complete PostgreSQL URL with non-placeholder credentials")
    if LOG_LEVEL not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        errors.append("LOG_LEVEL must be CRITICAL, ERROR, WARNING, INFO, or DEBUG")
    if FACE_MODEL_LICENSE != "commercial":
        errors.append(
            "FACE_MODEL_LICENSE must be commercial; bundled InsightFace pretrained "
            "models are not licensed for production"
        )
    if not ANTI_SPOOFING_MODEL_PATH:
        errors.append(
            "ANTI_SPOOFING_MODEL_PATH is required in production; temporal liveness "
            "alone does not protect against photo/video presentation attacks"
        )
    if not ANTI_SPOOFING_MODEL_SHA256:
        errors.append("ANTI_SPOOFING_MODEL_SHA256 is required in production")

    for status, setting_name in (
        ("keldi", "CAMERA_KELDI_SOURCE"), ("ketti", "CAMERA_KETTI_SOURCE")
    ):
        camera_source = str(CAMERA_INDEXES.get(status, ""))
        if not camera_source:
            errors.append(f"{setting_name} is required in production")
            continue
        parsed_camera = urlsplit(camera_source) if "://" in camera_source else None
        placeholder_credentials = parsed_camera and (
            _placeholder_secret(parsed_camera.password or "")
            or (parsed_camera.username or "").lower() == "admin"
            and (parsed_camera.password or "").lower() == "admin"
        )
        if placeholder_credentials or "change-password" in camera_source:
            errors.append(f"{setting_name} contains placeholder/default credentials")

    if errors:
        raise ProductionConfigError("Unsafe production configuration: " + "; ".join(errors))

    if check_model_files:
        try:
            validate_model_artifact(
                ANTI_SPOOFING_MODEL_PATH,
                ANTI_SPOOFING_MODEL_SHA256,
                "ANTI_SPOOFING_MODEL_PATH",
            )
            if FSVFM_ENABLED and not FSVFM_SHADOW_MODE:
                validate_model_artifact(
                    FSVFM_CHECKPOINT,
                    FSVFM_CHECKPOINT_SHA256,
                    "FSVFM_CHECKPOINT",
                )
        except ModelArtifactError as exc:
            raise ProductionConfigError(str(exc)) from exc
