"""Backward-compatible import for the application settings.

New code should import from ``school_face_id.config.settings``.
"""

from app.core.settings import *  # noqa: F401,F403
