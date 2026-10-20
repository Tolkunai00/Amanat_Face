# Amanat School Face ID

The active repository is split into independently deployable applications.

```text
backend/   FastAPI API, PostgreSQL, cameras and InsightFace
frontend/  Vite UI; the development server proxies /api/v1
flutter/   location for the Flutter client
```

## Simple local demo: SQLite + Docker

For a disposable local demo, use the existing opt-in SQLite test mode with
credentials and camera URLs from `.env`:

```powershell
docker compose --env-file .env -f docker-compose.local.yml up -d
```

Open `http://localhost:5173`. The database is stored in
`data/school-local.db`; development migrations run automatically on startup.
This starts a separate empty SQLite database and does not copy PostgreSQL data.
Existing PostgreSQL volumes are preserved. Stop unused PostgreSQL services with
`docker compose --env-file .env stop db backup` when switching to this mode.
Use the same `-f docker-compose.local.yml` option for subsequent local restarts.
This mode is for local demos only; deployed applications use PostgreSQL.

## Development: Vite + Docker backend

Create the private environment file once:

```powershell
Copy-Item .env.docker.example .env.docker
notepad .env.docker
```

Replace both example passwords. Then start the complete stack:

```powershell
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker ps
```

Open `http://localhost:5173`. FastAPI is also published at
`http://127.0.0.1:8080`; PostgreSQL remains private to the Compose network.
Backend source code is bind-mounted and Uvicorn reloads it after changes.
PostgreSQL data and the InsightFace model cache use named volumes; face images,
screenshots and logs use local runtime directories.

For frontend development with Vite hot reload, stop the packaged frontend first:

```powershell
docker compose --env-file .env.docker stop frontend
Set-Location frontend
npm install
npm run dev
```

Keep `VITE_API_BASE_URL` empty in development;
Vite proxies `/api/*` to the Docker backend on port 8080.

Stop without deleting data:

```powershell
docker compose --env-file .env.docker down
```

Do not add `-v` unless you intentionally want to delete the PostgreSQL and
biometric volumes. Docker Desktop must be running. For WSL commands, enable
Docker Desktop > Settings > Resources > WSL Integration.

## Backend process without Docker (optional)

PostgreSQL is required. Set `DATABASE_URL` to a reachable PostgreSQL database
before starting the Python process; the backend no longer creates SQLite files.

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://amanat:PASSWORD@127.0.0.1:5432/amanat'
python backend\run.py
```

Default addresses:

- frontend: use the exact address printed by Vite (normally `http://localhost:5173`);
- backend: `http://127.0.0.1:8080`;
- API docs: `http://127.0.0.1:8080/docs`.

Vite proxies API requests to the backend so the HttpOnly login cookie remains
same-origin.

## Database migrations

Alembic owns schema changes and PostgreSQL is the only runtime database. The
backend requires `DATABASE_URL`, applies pending migrations at startup, and can
also run them explicitly:

```powershell
cd backend
python -m alembic upgrade head
```

Back up PostgreSQL before every migration. Do not store database dumps in Git.
The Compose `backup` service creates a daily dump and performs a real restore
check. See [PostgreSQL backup and restore](docs/postgres-backup-restore.md).

## Runtime data

`data/`, `face_database/`, `screenshots/`, `logs/`, and `tmp/` are runtime
storage and are ignored by Git. They must never be served as public static
folders.

See [docs/architecture.md](docs/architecture.md).
Before a real deployment, complete the [production readiness checklist](docs/production-readiness.md).
Use the separate [production deployment guide](deploy/README.md) for HTTPS,
Docker secrets, monitoring and smoke tests.
