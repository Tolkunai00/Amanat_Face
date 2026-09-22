"""Backward-compatible import for face recognition services.

New code should import from ``school_face_id.recognition.camera_service``.
"""

from app.services.camera_service import *  # noqa: F401,F403
