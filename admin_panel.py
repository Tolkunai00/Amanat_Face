"""Backward-compatible server entry point.

The FastAPI application now lives in ``school_face_id.web.app``.
"""

from app.admin.admin_panel import app, run_server


if __name__ == "__main__":
    run_server()
