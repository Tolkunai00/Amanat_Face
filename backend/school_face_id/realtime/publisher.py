"""Sync-to-async realtime publisher, replaceable by Redis Pub/Sub later."""

import asyncio
import logging

from .manager import notification_connections


def _public_notification(notification):
    return {
        key: notification[key]
        for key in (
            "id", "notification_type", "message", "student", "event",
            "is_read", "read_at", "created_at",
        )
        if key in notification
    }


def publish_notification(notification):
    """Schedule targeted delivery after the repository transaction committed."""
    loop = notification_connections.loop
    if loop is None or loop.is_closed():
        return False
    payload = {"type": "notification.created", "data": _public_notification(notification)}
    coroutine = notification_connections.send_to_recipient(
        notification["recipient_type"], notification["recipient_id"], payload
    )
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    try:
        if running_loop is loop:
            loop.create_task(coroutine)
        else:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
            future.add_done_callback(_log_publish_failure)
    except RuntimeError:
        coroutine.close()
        return False
    return True


def _log_publish_failure(future):
    try:
        future.result()
    except Exception:
        logging.exception("Notification realtime publish task failed")
