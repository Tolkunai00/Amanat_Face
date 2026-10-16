# Amanat Face production runbook

## Prerequisites, hardware, OS and Docker

Use a supported 64-bit Linux host, SSD storage with monitored free space, enough
CPU/RAM for two camera pipelines, a UPS, synchronized time, and a GPU only if the
approved model runtime requires it. Install a supported Docker Engine with the
Compose v2 plugin and OpenSSL. Restrict inbound traffic to 80/443; Prometheus and
Alertmanager bind only to loopback.

The API image runs as UID 10001. Prometheus/Alertmanager use their vendor
unprivileged users. Official PostgreSQL and Nginx start a minimal root entrypoint
only to initialize storage/bind privileged ports, then run service workers as
their dedicated users; they receive no privileged mode or Docker socket.

```bash
docker version
docker compose version
openssl version
git status --short
```

## Secrets

`.env.prod` contains non-secret configuration. The only critical-secret sources
are Docker secret files; environment fallbacks are forbidden in production.

```bash
install -d -m 0700 deploy/secrets deploy/certs
openssl rand -base64 36 > deploy/secrets/postgres_password
openssl rand -base64 36 > deploy/secrets/admin_bootstrap_password
openssl rand -base64 48 > deploy/secrets/session_secret
chmod 0600 deploy/secrets/*
```

Never print these files. Rotation of the PostgreSQL password also requires a
coordinated PostgreSQL role update. Rotating `session_secret` invalidates active
sessions. `.env.prod` supplies domain, DB identifiers, cameras, model settings,
thresholds and release version; secret files always have sole precedence.

## TLS

Install the real provider chain and matching key as `deploy/certs/fullchain.pem`
and `deploy/certs/privkey.pem`; do not create a self-signed production certificate.

```bash
python deploy/production_preflight.py --check tls --env-file .env.prod
```

Renew atomically, repeat validation, then `docker compose --env-file .env.prod -f docker-compose.prod.yml exec gateway nginx -s reload`.

## Models and licenses

Place approved artifacts under `deploy/models/`, set reviewed SHA-256 values, and
keep `FSVFM_SHADOW_MODE=true`. Do not change thresholds during PHASE 2C.

```bash
python deploy/production_preflight.py --check models --env-file .env.prod
```

## Database and migration

PostgreSQL is private and has no host port. API startup never migrates schema.

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm backend python -m school_face_id.db.migrate status
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm backend python -m school_face_id.db.migrate upgrade
```

## Deployment and update procedure

Use an immutable tag or commit in `RELEASE_VERSION`. Review the diff, backup,
release notes and migration compatibility first.

```bash
cp .env.prod.example .env.prod
deploy/production_deploy.sh --dry-run
deploy/production_deploy.sh
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

The deploy sequence validates configuration/secrets/TLS/models/tools, renders
Compose, creates and restore-verifies a backup, migrates, builds/pulls, starts,
waits for liveness/readiness and runs the read-only smoke test. Any critical
failure stops the sequence.

## Smoke test

```bash
python scripts/production_smoke.py --base-url https://school.example.com --admin-password-file deploy/secrets/admin_bootstrap_password --release-version "$RELEASE_VERSION"
```

It reads health, auth/roles, students, cameras, attendance and reports. It writes
no attendance. It confirms public metrics are denied.

## Rollback

Code rollback and database rollback are separate. First review every migration.

```bash
deploy/production_rollback.sh --previous-version v1.2.3 --schema-compatible
# Only after incident approval and the restore runbook:
deploy/production_rollback.sh --previous-version v1.2.3 --restore-db /absolute/amanat.dump
```

Without a `deploy/releases/<version>.rollback-safe` review marker, explicit
`--schema-compatible`, or explicit restore, automation stops. Restore is never automatic.

## Backup, restore and offsite backup

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm -e BACKUP_RUN_ONCE=1 backup
tail -n 1 data/backups/backup-status.log
scripts/postgres_restore.sh /absolute/amanat.dump amanat_restore_test --temporary
```

Follow [BACKUP_RESTORE.md](BACKUP_RESTORE.md). Use `filesystem` only with a real
off-host/second-device mount, or an untracked credential configuration for S3.
Never put backup credentials in logs. Confirm `amanat_offsite_backup_success`.

## Cameras and camera soak

Validate least-privilege RTSP users and network reachability, then follow
[CAMERA_OPERATIONS.md](CAMERA_OPERATIONS.md). Run the existing soak diagnostic for
at least one real hour onsite and retain its report. One failed camera must not be
treated as evidence that the other is healthy.

## Monitoring and alerts

Prometheus: `http://127.0.0.1:9090`; Alertmanager: `http://127.0.0.1:9093`.
Rules are in `deploy/prometheus/rules/amanat.yml`. Initial rate/latency thresholds
are conservative and require tuning from measured baselines. Disk warning and
critical thresholds come from `DISK_WARNING_PERCENT`/`DISK_CRITICAL_PERCENT`.
Supply an untracked Alertmanager config through `ALERTMANAGER_CONFIG_FILE` and set
`ALERTMANAGER_RECEIVER`; otherwise readiness truthfully says `not_configured`.
Send a controlled test alert before launch.

## Disk full

At warning: identify the affected `database`, `backup`, `logs` or `models` label,
confirm retention/rotation, and expand storage. Do not delete current DB files or
the last verified backup. At critical: pause deploys and camera ingestion under
incident control, preserve evidence, free only approved expendable data.

## DB, camera and model failure

DB failure: check container health/storage, preserve the volume, stop deploys,
restore only after incident approval. Camera failure: check power/network/RTSP,
leave the other worker independent, and verify recovery counters. Model failure:
stop release, verify artifact digest/license/mount; never bypass MiniFAS or enable
FS-VFM enforcement as a workaround.

## Incident recovery

Record time, release, request IDs, affected cameras and alerts; avoid biometrics
or credentials in tickets. Stabilize, preserve logs/backups, choose forward fix or
reviewed rollback, run readiness and smoke, verify alert delivery, and document
root cause. Failure contracts are in [FAILURE_MODE_SMOKE.md](FAILURE_MODE_SMOKE.md).
