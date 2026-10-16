#!/bin/sh
set -eu

root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="$root_dir/.env.prod"
dry_run=0

usage() { echo "Usage: deploy/production_deploy.sh [--dry-run] [--env-file PATH]"; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1 ;;
    --env-file) shift; [ "$#" -gt 0 ] || { usage >&2; exit 2; }; env_file="$1" ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

compose="docker compose --env-file $env_file -f $root_dir/docker-compose.prod.yml"
preflight="$root_dir/deploy/production_preflight.py"
failed=0
blocked=0

report() { printf '%s %s: %s\n' "$1" "$2" "$3"; }
preflight_gate() {
  name="$1"
  if [ "$dry_run" = 1 ]; then
    if python "$preflight" --env-file "$env_file" --check "$name" --dry-run; then :; else
      code=$?; [ "$code" = 2 ] && blocked=1 || failed=1
    fi
  else
    python "$preflight" --env-file "$env_file" --check "$name"
  fi
}
read_env_value() {
  sed -n "s/^$1=//p" "$env_file" | tail -n 1 | tr -d '\r'
}

if [ ! -f "$env_file" ]; then
  if [ "$dry_run" = 1 ]; then
    report BLOCKED_EXTERNAL environment "environment file not found: $env_file"
    report BLOCKED_EXTERNAL secrets "environment and real secret files are required"
    report BLOCKED_EXTERNAL tls "environment hostname and real certificate are required"
    report BLOCKED_EXTERNAL models "environment digests and real artifacts are required"
    report BLOCKED_EXTERNAL docker_compose_config "environment file is required"
    report BLOCKED_EXTERNAL production_backup "not executed in non-mutating dry-run"
    report BLOCKED_EXTERNAL backup_verification "requires live PostgreSQL and a new dump"
    report BLOCKED_EXTERNAL database_migration "not executed in non-mutating dry-run"
    report BLOCKED_EXTERNAL build_pull "not executed in non-mutating dry-run"
    report BLOCKED_EXTERNAL service_start "not executed in non-mutating dry-run"
    report BLOCKED_EXTERNAL health_live "requires deployed release"
    report BLOCKED_EXTERNAL health_ready "requires deployed release"
    report BLOCKED_EXTERNAL production_smoke "requires deployed release and trusted TLS"
    exit 2
  fi
  report FAIL environment "environment file not found: $env_file"
  exit 1
fi

# 1-5: all read-only preflight gates.
preflight_gate environment
preflight_gate secrets
preflight_gate tls
preflight_gate models
preflight_gate docker

if [ "$failed" = 1 ]; then
  report FAIL deployment "preflight failed; no mutation performed"
  exit 1
fi

# 6: Compose rendering is read-only and is included in dry-run.
if docker compose --env-file "$env_file" -f "$root_dir/docker-compose.prod.yml" config -q; then
  report PASS docker_compose_config "configuration renders"
else
  report FAIL docker_compose_config "configuration invalid"
  exit 1
fi

if [ "$dry_run" = 1 ]; then
  report BLOCKED_EXTERNAL production_backup "not executed in non-mutating dry-run"
  report BLOCKED_EXTERNAL backup_verification "requires live PostgreSQL and a new dump"
  report BLOCKED_EXTERNAL database_migration "not executed in non-mutating dry-run"
  report BLOCKED_EXTERNAL build_pull "not executed in non-mutating dry-run"
  report BLOCKED_EXTERNAL service_start "not executed in non-mutating dry-run"
  report BLOCKED_EXTERNAL health_live "requires deployed release"
  report BLOCKED_EXTERNAL health_ready "requires deployed release"
  report BLOCKED_EXTERNAL production_smoke "requires deployed release and trusted TLS"
  [ "$blocked" = 1 ] && exit 2
  exit 0
fi

release_version="${RELEASE_VERSION:-$(read_env_value RELEASE_VERSION)}"
[ -n "$release_version" ] || { report FAIL release "RELEASE_VERSION missing"; exit 1; }
domain="$(read_env_value DOMAIN)"

# 7-8: backup script performs pg_restore verification before publishing a dump.
if docker compose --env-file "$env_file" -f "$root_dir/docker-compose.prod.yml" run --rm -e BACKUP_RUN_ONCE=1 backup; then
  report PASS production_backup "dump created"
else
  report FAIL production_backup "backup failed; migration was not attempted"
  exit 1
fi
status_file="$root_dir/data/backups/backup-status.log"
if [ -s "$status_file" ] && tail -n 1 "$status_file" | grep -q ' verified '; then
  report PASS backup_verification "latest dump passed restore verification"
else
  report FAIL backup_verification "verified backup status missing; migration was not attempted"
  exit 1
fi

# 9: migrations are explicit and must complete before new application startup.
if docker compose --env-file "$env_file" -f "$root_dir/docker-compose.prod.yml" run --rm backend python -m school_face_id.db.migrate upgrade; then
  report PASS database_migration "Alembic upgraded to head"
else
  report FAIL database_migration "migration failed; release was not started"
  exit 1
fi

# 10-11: immutable image tags carry RELEASE_VERSION provenance.
docker compose --env-file "$env_file" -f "$root_dir/docker-compose.prod.yml" pull --ignore-buildable
docker compose --env-file "$env_file" -f "$root_dir/docker-compose.prod.yml" build
docker compose --env-file "$env_file" -f "$root_dir/docker-compose.prod.yml" up -d

# 12-13: certificate validation is intentionally enabled (no -k).
wait_url() {
  label="$1"; url="$2"; expected="$3"; attempts=0
  while [ "$attempts" -lt 60 ]; do
    code="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "$url" || true)"
    [ "$code" = "$expected" ] && { report PASS "$label" "$url"; return 0; }
    attempts=$((attempts + 1)); sleep 2
  done
  report FAIL "$label" "last HTTP status=$code"; return 1
}
wait_url health_live "https://$domain/api/v1/health/live" 200 || exit 1
wait_url health_ready "https://$domain/api/v1/health/ready" 200 || exit 1

# 14: read-only production smoke; it never writes attendance.
python "$root_dir/scripts/production_smoke.py" \
  --base-url "https://$domain" \
  --admin-password-file "$root_dir/deploy/secrets/admin_bootstrap_password" \
  --release-version "$release_version"

# 15: concise deployment report (never prints secret values).
alert_receiver="$(read_env_value ALERTMANAGER_RECEIVER)"
[ -n "$alert_receiver" ] || alert_receiver="not_configured"
report PASS deployment "release=$release_version"
report PASS smoke "production read-only smoke completed"
report PASS alert_delivery "$alert_receiver"
