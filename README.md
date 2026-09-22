# School Face ID

Backend and web interface for a school attendance system with face
recognition.

## Start

The original launch commands are preserved:

```powershell
python admin_panel.py
```

or:

```powershell
.\start_server.bat
```

The main implementation is organized under `app/`. Root Python files and the
old `school_face_id/` package are compatibility entry points, so existing
commands and imports continue to work.

## Project structure

```text
fice_amanat/
├── app/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   └── settings.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── database.py
│   │   └── face_database.py
│   ├── services/
│   │   ├── __init__.py
│   │   └── camera_service.py
│   └── admin/
│       ├── __init__.py
│       └── admin_panel.py
│
├── frontend/
│   ├── __init__.py
│   ├── views.py
│   ├── template_loader.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── camera.html
│   │   └── error.html
│   └── static/
│       ├── styles.css
│       └── camera.css
│
├── data/
│   └── school.db
├── face_database/
├── screenshots/
├── tmp/
├── logs/
│   ├── server-runtime.log
│   ├── server-runtime-error.log
│   └── server.err
├── scripts/
│   └── start_server.bat
│
├── school_face_id/          # Compatibility package for old imports
├── admin_panel.py           # Compatibility server entry point
├── camera_service.py        # Compatibility import
├── database.py              # Compatibility import
├── fase_database.py         # Compatibility import/entry point
├── settings.py              # Compatibility import
├── views.py                 # Compatibility import
├── start_server.bat         # Compatibility Windows launcher
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

The service folders `.agents/` and `.idea/` remain at the project root because
they belong to development tools, not to the Python application.

## Responsibilities

- `app/core/settings.py` — environment configuration and absolute project
  paths.
- `app/database/database.py` — SQLite tables, queries and attendance rules.
- `app/database/face_database.py` — one-time photo import utility.
- `app/services/camera_service.py` — cameras, InsightFace, recognition and
  liveness checks.
- `app/admin/admin_panel.py` — FastAPI application, routes and authorization.
- `frontend/views.py` — dynamic server-rendered HTML.
- `frontend/templates/` — standalone HTML templates.
- `frontend/static/` — CSS files.
- `data/school.db` — persistent SQLite data.
- `face_database/` — persistent student photos.
- `screenshots/` — persistent recognition screenshots.
- `logs/` — server log files.
- `scripts/start_server.bat` — Windows launcher implementation.

## Dependency flow

```text
admin_panel
├── frontend
├── camera_service
│   ├── database
│   └── settings
├── database
│   └── settings
└── settings

face_database importer
├── camera_service
├── database
└── settings
```

All application data paths are resolved from the repository root with
`pathlib.Path`, so starting `admin_panel.py` does not depend on the current
working directory.

## Production

Environment variable names and values remain unchanged. Example:

```powershell
$env:PRODUCTION="1"
$env:APP_PORT="8000"
$env:APP_AUTO_PORT="0"
$env:TEACHER_PASSWORD="your-strong-teacher-password"
$env:ADMIN_PASSWORD="your-strong-admin-password"
$env:ADMIN_TOKEN="your-long-random-admin-token"
$env:ALLOWED_ORIGINS="http://127.0.0.1:8000,http://localhost:8000"
python admin_panel.py
```

Back up these paths regularly:

- `data/school.db`
- `face_database/`
- `screenshots/`
