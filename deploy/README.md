# Production deployment

The production stack is intentionally separate from the development Compose
file. It runs one Uvicorn process without reload, keeps PostgreSQL private,
terminates HTTPS at Nginx, verifies backups by restoring them, and exposes
Prometheus only on `127.0.0.1:9090`.

## Required files

Create these untracked files on the production host:

```text
.env.prod
deploy/secrets/postgres_password
deploy/secrets/admin_bootstrap_password
deploy/secrets/session_secret
deploy/certs/fullchain.pem
deploy/certs/privkey.pem
deploy/models/anti_spoof.onnx
deploy/models/fsvfm_vit_s_oci2m_run0.pth.tar
```

Copy `.env.prod.example` to `.env.prod`. Use independent random values; the
PostgreSQL and session values must have at least 32 characters and the bootstrap
administrator password at least 12. Use filenames without `.txt`. The backend
constructs its PostgreSQL URL from non-secret `.env.prod` identifiers and the
single `postgres_password` Docker secret, so there is no duplicate URL secret.
On Linux use mode `0600`; group/world-readable secrets fail preflight.

Obtain `fullchain.pem` and `privkey.pem` from the organization's ACME/certificate
provider for the exact `DOMAIN`. Never use a self-signed certificate for the
school deployment. Restrict secret and private-key permissions to the deployment
operator. On Linux, ensure UID `10001` can write `face_database/`, `screenshots/`
and `logs/`.

The experimental MiniFASNetV2 PAD candidate is mounted at
`/models/anti_spoof.onnx`. Set `ANTI_SPOOFING_MODEL_TYPE=minifasnet_v2_80`, pin
the reviewed file with `ANTI_SPOOFING_MODEL_SHA256`, and verify the exact output
class order through `ANTI_SPOOFING_CLASS_NAMES` and
`ANTI_SPOOFING_LIVE_INDEX`. The profile expects BGR float32 NCHW `1x3x80x80`
after a 2.7-scale bbox crop and division by 255. The threshold must be selected
from local print and replay captures; the default is only a configuration
baseline and does not establish production readiness.

FS-VFM is mounted read-only at
`/models/fsvfm_vit_s_oci2m_run0.pth.tar`. `FSVFM_CHECKPOINT` uses that same
container path and `FSVFM_CHECKPOINT_SHA256` pins the reviewed artifact. Keep
`FSVFM_SHADOW_MODE=true`; a shadow validation failure is degraded readiness and
must not become an attendance gate.

Schema upgrades are an explicit deployment step and are never run by the
production API process:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm backend \
  python -m school_face_id.db.migrate upgrade
```

## Validate and start

```bash
deploy/production_deploy.sh --dry-run
deploy/production_deploy.sh
```

Only ports 80 and 443 should be internet-accessible. Port 9090 binds to
localhost for an SSH tunnel or local monitoring agent. Configure firewall rules
accordingly.

## Verification

```bash
python scripts/production_smoke.py \
  --base-url https://school.example.com \
  --admin-password-file deploy/secrets/admin_bootstrap_password
tail -n 10 data/backups/backup-status.log
```

The smoke test requires a trusted certificate and checks liveness/readiness,
login and role enforcement, cameras, classes, students, attendance, audit,
model state and XLSX export. Public metrics must return 403/404; Prometheus
scrapes them directly over the private network.

Prometheus is available only from the host at `http://127.0.0.1:9090`. Alert on:

- `amanat_database_up == 0` for one minute;
- `amanat_camera_connected == 0` during school hours;
- increasing `amanat_camera_last_frame_age_seconds`;
- `amanat_backup_age_seconds > 93600` (26 hours).

Certificate renewal is provider-specific. Renew into `deploy/certs/`, validate
the new certificate, then run `docker compose ... exec gateway nginx -s reload`.
