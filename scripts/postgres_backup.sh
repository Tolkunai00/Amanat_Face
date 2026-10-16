#!/bin/sh
set -eu

backup_dir="${BACKUP_DIR:-/backups}"
interval_seconds="${BACKUP_INTERVAL_SECONDS:-86400}"
retention_days="${BACKUP_RETENTION_DAYS:-30}"
offsite_mode="${OFFSITE_BACKUP_MODE:-none}"
offsite_status_file="${OFFSITE_BACKUP_STATUS_FILE:-$backup_dir/offsite-status.json}"

case "$retention_days" in *[!0-9]*|'') echo "BACKUP_RETENTION_DAYS must be an integer" >&2; exit 2;; esac
case "$interval_seconds" in *[!0-9]*|'') echo "BACKUP_INTERVAL_SECONDS must be an integer" >&2; exit 2;; esac
case "${POSTGRES_DB:-amanat}" in *[!A-Za-z0-9_]*|'') echo "POSTGRES_DB contains unsafe characters" >&2; exit 2;; esac

mkdir -p "$backup_dir"
export PGHOST="${POSTGRES_HOST:-db}"
export PGUSER="${POSTGRES_USER:-amanat}"
if [ -n "${POSTGRES_PASSWORD_FILE:-}" ]; then
  PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")"
else
  PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD or POSTGRES_PASSWORD_FILE is required}"
fi
export PGPASSWORD

temporary_path=""
metadata_temporary_path=""
verify_database=""

cleanup() {
  [ -z "$temporary_path" ] || rm -f "$temporary_path"
  [ -z "$metadata_temporary_path" ] || rm -f "$metadata_temporary_path"
  if [ -n "$verify_database" ]; then
    dropdb --if-exists --force "$verify_database" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

write_offsite_status() {
  status="$1"
  message="$2"
  status_tmp="$offsite_status_file.partial.$$"
  printf '{"timestamp_utc":"%s","mode":"%s","status":"%s","message":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$offsite_mode" "$status" "$message" > "$status_tmp"
  mv "$status_tmp" "$offsite_status_file"
}

publish_offsite() {
  dump_path="$1"
  metadata_path="$2"
  case "$offsite_mode" in
    none)
      write_offsite_status "not_configured" "OFFSITE_BACKUP_MODE=none"
      ;;
    filesystem)
      destination="${OFFSITE_BACKUP_DIR:?OFFSITE_BACKUP_DIR is required for filesystem mode}"
      mkdir -p "$destination"
      dump_target="$destination/$(basename "$dump_path")"
      metadata_target="$destination/$(basename "$metadata_path")"
      cp "$dump_path" "$dump_target.partial.$$"
      mv "$dump_target.partial.$$" "$dump_target"
      cp "$metadata_path" "$metadata_target.partial.$$"
      mv "$metadata_target.partial.$$" "$metadata_target"
      write_offsite_status "uploaded" "filesystem copy completed"
      ;;
    s3)
      uploader="${OFFSITE_S3_UPLOADER:?OFFSITE_S3_UPLOADER is required for s3 mode}"
      [ -f "$uploader" ] || { echo "OFFSITE_S3_UPLOADER does not exist" >&2; return 1; }
      sh "$uploader" "$dump_path" "$metadata_path"
      write_offsite_status "uploaded" "s3 uploader completed"
      ;;
    *)
      echo "OFFSITE_BACKUP_MODE must be none, filesystem, or s3" >&2
      return 1
      ;;
  esac
}

run_backup() {
  timestamp_compact="$(date -u +%Y%m%dT%H%M%SZ)"
  timestamp_iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  nonce="$(od -An -N4 -tx4 /dev/urandom | tr -d ' \n')"
  backup_filename="amanat-$timestamp_compact-$nonce.dump"
  dump_path="$backup_dir/$backup_filename"
  metadata_path="$dump_path.json"
  temporary_path="$dump_path.partial.$$"
  metadata_temporary_path="$metadata_path.partial.$$"

  if ! pg_dump --format=custom --file="$temporary_path" "${POSTGRES_DB:-amanat}"; then
    echo "pg_dump failed" >&2
    return 1
  fi
  [ -s "$temporary_path" ] || { echo "pg_dump produced an empty backup" >&2; return 1; }
  pg_restore --list "$temporary_path" >/dev/null
  sha256="$(sha256sum "$temporary_path" | awk '{print $1}' | sed 's/^\\//')"
  backup_size="$(wc -c < "$temporary_path" | tr -d ' ')"

  verify_database="amanat_restore_${timestamp_compact%%Z}_$$_$nonce"
  verify_database="$(printf '%s' "$verify_database" | tr -cd 'A-Za-z0-9_' | cut -c1-63)"
  createdb "$verify_database"
  pg_restore --exit-on-error --no-owner --no-privileges \
    --dbname="$verify_database" "$temporary_path"

  alembic_revision="$(psql --dbname="$verify_database" --tuples-only --no-align \
    --command='SELECT version_num FROM alembic_version')"
  [ -n "$alembic_revision" ] || { echo "Restored database has no Alembic revision" >&2; return 1; }
  students_count="$(psql --dbname="$verify_database" -Atc 'SELECT COUNT(*) FROM students')"
  attendance_count="$(psql --dbname="$verify_database" -Atc 'SELECT COUNT(*) FROM attendance_events')"
  embeddings_count="$(psql --dbname="$verify_database" -Atc 'SELECT COUNT(*) FROM face_embeddings')"
  teachers_count="$(psql --dbname="$verify_database" -Atc 'SELECT COUNT(*) FROM teachers')"
  parents_count="$(psql --dbname="$verify_database" -Atc 'SELECT COUNT(*) FROM parent_accounts')"
  tool_version="$(pg_dump --version | tr '"' "'")"

  printf '%s\n' \
    "{\"timestamp_utc\":\"$timestamp_iso\",\"database_name\":\"${POSTGRES_DB:-amanat}\",\"alembic_revision\":\"$alembic_revision\",\"backup_filename\":\"$backup_filename\",\"backup_size\":$backup_size,\"sha256\":\"$sha256\",\"tool_version\":\"$tool_version\",\"counts\":{\"students\":$students_count,\"attendance\":$attendance_count,\"face_embeddings\":$embeddings_count,\"teachers\":$teachers_count,\"parents\":$parents_count}}" \
    > "$metadata_temporary_path"

  dropdb --if-exists --force "$verify_database"
  verify_database=""
  mv "$temporary_path" "$dump_path"
  temporary_path=""
  mv "$metadata_temporary_path" "$metadata_path"
  metadata_temporary_path=""

  publish_offsite "$dump_path" "$metadata_path"
  find "$backup_dir" -type f \( -name 'amanat-*.dump' -o -name 'amanat-*.dump.json' \) \
    -mtime "+$retention_days" -delete
  printf '%s verified %s sha256=%s\n' "$timestamp_compact" "$dump_path" "$sha256" \
    >> "$backup_dir/backup-status.log"
}

while true; do
  run_backup
  [ "${BACKUP_RUN_ONCE:-0}" = "1" ] && break
  sleep "$interval_seconds"
done
