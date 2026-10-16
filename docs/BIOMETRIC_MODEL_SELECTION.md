# Biometric model-selection report

Validation date: 2026-10-08. Phase 5 changed no production thresholds, PAD
decision code, attendance behavior, or FS-VFM shadow-only policy.

## Identity

`backend/recognition_validation` still contains no validation images.
`IDENTITY_CALIBRATED=false` and status is `BLOCKED_DATASET`.

## PAD evidence scope

The standardized `backend/pad_validation` audit found zero media and zero
sessions. Every required category (`phone_photo`, `phone_video`,
`laptop_photo`, `laptop_video`, `print`) is `NOT_TESTED`, so
`PAD_DATASET_COMPLETE=false`.

The Phase 5 run uses historical `backend/pad_debug` only: 225 raw sources, 164
unique sources, 61 duplicates removed, and 159 evidence groups. Of those groups,
146 were evaluable. Video frames were aggregated by evidence group rather than
counted as independent attacks. `SCREENING_ONLY=true`; laptop-video, print,
cross-camera attack coverage and a held-out split remain absent.

## Model comparison

| Model | License | Checkpoint | Input | APCER | BPCER | ACER | Failed | CPU avg | Worst tested attack | Verdict |
|---|---|---|---|---:|---:|---:|---:|---:|---|---|
| MiniFASNetV2 | artifact license/provenance unresolved | historical ONNX | 80x80 BGR, 2.7 bbox | 0.00% | 100.00% | 50.00% | 8.18% | 40.21 ms | not meaningful: all live rejected | **FAIL** |
| FS-VFM ViT-S/16 | research baseline | historical `.pth.tar` | 224x224 RGB, 1.74 bbox | 54.17% | 5.41% | 29.79% | 8.18% | 1276.28 ms | phone_video 100% APCER | **FAIL** |
| anti-spoof-mn3 | COMMERCIAL_OK (MIT; legal sign-off still required) | official ONNX, SHA-256 `c4c99a...b3262` | 128x128 RGB, exact bbox | 80.56% | 6.76% | 43.66% | 8.18% | 29.25 ms | laptop_photo 88.00% APCER | **FAIL** |

anti-spoof-mn3 latency p50/p95 was 26.79/46.34 ms. At threshold 0.50 its
per-attack APCER was phone_photo 80.00%, phone_video 0.00% (only two evidence
groups), and laptop_photo 88.00%; laptop_video and print were `NOT_TESTED`.
Live BPCER by condition was front 15.00%, left 0%, right 0%, far 7.69%, and
low_light 8.33% among evaluable groups. These are measured values, not FPS.

## Threshold sweep

The calibration-only sweep found minimum ACER at threshold 0.995:
APCER 43.06%, BPCER 18.92%, ACER 30.99%. This remains a poor screening result,
was selected on calibration evidence, and cannot be evaluated as a held-out
candidate because no final test split exists. The sweep is retained in
`backend/pad_debug/pad_models_phase5.json`; it did not write any production
setting.

## Selection decision

Selection priority is worst-case APCER, overall APCER, BPCER, failure rate,
latency, license, then cross-camera stability. anti-spoof-mn3 is the first new
candidate with a reproducible public checkpoint and a complete adapter, but its
80.56% overall APCER and 88% worst tested attack APCER fail screening. It is not
a `SCREENING_CANDIDATE` merely because it is faster or legally clearer.

`BEST_PAD_CANDIDATE=NONE`  
`PAD_VALIDATED=false`  
`FSVFM_ENFORCEMENT_ALLOWED=false`  
`PRODUCTION_THRESHOLDS_CHANGED=false`  
`PRODUCTION_READY=false`

## Remaining gates

- populate group-aware development and held-out `pad_validation` splits;
- collect laptop-video and print attacks;
- collect attack coverage on multiple cameras/devices/identities;
- select any threshold only on development, then lock it before held-out test;
- obtain formal organizational legal approval for the selected artifact;
- find a model whose worst-case attacks pass an explicitly approved risk limit.
