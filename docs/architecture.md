# Architecture

```text
Browser frontend ─┐
Flutter client ────┼── HTTPS /api/v1 ──> FastAPI backend
                   │                         ├── PostgreSQL in Docker (SQLite fallback for local tests)
                   │                         ├── camera streams
                   │                         ├── InsightFace
                   │                         └── protected photos
                   └── no direct storage access
```

## Active boundaries

- `backend/` owns authentication, authorization, persistence, attendance rules,
  cameras, recognition and protected media.
- `frontend/` owns browser presentation, forms and navigation. It only imports
  JavaScript modules from its own directory and only reads data through API.
- `flutter/` is an independent client of the same `/api/v1` contract.
- Runtime data remains outside all source packages.

## Separate production deployment

Recommended origins:

```text
https://app.example.com  -> frontend
https://api.example.com  -> backend
```

Configure `VITE_API_BASE_URL=https://api.example.com` during the frontend build
and `ALLOWED_ORIGINS=https://app.example.com` in the backend environment.
Prefer subdomains under one parent site for cookie-based sessions. Unrelated
sites require an explicit cross-site token/cookie and CSRF design.

## Backend modules

```text
school_face_id/
├── main.py
├── api/
│   ├── dependencies.py
│   ├── router.py
│   └── routers/
│       ├── auth.py
│       ├── attendance.py
│       ├── camera.py
│       ├── health.py
│       ├── students.py
│       └── teachers.py
├── config/
├── data/                 PostgreSQL in Docker (SQLite fallback for local tests) connection and migration facade
├── repositories/
│   ├── auth.py
│   ├── attendance.py
│   ├── students.py
│   └── teachers.py
├── importers/
└── recognition/
    ├── anti_spoof.py         local ONNX PAD adapter
    ├── embedding.py          canonical normalized embedding extraction
    ├── liveness.py           temporal multi-frame heuristic
    ├── matcher.py            identity scoring and margin gate
    ├── models.py             internal biometric decision objects
    ├── tracker.py            per-camera IoU/center-distance tracks
    └── camera_service.py     camera workers and pipeline orchestration

backend/migrations/       Alembic schema history
frontend/src/             independent browser client
flutter/                  reserved independent Flutter client
```

## Persistence model

Alembic is the only schema-version mechanism. Students have stable IDs; face
embeddings and attendance refer to those IDs. Classes and teacher assignments
are represented by `classes` and `teacher_classes`. The normalized tables no
longer store parent secrets or duplicate student/class names in attendance and
face embeddings. Parent passwords are PBKDF2 hashes in `parent_accounts`;
session tokens are stored only as SHA-256 digests. Revision `20260925_02` is
irreversible by design; restore the pre-migration backup for a rollback.

## Recognition boundary

Recognition uses multiple normalized embeddings per stable student identity.
The code path executed by each camera worker is:

```text
Camera frame
  -> Detection (fast; accurate/fallback only when the previous result is unusable)
  -> Per-camera face tracking
  -> Quality gate
  -> PAD / anti-spoof (local ONNX adapter, or an explicit development bypass)
  -> Temporal liveness
  -> Normalized InsightFace embedding
  -> Identity matching (similarity threshold and distinct-identity margin)
  -> Attendance decision
  -> Existing keldi/ketti attendance service
```

Detector instances are cached by profile (`320`, `480`, `640`) and protected by
per-profile inference locks. A camera worker owns its tracker and temporal
liveness state, so detections from different cameras cannot share a track.

Three boundaries are intentional:

- `track_id != student_id`: a track exists before a person is recognized, and
  recognition never participates in geometric track association.
- Temporal liveness is not PAD. Temporal liveness measures multi-frame motion
  and pose change; PAD estimates whether the presentation is an attack.
- Recognition is not attendance permission. A known identity cannot create an
  event until quality, PAD (or explicit non-production bypass), and temporal
  liveness gates have passed. `FaceDecision.rejection_reason` records why the
  event was denied.

When `PRODUCTION=1`, configuration validation requires a PAD model path and the
application loads the ONNX session during startup. A missing or unloadable model
therefore cannot silently turn into a production bypass.

### Experimental MiniFASNetV2 PAD profile

`ANTI_SPOOFING_MODEL_TYPE=minifasnet_v2_80` enables the first experimental RGB
PAD candidate based on the Silent Face Anti-Spoofing MiniFASNetV2 2.7/80x80
preprocessing contract. The backend does not download weights. A reviewed local
ONNX file must be supplied through `ANTI_SPOOFING_MODEL_PATH`; an optional
`ANTI_SPOOFING_MODEL_SHA256` pins the exact artifact.

The profile expands the detector bbox by `2.7` around its center, clamps it to
the frame, resizes it to `80x80`, preserves BGR channel order, converts to
float32, divides by 255, and produces a contiguous `1x3x80x80` NCHW tensor. It
does not use ArcFace alignment. The configured three-class output is converted
from logits with softmax and retains all configured class probabilities as well
as the live-class score. Class order must be verified against the exact ONNX
artifact and configured with `ANTI_SPOOFING_CLASS_NAMES` and
`ANTI_SPOOFING_LIVE_INDEX`.

This candidate is not production-ready by inclusion alone:

- RGB-only PAD does not guarantee protection against every presentation attack.
- Results depend strongly on camera, compression, lighting and capture distance.
- Cross-domain generalization is limited.
- `ANTI_SPOOFING_THRESHOLD` must be calibrated on local camera captures.
- Print attacks and video replay attacks require separate local evaluation.
- A threshold reported by another repository is not a production threshold for
  this deployment.

Successful PAD results are cached only on the owning camera track and only for
`ANTI_SPOOFING_CACHE_TTL_SECONDS`. Track expiration discards the cache. PAD and
temporal liveness remain independent security stages.

## Offline biometric evaluation

`backend/scripts/evaluate_biometrics.py` reports recognition FAR/FRR/TPR/FPR,
PAD confusion counts/APCER/BPCER/ACER when labels and a model exist, and stage
latencies. It does not alter configured thresholds. Expected local layout:

```text
data/biometric_eval/
├── live/
│   ├── person_001/
│   └── person_002/
└── spoof/
    ├── phone_photo/
    ├── phone_video/
    ├── print/
    ├── laptop_photo/
    └── laptop_video/
```

The repository ignores all of `data/`; real images and embeddings must stay
local and must not be committed or sent to external services.

Use `--pad-scores-csv data/biometric_eval/pad_scores.csv` to export only paths,
labels, attack types, scores, predicted classes, decisions and inference times.
`--pad-threshold-sweep-step 0.01` evaluates thresholds without changing `.env`;
the minimum ACER on this evaluation set is not guaranteed to generalize to
production traffic.
