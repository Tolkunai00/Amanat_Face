"""Structured production logging with conservative credential redaction."""

from __future__ import annotations

import contextvars
from datetime import datetime, timezone
import json
import logging
import re
from urllib.parse import urlsplit, urlunsplit


request_id_context = contextvars.ContextVar("request_id", default="")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(password|passwd|secret|token|authorization|cookie|session|api[_-]?key)"
    r"(\s*[=:]\s*)([^\s,;]+)"
)
_URL = re.compile(r"\b(?:rtsp|rtsps|postgresql(?:\+psycopg)?|https?)://[^\s]+", re.I)


def _redact_url(match: re.Match) -> str:
    raw = match.group(0)
    suffix = ""
    while raw and raw[-1] in ",;)]}":
        suffix = raw[-1] + suffix
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        if parsed.username is None and parsed.password is None:
            return raw + suffix
        host = parsed.hostname or ""
        if parsed.port:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme, f"***:***@{host}", parsed.path, parsed.query, parsed.fragment)) + suffix
    except ValueError:
        return "[REDACTED_URL]" + suffix


def redact(value) -> str:
    """Return log-safe text without mutating the original object."""
    text = str(value)
    text = _URL.sub(_redact_url, text)
    return _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)


class JsonFormatter(logging.Formatter):
    fields = ("request_id", "camera_id", "track_id", "student_id", "latency_ms", "error_type")

    def format(self, record: logging.LogRecord) -> str:
        error_type = getattr(record, "error_type", None)
        if not error_type and record.exc_info:
            error_type = record.exc_info[0].__name__
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "service": "amanat-face-api",
            "event": getattr(record, "event", record.name),
            "message": redact(record.getMessage()),
            "request_id": getattr(record, "request_id", None) or request_id_context.get() or None,
            "camera_id": getattr(record, "camera_id", None),
            "track_id": getattr(record, "track_id", None),
            "student_id": getattr(record, "student_id", None),
            "latency_ms": getattr(record, "latency_ms", None),
            "error_type": error_type,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str, production: bool) -> None:
    handler = logging.StreamHandler()
    if production:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level, logging.INFO))
