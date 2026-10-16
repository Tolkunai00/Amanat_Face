# Production readiness gate

`PASS` requires retained evidence. Missing real infrastructure is
`BLOCKED_EXTERNAL`, not a simulated pass. This table is the release record
template; update evidence links and owner names during the real launch review.

| GATE | STATUS | EVIDENCE | OWNER/ACTION |
|---|---|---|---|
| tests | PASS | `195 passed, 6 skipped` on 2026-10-08 | Re-run on clean release runner |
| docker config | PASS | `.env.prod.example` Compose config exited 0 on 2026-10-08 | Re-run with real `.env.prod` |
| docker build | BLOCKED_EXTERNAL | Docker CLI present; Linux engine unavailable on validation host | Platform: build on release runner |
| secrets | BLOCKED_EXTERNAL | Real untracked 0600 files required | Operator |
| TLS | BLOCKED_EXTERNAL | Real trusted cert/key/SAN required | Operator |
| models | BLOCKED_EXTERNAL | Approved artifacts and matching SHA required | ML/Legal |
| DB migration | NOT_TESTED | Pre/post Alembic status from production | DBA |
| backup | BLOCKED_EXTERNAL | Real PostgreSQL verified dump required | DBA |
| restore | BLOCKED_EXTERNAL | Isolated real restore evidence required | DBA |
| offsite | BLOCKED_EXTERNAL | Successful real off-host upload and restore | Platform |
| camera soak | BLOCKED_EXTERNAL | Real onsite 1h+ soak report | Site Ops |
| load test | NOT_TESTED | Approved production-like load report | Engineering |
| monitoring | PASS | Alert YAML parse/name test and private metrics config test pass | Tune thresholds onsite |
| alert delivery | BLOCKED_EXTERNAL | Receiver config and controlled delivered alert | On-call |
| smoke | BLOCKED_EXTERNAL | Trusted live deployment required | Release manager |
| licenses | BLOCKED_EXTERNAL | Written ArcFace/model authorization | Legal |
| recognition calibration | NOT_TESTED | Held-out onsite FAR/FRR evidence | ML |
| PAD calibration | NOT_TESTED | Onsite camera calibration evidence | ML/Security |
| attack benchmark | NOT_TESTED | Full print/replay benchmark | Security |
| release provenance | BLOCKED_EXTERNAL | Clean signed/tagged immutable release | Release manager |

`PRODUCTION_READY=false`. PHASE 2C cannot change that decision.
