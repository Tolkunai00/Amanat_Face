# Failure-mode smoke contract

Run these scenarios only in an isolated copy with synthetic data. The production
automation never stops the database, corrupts models, fills disks, or disconnects
cameras. `python scripts/failure_mode_smoke.py --json` prints the machine-readable
contract.

| Scenario | Expected health | Expected attendance behavior | Expected alert |
|---|---|---|---|
| DB unavailable | `not_ready`, HTTP 503 | reads/writes fail; no fabricated event | `DatabaseUnavailable`, `BackendNotReady` |
| One camera offline | ready/degraded, HTTP 200 | other camera continues | `CameraOffline` |
| Both cameras offline | ready/degraded, HTTP 200 | no new automatic attendance | `CameraOffline` for both |
| MiniFAS corrupt | `not_ready`, HTTP 503/startup failure | recognition blocked | `BackendNotReady` |
| FS-VFM missing, shadow | `degraded`, HTTP 200 | MiniFAS stays authoritative | operator warning; no enforcement |
| FS-VFM missing, enforcement | `not_ready`, HTTP 503 | recognition blocked | `BackendNotReady` |
| ArcFace unavailable | `not_ready`, HTTP 503 | no recognition attendance | `BackendNotReady` |
| Migration behind head | `not_ready`, HTTP 503 | deploy stops before startup | `MigrationMismatch` |
| Backup failure | application unchanged | deploy stops before migration | eventually `BackupTooOld` |
| Disk low simulation | ready until required writes fail | intervene before critical | `DiskSpaceLow`, then critical |

Safe procedure: clone the stack to a non-production Compose project, replace
cameras with synthetic sources, use an empty synthetic database, run one fault at
a time, save health JSON and Prometheus alert evidence, then destroy only that
explicit test project. Disk-low testing must use a bounded loop device or quota;
never write filler data to a production filesystem.
