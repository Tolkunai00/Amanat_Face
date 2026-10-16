# Biometric validation

Validation date: 2026-10-08. These are offline measurements, not production
configuration. `RECOGNITION_THRESHOLD`, `RECOGNITION_MARGIN`, enrollment data,
PAD threshold, attendance behavior and `FSVFM_SHADOW_MODE=true` were not changed.

## 1. Dataset

Identity dataset status: **BLOCKED_DATASET**. The required independent
`recognition_validation/` dataset is absent, so no identity result is inferred
from enrollments or from PAD captures.

PAD used `backend/pad_debug` with the locally pinned FS-VFM checkpoint. It found
225 source files, retained 164 unique sources, and removed 61 exact SHA-256 or
copied-name duplicates. Video sampling and still captures yielded 190 processed
frame records, aggregated into 159 independent capture/video evidence groups.
Thirteen groups had no evaluable face (group-aware failed detection rate 8.18%).

## 2. Identity protocol

Run:

```bash
python backend/scripts/calibrate_recognition.py \
  --dataset recognition_validation \
  --csv tmp/recognition_calibration.csv \
  --json tmp/recognition_calibration.json
```

The evaluator uses the runtime accurate InsightFace/ArcFace detector, enrollment
quality checks and L2-normalized embedding path. Preferred layout is
`person/gallery/<session>/...` plus `person/probe/<different-session>/...`.
Declared gallery/probe folders are never mixed. For the simple `person/*.jpg`
layout, the fallback is deterministic capture-group splitting, never random
image splitting, and the summary warns that session leakage must be reviewed.

Each probe is compared to each identity's gallery. The report preserves top-1,
top-2, margin, expected/predicted identity, genuine/impostor result, all
different-person scores, ROC, FAR/FRR/EER sweeps, per-person behavior and
low-light/far/left/right behavior. A gallery file cannot be its own probe.

## 3. PAD protocol

Run used:

```bash
python backend/scripts/calibrate_pad.py \
  --dataset backend/pad_debug \
  --checkpoint deploy/models/fsvfm_vit_s_oci2m_run0.pth.tar \
  --csv tmp/pad_calibration.csv \
  --json tmp/pad_calibration.json
```

The evaluator reuses the existing FS-VFM diagnostic checkpoint loader,
face detector, official preprocessing adaptation and class mapping. Videos are
sampled at bounded intervals. Correlated frames are not independent trials: one
video/capture group contributes one worst-case score (maximum live score for an
attack; minimum live score for bona-fide data).

## 4. Duplicate control

Before model inference, files are grouped by exact SHA-256 and normalized copied
filenames, including `name (1).jpg`. Exact/copy duplicates are excluded from
independent evidence.

| Raw sources | Unique sources | Removed |
|---:|---:|---:|
| 225 | 164 | 61 |

The JSON report lists every removed file, reason, original file and SHA-256.

## 5. Recognition metrics

No separate validation dataset exists.

| Metric | Result |
|---|---|
| Genuine count | NOT_TESTED |
| Impostor count | NOT_TESTED |
| FAR | NOT_TESTED |
| FRR | NOT_TESTED |
| EER | NOT_TESTED |
| Identity calibrated | **false** |

At least three represented identities, independent gallery/probe captures,
genuine coverage per person and sufficient different-person comparisons are
required before `IDENTITY_CALIBRATED` can become true.

## 6. PAD metrics

Metrics below use the configured diagnostic threshold 0.50 and group-aware
worst-case evidence, not raw-frame independence.

| Metric | Result |
|---|---:|
| Live groups evaluated | 74 |
| Spoof groups evaluated | 72 |
| APCER | 54.17% |
| BPCER | 5.41% |
| ACER | 29.79% |
| EER-like value | 19.91% |
| EER-like threshold | 0.995 |
| Score overlap | yes |
| Failed evidence groups | 13 / 159 (8.18%) |

The EER-like threshold is a diagnostic sweep point, not a production candidate:
the distributions overlap heavily and performance is unacceptable for enforcement.

## 7. Per-person weaknesses

Identity per-person behavior is **NOT_TESTED** because the recognition dataset is
missing. PAD data is multi-person, but PAD acceptance is assessed by live
condition and attack type; it must not be used as an identity calibration set.

## 8. Per-condition weaknesses

| Live condition | Evaluated groups | BPCER | Worst live score |
|---|---:|---:|---:|
| front | 20 | 0.00% | 0.6053 |
| left | 15 | 6.67% | 0.00079 |
| right | 14 | 0.00% | 0.9820 |
| far | 13 | 7.69% | 0.00009 |
| low_light | 12 | 16.67% | 0.00020 |

Low light is the weakest evaluated live condition. Far and left captures also
contain severe low-score failures.

## 9. Per-attack weaknesses

| Attack | Status | Evaluated groups | APCER | Worst live score |
|---|---|---:|---:|---:|
| phone_photo | EVALUATED | 45 | 44.44% | 0.99920 |
| phone_video | EVALUATED | 2 | 100.00% | 0.99986 |
| laptop_photo | EVALUATED | 25 | 68.00% | 0.99995 |
| laptop_video | NOT_TESTED | 0 | — | — |
| print | NOT_TESTED | 0 | — | — |

Phone-video failure is complete on the small available sample. Laptop and phone
photo APCER are also high. Missing laptop-video and print coverage independently
forces `PAD_VALIDATED=false`.

## 10. Threshold candidates

Identity threshold and margin candidates: **NOT_TESTED**. No values are proposed
without an independent identity dataset.

The PAD sweep is retained in `tmp/pad_calibration.json`. No PAD threshold is
recommended for enforcement: changing the operating point cannot repair the
observed attack overlap, 100% phone-video APCER, missing categories or detection
failures. Threshold candidates are never written to `.env.prod.example`,
`.env.prod`, settings or Compose.

## 11. Remaining gaps

- independent recognition gallery/probe dataset with session metadata;
- sufficient different-person and similar-looking-student coverage;
- identity low-light, far and angle probes;
- laptop-video and print attacks;
- more independent phone-video sessions;
- investigation of laptop/phone photo false accepts;
- investigation of PAD face-detection failures;
- held-out validation after any future model/preprocessing change;
- documented organizational APCER/BPCER/failed-detection acceptance limits.

## 12. Production recommendation

`IDENTITY_CALIBRATED=false`.

`PAD_VALIDATED=false`.

`FSVFM_ENFORCEMENT_ALLOWED=false`.

Keep FS-VFM shadow-only. Do not change production recognition or PAD thresholds
from this run. `PRODUCTION_READY=false`.
