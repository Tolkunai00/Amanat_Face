"""Private persistent camera configuration for the single-worker API."""

import ipaddress
import json
import os
import re
import uuid
import threading
from urllib.parse import quote, unquote, urlsplit

from pydantic import BaseModel, Field, field_validator

from ..config.settings import CAMERA_INDEXES, DATA_DIR

SETTINGS_PATH = DATA_DIR / "camera-settings.json"
settings_lock = threading.RLock()
camera_names = {}


def is_classroom(direction):
    return direction == "classroom" or bool(re.fullmatch(r"classroom_[a-f0-9]{32}", direction))


class CameraConfig(BaseModel):
    name: str = Field(default="", max_length=100)
    host: str
    port: int = Field(default=554, ge=1, le=65535)
    username: str = Field(default="admin", max_length=128)
    password: str = Field(default="", max_length=512, repr=False)
    path: str = Field(default="/V_ENC_000", max_length=1024)

    @field_validator("host")
    @classmethod
    def valid_host(cls, value):
        return str(ipaddress.ip_address(value.strip()))

    @field_validator("path")
    @classmethod
    def valid_path(cls, value):
        if not value.startswith("/") or any(c.isspace() for c in value) or "#" in value:
            raise ValueError("Stream path must start with / and contain no spaces or #")
        return value


def load_camera_settings():
    with settings_lock:
        if SETTINGS_PATH.exists():
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            for direction, source in saved.items():
                if direction in ("keldi", "ketti") or is_classroom(direction):
                    CAMERA_INDEXES[direction] = source
            camera_names.update(saved.get("__names__", {}))


def persist(sources, names):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({**sources, "__names__": names}, handle)
    os.replace(temporary, SETTINGS_PATH)


def public_config(direction):
    source = CAMERA_INDEXES.get(direction, "")
    parsed = urlsplit(source) if isinstance(source, str) else urlsplit("")
    return {
        "direction": direction,
        "name": camera_names.get(direction, "Аудитория" if is_classroom(direction) else ""),
        "host": parsed.hostname or "",
        "port": parsed.port or 554,
        "username": unquote(parsed.username or ""),
        "path": parsed.path + ("?" + parsed.query if parsed.query else "") if parsed.hostname else "/V_ENC_000",
        "has_password": bool(parsed.password),
    }


def build_source(direction, config):
    password = config.password
    if not password:
        old = CAMERA_INDEXES.get(direction, "")
        password = unquote(urlsplit(old).password or "") if isinstance(old, str) else ""
    host = f"[{config.host}]" if ":" in config.host else config.host
    credentials = f"{quote(config.username, safe='')}:{quote(password, safe='')}@" if config.username else ""
    return f"rtsp://{credentials}{host}:{config.port}{config.path}"


def save_config(direction, config):
    from ..recognition.camera_service import get_camera_worker

    with settings_lock:
        source = build_source(direction, config)
        saved = dict(CAMERA_INDEXES)
        saved[direction] = source
        names = dict(camera_names)
        if is_classroom(direction):
            names[direction] = config.name.strip() or "Аудитория"
        persist(saved, names)
        camera_names.update(names)
        CAMERA_INDEXES[direction] = source
        get_camera_worker(direction, source)
        return public_config(direction)


def create_classroom(config):
    with settings_lock:
        return save_config("classroom_" + uuid.uuid4().hex, config)


def delete_classroom(direction):
    from ..recognition.camera_service import camera_workers, workers_lock

    with settings_lock:
        if not is_classroom(direction) or direction not in CAMERA_INDEXES:
            raise KeyError(direction)
        saved = dict(CAMERA_INDEXES)
        names = dict(camera_names)
        saved.pop(direction)
        names.pop(direction, None)
        # Persist a blank legacy slot to override a legacy environment source.
        if direction == "classroom":
            saved[direction] = ""
        persist(saved, names)
        CAMERA_INDEXES.pop(direction)
        if direction == "classroom":
            CAMERA_INDEXES[direction] = ""
        camera_names.pop(direction, None)
        with workers_lock:
            worker = camera_workers.pop(direction, None)
        if worker is not None:
            worker.stop()


def test_connection(direction, config):
    import cv2

    source = build_source(direction, config)
    capture = cv2.VideoCapture(source, cv2.CAP_FFMPEG, [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
        cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000,
    ])
    try:
        ok, frame = capture.read() if capture.isOpened() else (False, None)
        return {"connected": bool(ok and frame is not None)}
    finally:
        capture.release()
