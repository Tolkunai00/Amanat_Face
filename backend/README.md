# Backend

API-only FastAPI backend. It owns authentication, PostgreSQL, attendance rules,
cameras, face recognition and protected media access.

## Local run

From the repository root:

```powershell
python backend\run.py
```

API documentation: `http://127.0.0.1:8080/docs`.
Versioned client contract: `/api/v1`.

## Camera connection (USB or IP/RTSP)

Administrators can configure the entrance and exit IP cameras on the Camera page.
The separate Classroom page supports multiple named cameras with independent
settings and deletion. Existing single-classroom settings remain compatible.
Its grid refreshes snapshots once per second; Open video starts a continuous
stream for the selected camera. Classroom cameras never run face recognition
or create entrance/exit attendance events.
Enter the IP, RTSP port, username, password and stream path, then test the video
and save. For the configured Cootli model the stream path is `/V_ENC_000`.
An empty password field preserves the existing password. Saved settings override
the environment camera sources and take effect without a backend restart.
They persist in the private `data/camera-settings.json` file; preserve this data
directory across container recreation. Camera passwords are never returned by
the configuration API. The backend must have network access to the cameras.

As an alternative, configure entrance and exit cameras through the private runtime
environment. The application never returns camera passwords through its API or
frontend. Use a numeric source for a local USB camera:

```dotenv
CAMERA_KELDI_SOURCE=0
CAMERA_KETTI_SOURCE=1
```

For IP cameras, use the complete RTSP URL. For a local PowerShell run, set the
variables in the same terminal before starting the backend:

```powershell
$env:CAMERA_KELDI_SOURCE = 'rtsp://camera_user:URL_ENCODED_PASSWORD@192.168.1.100:554/Streaming/Channels/101'
$env:CAMERA_KETTI_SOURCE = 'rtsp://camera_user:URL_ENCODED_PASSWORD@192.168.1.101:554/Streaming/Channels/101'
python backend\run.py
```

For Docker, put the same values (without `$env:` and quotes) in the untracked
`.env.docker` file used by `docker compose --env-file .env.docker ...`.

The exact port and stream path depend on the camera vendor. If a login or
password contains `@`, `:`, `/`, `#`, `%` or spaces, URL-encode it before
placing it in the URL. Do not commit the real IP, login or password. Restart the
backend after changing camera sources. The UI deliberately shows only the
video feed and never exposes connection credentials. Docker Desktop on Windows
normally cannot access a host USB camera directly, so RTSP is preferred there.

## Development container stack

From the repository root:

```powershell
Copy-Item .env.docker.example .env.docker
# Edit both passwords and the public ALLOWED_ORIGINS value.
docker compose --env-file .env.docker up -d --build
```

FastAPI is published at `http://127.0.0.1:8080`. PostgreSQL is reachable only
on the private Compose network. The bind-mounted backend source and Uvicorn
`--reload` make Python changes available without rebuilding the image. Run the
Vite frontend separately with `npm run dev` from `frontend/`; it proxies
`/api/` to FastAPI.

## Schema migrations

Application startup applies pending revisions to PostgreSQL. `DATABASE_URL` is
required and the application never falls back to a local SQLite database. Make
a PostgreSQL backup before every migration. Historical SQLite migration fixtures
are retained only inside isolated tests and are never used for runtime data.

## Managed accounts and classes

Teacher and parent passwords must contain at least 12 characters, and their
logins share one case-insensitive namespace. Teacher profile/class updates and
password changes are separate operations; both revoke existing teacher
sessions. Account deletion endpoints block the account and preserve its
history. Use the corresponding `PUT /api/v1/{teachers|parents}/{id}/status`
endpoint to reactivate it.

Classes are managed independently through `/api/v1/classes`. Teacher
assignments reference stable class and teacher IDs, so either login or class
name can be changed safely. Successful administrator mutations are recorded
without request bodies or passwords and can be inspected through
`GET /api/v1/audit`.

## Liveness limitation

The camera requires several frames and a small head-pose change before recording
attendance. This blocks the previous single-image shortcut, but passive liveness
is not equivalent to a dedicated anti-spoofing model or depth/IR camera.
Production startup is blocked until `ANTI_SPOOFING_MODEL_PATH` is set and
`FACE_MODEL_LICENSE=commercial` explicitly records that the deployed weights
have a production license. The default `buffalo_l` weights are evaluation-only.

After every student has 3-5 independent enrollment photos, evaluate a candidate
recognition threshold against the PostgreSQL gallery:

```powershell
python backend\scripts\evaluate_recognition_threshold.py --target-far 0.001
```

The reported threshold is only a starting point. Confirm it with separate
entrance-camera captures of real students before changing `RECOGNITION_THRESHOLD`.

## Realtime notification limitation

Notification history remains in PostgreSQL, while active WebSocket connections
are intentionally process-local. Run the backend with one Uvicorn worker for
realtime delivery. A future multi-worker deployment must replace the internals
of `school_face_id.realtime.publisher` with Redis Pub/Sub (or an equivalent
broker); Notification Service and the WebSocket payload contract can remain
unchanged. Clients always recover missed messages through the HTTP notification
API after reconnect.

## Attendance screenshot retention

Attendance event screenshots are temporary evidence. `SCREENSHOT_RETENTION_DAYS`
controls their lifetime and defaults to `7`; setting it to `0` disables creation
of new event screenshots while attendance events and notifications continue to
work. `SCREENSHOT_CLEANUP_INTERVAL_MINUTES` controls the process-local cleanup
task and defaults to `60`.

Cleanup deletes only files safely resolved inside `SCREENSHOTS_DIR`, then clears
the event's photo metadata. Attendance history remains persistent. Enrollment
photos and embeddings under `face_database/` are never part of this policy.
The Alembic migration only adds/backfills expiration metadata and never deletes
physical files. Manual inspection or cleanup is available with:

```powershell
python backend\scripts\cleanup_event_photos.py --dry-run
python backend\scripts\cleanup_event_photos.py
```

Like realtime delivery, the scheduler is intentionally process-local. Cleanup
is idempotent, but a future multi-worker deployment may use a distributed lock
to avoid duplicate work.
