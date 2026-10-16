#!/bin/sh
set -eu

usage() {
  echo "Usage: postgres_restore.sh BACKUP_PATH TARGET_DB [--replace-target] [--allow-production-target] [--temporary]" >&2
  exit 2
}

[ "$#" -ge 2 ] || usage
backup_path="$1"
target_database="$2"
shift 2
replace_target=0
allow_production=0
temporary=0
for argument in "$@"; do
  case "$argument" in
    --replace-target) replace_target=1 ;;
    --allow-production-target) allow_production=1 ;;
    --temporary) temporary=1 ;;
    *) usage ;;
  esac
done

case "$target_database" in *[!A-Za-z0-9_]*|'') echo "TARGET_DB contains unsafe characters" >&2; exit 2;; esac
[ -f "$backup_path" ] || { echo "Backup does not exist" >&2; exit 3; }
[ -s "$backup_path" ] || { echo "Backup is empty" >&2; exit 3; }
metadata_path="$backup_path.json"

export PGHOST="${POSTGRES_HOST:-db}"
export PGUSER="${POSTGRES_USER:-amanat}"
if [ -n "${POSTGRES_PASSWORD_FILE:-}" ]; then
  PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")"
else
  PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD or POSTGRES_PASSWORD_FILE is required}"
fi
export PGPASSWORD

production_database="${POSTGRES_DB:-amanat}"
if [ "$target_database" = "$production_database" ] && [ "$allow_production" != "1" ]; then
  echo "Refusing to restore over production database without --allow-production-target" >&2
  exit 4
fi

json_string() {
  key="$1"
  sed -n "s/.*\"$key\":\"\([^\"]*\)\".*/\1/p" "$metadata_path" | head -n 1
}
json_count() {
  key="$1"
  sed -n "s/.*\"$key\":\([0-9][0-9]*\).*/\1/p" "$metadata_path" | head -n 1
}

if [ -f "$metadata_path" ]; then
  expected_sha="$(json_string sha256)"
  [ -n "$expected_sha" ] || { echo "Metadata has no SHA256" >&2; exit 5; }
  actual_sha="$(sha256sum "$backup_path" | awk '{print $1}' | sed 's/^\\//')"
  [ "$actual_sha" = "$expected_sha" ] || {
    echo "Backup SHA256 mismatch (expected $expected_sha, actual $actual_sha)" >&2
    exit 5
  }
fi
pg_restore --list "$backup_path" >/dev/null || { echo "Backup catalog is corrupt" >&2; exit 6; }

created=0
success=0
cleanup() {
  if [ "$created" = "1" ] && { [ "$success" != "1" ] || [ "$temporary" = "1" ]; }; then
    dropdb --if-exists --force "$target_database" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

exists="$(psql --dbname=postgres -Atc "SELECT 1 FROM pg_database WHERE datname='$target_database'")"
if [ "$exists" = "1" ]; then
  [ "$replace_target" = "1" ] || { echo "Target database exists; use --replace-target" >&2; exit 7; }
  dropdb --if-exists --force "$target_database"
fi
createdb "$target_database"
created=1
pg_restore --exit-on-error --no-owner --no-privileges --dbname="$target_database" "$backup_path"

revision="$(psql --dbname="$target_database" -Atc 'SELECT version_num FROM alembic_version')"
[ -n "$revision" ] || { echo "Restored database has no Alembic revision" >&2; exit 8; }
students="$(psql --dbname="$target_database" -Atc 'SELECT COUNT(*) FROM students')"
attendance="$(psql --dbname="$target_database" -Atc 'SELECT COUNT(*) FROM attendance_events')"
embeddings="$(psql --dbname="$target_database" -Atc 'SELECT COUNT(*) FROM face_embeddings')"
teachers="$(psql --dbname="$target_database" -Atc 'SELECT COUNT(*) FROM teachers')"
parents="$(psql --dbname="$target_database" -Atc 'SELECT COUNT(*) FROM parent_accounts')"

if [ -f "$metadata_path" ]; then
  for pair in "students:$students" "attendance:$attendance" "face_embeddings:$embeddings" "teachers:$teachers" "parents:$parents"; do
    key="${pair%%:*}"
    actual="${pair#*:}"
    expected="$(json_count "$key")"
    [ -z "$expected" ] || [ "$actual" = "$expected" ] || {
      echo "Integrity count mismatch for $key" >&2
      exit 9
    }
  done
fi

success=1
printf '{"status":"restored","target_database":"%s","alembic_revision":"%s","counts":{"students":%s,"attendance":%s,"face_embeddings":%s,"teachers":%s,"parents":%s}}\n' \
  "$target_database" "$revision" "$students" "$attendance" "$embeddings" "$teachers" "$parents"
