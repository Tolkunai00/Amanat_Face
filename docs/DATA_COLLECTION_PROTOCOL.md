# Amanat Face data collection protocol

This protocol governs Phase 6 validation data. The datasets are evidence, not
training data. Do not train or fine-tune any model on `development` or `test`,
and never copy held-out samples into calibration. Production thresholds and PAD
enforcement remain unchanged until a human security owner approves acceptance
limits.

## Recognition

### Gallery

Capture `front`, `left`, and `right` in one or more declared gallery sessions.
Gallery capture stores images and metadata only; it does not enroll a student or
write attendance. Use an opaque `person-id`, not a name.

```powershell
python backend/scripts/capture_recognition_dataset.py --camera 0 --camera-id camera_1 `
  --person-id person001 --role gallery --session-id gallery_20261008_a `
  --condition front --output backend/recognition_validation/development
```

### Probe

Capture `front`, `left`, `right`, `far`, and `low_light`. A probe session must be
a physically separate capture session with a new session ID. Probe coverage on
both cameras is desirable.

### Sessions and leakage prevention

Gallery and probe may not share a session ID, file, SHA256, normalized copied
filename, or burst sequence ID. Development/calibration and held-out test data
must also be collected as separate groups. Exact duplicate bytes are excluded
from metric inputs and reported as `DATASET_PROTOCOL_FAIL`; files are not
silently deleted. A person without a probe session is `NOT_VALIDATABLE`.

Run the audit before calibration:

```powershell
python backend/scripts/recognition_dataset_audit.py `
  --dataset backend/recognition_validation `
  --manifest backend/recognition_validation/manifest.csv `
  --report backend/recognition_validation/audit.json
```

Select threshold and margin only with `--phase calibration`. A held-out run
refuses to start without explicit values selected on calibration data:

```powershell
python backend/scripts/calibrate_recognition.py --dataset backend/recognition_validation `
  --phase test --threshold APPROVED_CALIBRATION_VALUE --margin APPROVED_CALIBRATION_VALUE `
  --manifest backend/recognition_validation/manifest.csv `
  --csv backend/recognition_validation/test_results.csv `
  --json backend/recognition_validation/test_results.json
```

### Conditions and cameras

`far` and `low_light` are retained whenever exactly one real face is detectable
and the frame is not unusably dark or severely blurred. Identity similarity is
never a capture quality gate. Record stable physical camera labels such as
`camera_1` and `camera_2`, rather than changing URLs or local device numbers.

## PAD

### Live

For every person/camera/session, collect `front`, `left`, `right`, `far`, and
`low_light` under `live/<person>/<camera>/<session>/<condition>`.

### Presentation attacks

Collect each category separately: `phone_photo`, `phone_video`,
`laptop_photo`, `laptop_video`, and `print`. Every physical replay or print run
has a new attack session ID and one `metadata.json`. Supply the real inventory
device ID and source identity. Leave unknown screen brightness, paper type, and
print size empty; never estimate them.

```powershell
python backend/scripts/capture_pad_dataset.py --camera 0 --camera-id camera_1 `
  --label spoof --attack-type phone_video --session-id attack_session_001 `
  --source-identity person001 --display-or-print-device phone_inventory_01 `
  --sample-interval 1.0 --split development --output backend/pad_validation
```

Video frames are sampled at the configured interval. All sampled frames from a
single replay keep one `attack_session_id` and are aggregated as one evidence
group. Twenty sessions from one phone are twenty sessions but only one device.
Each attack type should be captured on both cameras.

Run the audit, then the common three-model benchmark only on standardized data:

```powershell
python backend/scripts/pad_dataset_audit.py --dataset backend/pad_validation `
  --report backend/pad_validation/audit.json
python backend/scripts/benchmark_pad_models.py --images backend/pad_validation `
  --models minifas fsvfm anti_spoof_mn3 `
  --report backend/pad_validation/benchmark.json `
  --csv backend/pad_validation/benchmark.csv
```

Historical `pad_debug` results are screening evidence only and cannot produce a
production verdict. Do not download more PAD models during this phase.

## Operator checklist

- Confirm participant consent and use opaque identity IDs.
- Confirm the selected split, role/label, camera ID, and new session ID aloud.
- Clean the camera lens and verify the camera clock/network before capture.
- Keep gallery and probe physically separate; do not rename or copy frames.
- Capture all required conditions, including legitimate `far` and `low_light`.
- Ensure only one face is visible; investigate repeated blur/darkness rejects.
- For PAD, record the actual device inventory ID and source identity.
- For video replay, keep one attack session and use sampled frames.
- For print, record paper type and size only when known.
- Repeat every attack category on the second camera when available.
- Run both audits and resolve every protocol violation before metrics.
- Preserve held-out data; never use it to choose threshold, margin, or model.
- Verify production configuration and PAD enforcement remain unchanged.
