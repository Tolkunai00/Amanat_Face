"""API-only FastAPI application for web and Flutter clients."""

import os
import secrets
import threading
import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import router as api_router
from .config.security import get_session
from .config.settings import (
    ALLOWED_ORIGINS, AUTO_MIGRATE, CAMERA_INDEXES, DEBUG, FACE_DIR, LOG_LEVEL,
    SCREENSHOTS_DIR,
    WARM_UP_RECOGNITION,
    PRODUCTION,
    validate_runtime_settings,
)
from .db.migrations import init_db


from .observability.logging import configure_logging, request_id_context

configure_logging(LOG_LEVEL, PRODUCTION)
logger = logging.getLogger(__name__)


def warm_up_recognition_in_background() -> None:
    def worker():
        try:
            # Importing InsightFace itself is expensive on Windows, so both the
            # import and model initialization stay outside API startup.
            from .recognition.camera_service import get_fast_face_app
            get_fast_face_app()
        except Exception as exc:
            logger.exception("recognition warmup unavailable", extra={"event": "recognition_warmup_failed", "error_type": type(exc).__name__})

    timer = threading.Timer(2.0, worker)
    timer.daemon = True
    timer.name = "face-model-warmup"
    timer.start()


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_runtime_settings()
    if AUTO_MIGRATE:
        logger.warning("applying explicitly enabled development migrations", extra={"event": "development_auto_migrate"})
        init_db()
    os.makedirs(FACE_DIR, exist_ok=True)
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    from .repositories.students import delete_invalid_face_embeddings
    try:
        removed_embeddings = delete_invalid_face_embeddings()
        if removed_embeddings:
            logger.info("removed %s invalid face embeddings", removed_embeddings, extra={"event": "embedding_cleanup"})
    except Exception as exc:
        logger.exception("startup database maintenance unavailable", extra={"event": "database_maintenance_failed", "error_type": type(exc).__name__})
    from .config.settings import ANTI_SPOOFING_MODEL_PATH
    if ANTI_SPOOFING_MODEL_PATH:
        from .recognition.anti_spoof import get_anti_spoof_model
        get_anti_spoof_model()
        logger.info("anti-spoof model ready", extra={"event": "anti_spoof_ready"})
    from .recognition.fsvfm_pad import initialize_fsvfm_for_startup
    initialize_fsvfm_for_startup()
    from .recognition.camera_service import start_camera_workers, shutdown_camera_workers
    start_camera_workers(CAMERA_INDEXES)
    if WARM_UP_RECOGNITION:
        warm_up_recognition_in_background()
    from .services.event_photos import cleanup_scheduler

    cleanup_task = asyncio.create_task(cleanup_scheduler(), name="event-photo-cleanup")
    logger.info("API ready", extra={"event": "api_ready"})
    try:
        yield
    finally:
        shutdown_camera_workers()
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(
    title="Amanat School Face ID API",
    version="1.0.0",
    debug=DEBUG,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)
app.include_router(api_router)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", "")
    if not request_id or len(request_id) > 128 or not request_id.replace("-", "").isalnum():
        request_id = uuid.uuid4().hex
    token = request_id_context.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s %s", request.method, request.url.path, response.status_code,
            extra={"event": "http_request", "request_id": request_id,
                   "latency_ms": round((time.perf_counter() - started) * 1000, 3)},
        )
        return response
    except Exception as exc:
        logger.exception(
            "request failed", extra={"event": "http_request_failed", "request_id": request_id,
                                     "error_type": type(exc).__name__}
        )
        raise
    finally:
        request_id_context.reset(token)


@app.middleware("http")
async def admin_audit(request: Request, call_next):
    """Record successful administrative mutations without storing request secrets."""
    unsafe = request.method in {"POST", "PUT", "PATCH", "DELETE"}
    session = get_session(request) if unsafe else None
    response = await call_next(request)
    if (unsafe and session and session.get("role") == "admin"
            and request.url.path.startswith("/api/v1/") and response.status_code < 400):
        parts = [part for part in request.url.path.removeprefix("/api/v1/").split("/") if part]
        try:
            from .repositories.audit import record_admin_action
            record_admin_action(
                session.get("login") or "admin",
                request.method.lower(),
                parts[0] if parts else "api",
                parts[1] if len(parts) > 1 else None,
                {
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "client": request.client.host if request.client else None,
                },
            )
        except Exception as exc:
            logger.exception("audit write failed", extra={"event": "audit_write_failed", "error_type": type(exc).__name__})
    return response


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    unsafe = request.method in {"POST", "PUT", "PATCH", "DELETE"}
    is_api = request.url.path.startswith("/api/v1/")
    is_login = request.url.path == "/api/v1/auth/login"
    if unsafe and is_api and not is_login:
        session = get_session(request)
        if session is not None:
            supplied = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(supplied, expected):
                return JSONResponse(status_code=403, content={"detail": "Invalid CSRF token"})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/")
def root():
    return {"service": "school-face-id-api", "docs": "/docs", "health": "/api/v1/health"}
