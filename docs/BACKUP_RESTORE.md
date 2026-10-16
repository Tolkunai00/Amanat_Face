# PostgreSQL backup and restore

Production backups use PostgreSQL custom format. A backup is considered valid
only after `pg_restore --list`, restore into a unique temporary database, and
integrity queries all succeed. The backup service writes an atomic `.dump` and
`.dump.json` pair. Metadata contains the UTC timestamp, database name, Alembic
revision, size, SHA-256, PostgreSQL tool version, and row counts; it never
contains passwords, URLs, sessions, or camera credentials.

## Create and verify

The Compose backup service runs periodically. For a one-shot verified backup:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm \
  -e BACKUP_RUN_ONCE=1 backup sh /scripts/postgres_backup.sh
```

`BACKUP_RETENTION_DAYS` controls local retention. Failure of `pg_dump`, catalog
validation, temporary restore, revision check, count query, or offsite upload
returns non-zero. Temporary databases have unique names and are removed by a
trap on success, error, or termination.

## Restore

Restore into a new test database first:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm backup \
  sh /scripts/postgres_restore.sh /backups/amanat-TIMESTAMP.dump amanat_restore_test
```

The script verifies the sidecar checksum when present, validates the archive,
checks the Alembic revision, and checks `students`, `attendance_events`,
`face_embeddings`, `teacher_accounts`, and `parent_accounts`. Sidecar counts
are compared exactly.

To replace an existing non-production test database, add `--replace-target`.
Restoring over `POSTGRES_DB` is destructive and is refused unless both
`--replace-target` and `--allow-production-target` are supplied. Before doing
that, stop API/camera writers, retain another verified off-host copy, and record
the current Alembic revision.

Temporary disaster-recovery verification uses `--temporary`; the target is
dropped even after a successful check.

## Off-host modes

- `OFFSITE_BACKUP_MODE=none`: local backup remains enabled; readiness reports
  `offsite_backup.status=not_configured`.
- `filesystem`: set `OFFSITE_BACKUP_DIR_HOST` to an actually separate mounted
  disk. Files and metadata are copied through temporary names and renamed.
- `s3`: provide an isolated executable via `OFFSITE_S3_UPLOADER` and credentials
  through the runtime environment or workload identity. The supplied adapter
  expects an operator-provided AWS CLI and `OFFSITE_S3_URI`; the base backup
  image does not pretend that an uploader is installed.

Do not call a directory on the same physical disk an off-host backup. Periodic
restore drills on a separate PostgreSQL instance remain mandatory.
