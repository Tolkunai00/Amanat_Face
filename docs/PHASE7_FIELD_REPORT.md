# Phase 7 — initial field report, 2026-10-09

Сбор с камер не выполнен: реальные camera sources и физические operator sessions
не предоставлены. Выполнены все шесть --help и оба audit существующих datasets.
Оба datasets BLOCKED_DATASET. Calibration/benchmark не запускались.
Ноль файлов не доказывает отсутствие leakage в будущих данных.

```text
RECOGNITION DATASET
persons: 0
gallery sessions: 0
probe sessions: 0
cameras: []
duplicates: 0
leakage: NONE_DETECTED_ON_EMPTY_DATASET
coverage: NONE / BLOCKED_DATASET

IDENTITY
genuine count: NOT_MEASURED
impostor count: NOT_MEASURED
FAR: NOT_MEASURED
FRR: NOT_MEASURED
EER: NOT_MEASURED
top1: NOT_MEASURED
unknown rejection: NOT_MEASURED
candidate threshold: NONE
candidate margin: NONE
dangerous impostor pairs: NOT_MEASURED
per-person results: NOT_MEASURED
per-condition results: NOT_MEASURED
per-camera results: NOT_MEASURED
held-out result: NO_HELD_OUT_DATA
IDENTITY_CALIBRATED=false

PAD DATASET
live sessions: 0
attack sessions: 0
devices: 0
source identities: 0
cameras: []
duplicates: 0
missing categories: phone_photo, phone_video, laptop_photo, laptop_video, print
missing live conditions: front, left, right, far, low_light
second-camera coverage: ABSENT
held-out split: ABSENT
PAD_DATASET_COMPLETE=false
PAD_VALIDATED=false

PAD BENCHMARK
MiniFAS: NOT_RUN
FS-VFM: NOT_RUN
anti-spoof-mn3: NOT_RUN
APCER/BPCER/ACER/failed rate/avg/p50/p95 latency: NOT_MEASURED
per attack/live condition/camera: NOT_MEASURED

BEST PAD: NONE

PRODUCTION_THRESHOLDS_CHANGED=false
FSVFM_ENFORCEMENT_ALLOWED=false
PRODUCTION_READY=false
```

Audit evidence:

- [Recognition audit](../backend/recognition_validation/field_phase7/audit_initial.json)
- [PAD audit](../backend/pad_validation/field_phase7/audit_initial.json)

Production code, config и model weights не редактировались.
