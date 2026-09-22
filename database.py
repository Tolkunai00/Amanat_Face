"""Backward-compatible import for the database layer.

New code should import from ``school_face_id.data.database``.
"""

from app.database.database import *  # noqa: F401,F403
