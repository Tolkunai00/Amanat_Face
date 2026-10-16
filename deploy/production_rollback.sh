#!/bin/sh
set -eu

root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="$root_dir/.env.prod"
previous_version=""
restore_db=""
schema_compatible=0

usage() {
  echo "Usage: deploy/production_rollback.sh --previous-version TAG [--schema-compatible] [--restore-db BACKUP] [--env-file PATH]"
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --previous-version) shift; previous_version="${1:-}" ;;
    --restore-db) shift; restore_db="${1:-}" ;;
    --schema-compatible) schema_compatible=1 ;;
    --env-file) shift; env_file="${1:-}" ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done
[ -n "$previous_version" ] || { usage >&2; exit 2; }
case "$previous_version" in latest|*[!A-Za-z0-9._+-]*) echo "Unsafe previous version" >&2; exit 2;; esac

if [ "$schema_compatible" != 1 ] && [ ! -f "$root_dir/deploy/releases/$previous_version.rollback-safe" ] && [ -z "$restore_db" ]; then
  echo "STOP: code rollback is not DB rollback. Migration reversibility is unproven." >&2
  echo "Review migrations, then use --schema-compatible, or follow the backup restore procedure with --restore-db." >&2
  exit 3
fi

if [ -n "$restore_db" ]; then
  [ -f "$restore_db" ] || { echo "Backup does not exist: $restore_db" >&2; exit 2; }
  echo "Explicit DB restore requested; this is separate from code rollback."
  docker compose --env-file "$env_file" -f "$root_dir/docker-compose.prod.yml" run --rm \
    -v "$restore_db:/restore/input.dump:ro" backup \
    sh /scripts/postgres_restore.sh /restore/input.dump "${POSTGRES_DB:-amanat}"
fi

if ! docker image inspect "amanat-face-backend:$previous_version" "amanat-face-frontend:$previous_version" >/dev/null 2>&1; then
  RELEASE_VERSION="$previous_version" docker compose --env-file "$env_file" \
    -f "$root_dir/docker-compose.prod.yml" pull backend frontend
fi
RELEASE_VERSION="$previous_version" docker compose --env-file "$env_file" \
  -f "$root_dir/docker-compose.prod.yml" up -d --no-build backend frontend gateway

domain="$(sed -n 's/^DOMAIN=//p' "$env_file" | tail -n 1 | tr -d '\r')"
attempts=0
while [ "$attempts" -lt 60 ]; do
  code="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "https://$domain/api/v1/health/ready" || true)"
  [ "$code" = 200 ] && { echo "PASS rollback: release=$previous_version health=ready"; exit 0; }
  attempts=$((attempts + 1)); sleep 2
done
echo "FAIL rollback health verification: HTTP $code" >&2
exit 1
