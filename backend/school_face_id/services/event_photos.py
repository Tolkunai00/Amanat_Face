"""Safe lifecycle management for temporary attendance event screenshots."""

import asyncio
from datetime import timedelta
import logging
from pathlib import Path
import re

import cv2

from ..config.settings import (
    JPEG_QUALITY,
    PROJECT_ROOT,
    SCREENSHOT_CLEANUP_INTERVAL_MINUTES,
    SCREENSHOT_RETENTION_DAYS,
    SCREENSHOTS_DIR,
)
from ..repositories import attendance as attendance_repository
from .attendance import local_now, normalize_occurred_at


EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class EventPhotoError(RuntimeError):
    pass


def _root():
    return Path(SCREENSHOTS_DIR).resolve()


def _is_within(path, root):
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def resolve_event_photo_path(stored_path):
    """Resolve new and legacy relative paths, never outside SCREENSHOTS_DIR."""
    if not stored_path:
        raise EventPhotoError("Event photo path is empty")
    raw = Path(str(stored_path))
    if raw.is_absolute():
        raise EventPhotoError("Absolute event photo paths are not allowed")
    root = _root()
    candidates = [(root / raw).resolve(), (Path(PROJECT_ROOT).resolve() / raw).resolve()]
    safe_candidates = [candidate for candidate in candidates if _is_within(candidate, root)]
    for candidate in safe_candidates:
        if candidate.exists():
            return candidate
    if safe_candidates:
        return safe_candidates[0]
    raise EventPhotoError("Event photo path escapes SCREENSHOTS_DIR")


def event_photo_filename(event_id):
    value = str(event_id)
    if not EVENT_ID_PATTERN.fullmatch(value):
        raise EventPhotoError("Invalid attendance event ID for photo filename")
    return f"{value}.jpg"


def photo_expiration(occurred_at, retention_days=None):
    days = SCREENSHOT_RETENTION_DAYS if retention_days is None else retention_days
    if days < 0:
        raise EventPhotoError("Screenshot retention cannot be negative")
    return (normalize_occurred_at(occurred_at) + timedelta(days=days)).isoformat(
        timespec="seconds"
    )


def save_event_screenshot(event_id, frame, occurred_at, retention_days=None):
    """Atomically write a JPEG then attach its relative metadata to the event."""
    days = SCREENSHOT_RETENTION_DAYS if retention_days is None else retention_days
    if days == 0:
        return None
    if days < 0:
        raise EventPhotoError("Screenshot retention cannot be negative")
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    filename = event_photo_filename(event_id)
    final_path = resolve_event_photo_path(filename)
    temporary_path = final_path.with_suffix(".tmp.jpg")
    try:
        written = cv2.imwrite(
            str(temporary_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
        )
        if not written:
            raise EventPhotoError("Cannot encode attendance event screenshot")
        temporary_path.replace(final_path)
        expires_at = photo_expiration(occurred_at, days)
        if not attendance_repository.set_event_photo(event_id, filename, expires_at):
            final_path.unlink(missing_ok=True)
            raise EventPhotoError("Attendance event disappeared while attaching its photo")
        return attendance_repository.get_event_by_id(event_id)
    finally:
        temporary_path.unlink(missing_ok=True)


def attach_existing_event_photo(event_id, stored_path, occurred_at, retention_days=None):
    """Attach an already-created safe test/import file without moving it."""
    days = SCREENSHOT_RETENTION_DAYS if retention_days is None else retention_days
    if days == 0:
        return None
    path = resolve_event_photo_path(stored_path)
    if not path.is_file():
        raise EventPhotoError("Attendance event screenshot does not exist")
    relative = path.relative_to(_root()).as_posix()
    attendance_repository.set_event_photo(
        event_id, relative, photo_expiration(occurred_at, days)
    )
    return attendance_repository.get_event_by_id(event_id)


def readable_event_photo(event):
    if not event.get("photo_path"):
        raise EventPhotoError("Event photo not found")
    expires_at = event.get("photo_expires_at")
    if expires_at and normalize_occurred_at(expires_at) <= local_now():
        raise EventPhotoError("Event photo has expired")
    path = resolve_event_photo_path(event["photo_path"])
    if not path.is_file():
        raise EventPhotoError("Event photo not found")
    return path


def cleanup_expired_event_photos(*, dry_run=False, now=None):
    now_value = normalize_occurred_at(now).isoformat(timespec="seconds")
    rows = attendance_repository.list_expired_event_photos(now_value)
    stats = {"scanned": len(rows), "deleted": 0, "missing": 0, "failed": 0}
    if dry_run:
        stats["would_delete"] = len(rows)
        return stats
    for row in rows:
        try:
            path = resolve_event_photo_path(row["photo_path"])
            if path.exists():
                path.unlink()
                status = "deleted"
            else:
                status = "missing"
            if attendance_repository.clear_event_photo(row["id"], row["photo_path"]):
                stats[status] += 1
        except Exception:
            stats["failed"] += 1
            logging.exception(
                "Event photo cleanup failed for event %s path %s",
                row["id"], row["photo_path"],
            )
    return stats


def find_orphan_event_photos():
    """Return diagnostics only; unknown files are never deleted automatically."""
    root = _root()
    if not root.exists():
        return []
    referenced = set()
    for row in attendance_repository.list_event_photo_references():
        try:
            referenced.add(resolve_event_photo_path(row["photo_path"]))
        except EventPhotoError:
            continue
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.jpg")
        if path.is_file() and path.resolve() not in referenced
    )


async def cleanup_scheduler():
    interval = SCREENSHOT_CLEANUP_INTERVAL_MINUTES * 60
    while True:
        await asyncio.sleep(interval)
        try:
            stats = await asyncio.to_thread(cleanup_expired_event_photos)
            if stats["scanned"]:
                logging.info("Event photo cleanup completed: %s", stats)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Event photo cleanup iteration failed")
