# PostgreSQL backup and restore

The Compose stack runs the `backup` service once a day. Every backup is first
written with a `.partial` suffix, checked with `pg_restore --list`, then restored
into the temporary `amanat_restore_check` database. A backup is marked as valid
only after the restored database contains `alembic_version`.

Backups are stored in `data/backups/` and retained for 30 days by default.
Change `BACKUP_INTERVAL_SECONDS` or `BACKUP_RETENTION_DAYS` in `.env.docker` if
needed. Check the most recent verification with:

```powershell
Get-Content data\backups\backup-status.log -Tail 10
docker compose --env-file .env.docker logs --tail 50 backup
```

## Manual backup and verification

Restarting the backup service immediately creates and verifies a new dump:

```powershell
docker compose --env-file .env.docker restart backup
```

Wait until a new `verified` line appears in `backup-status.log`. Copy verified
dumps to separate encrypted storage; a backup on the same computer is not
protection from disk loss or ransomware.

## Restore

Use a maintenance window: restoring replaces the selected database. First stop
the API, resolve the exact dump path, and preserve the current database with a
fresh dump.

```powershell
docker compose --env-file .env.docker stop backend
Get-ChildItem data\backups\amanat-*.dump | Sort-Object LastWriteTime -Descending
docker compose --env-file .env.docker restart backup
```

After the new backup has a `verified` entry, restore the selected file. Replace
`amanat-YYYYMMDDTHHMMSSZ.dump` with the exact verified filename:

```powershell
docker compose --env-file .env.docker exec -T backup dropdb --host db --username amanat --if-exists --force amanat_restore
docker compose --env-file .env.docker exec -T backup createdb --host db --username amanat amanat_restore
docker compose --env-file .env.docker exec -T backup pg_restore --host db --username amanat --no-owner --no-privileges --exit-on-error --dbname amanat_restore /backups/amanat-YYYYMMDDTHHMMSSZ.dump
```

The commands intentionally restore into a separate database. Verify it before
switching `POSTGRES_DB`/`DATABASE_URL` to it. Run the application smoke tests,
then start the API again:

```powershell
docker compose --env-file .env.docker start backend
```

Never restore an unverified `.partial` file.
