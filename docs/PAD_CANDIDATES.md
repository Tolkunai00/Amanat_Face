# PAD candidate discovery

Discovery date: 2026-10-08. A candidate enters the benchmark only when its
source, immutable checkpoint, preprocessing, class semantics, and license are
all identifiable. Repository licenses are not treated as legal approval for an
Amanat deployment.

## License gate

| Candidate | Source/code license | Artifact status | Commercial-use bucket | Benchmark decision |
|---|---|---|---|---|
| Open Model Zoo `anti-spoof-mn3` | Original model MIT; OMZ tooling Apache-2.0 | Official ONNX URL, size and checksum published | **COMMERCIAL_OK**, subject to organizational legal review | Included |
| MiniFASNetV2 | Upstream repository Apache-2.0 | Existing Amanat ONNX provenance is not yet pinned to an upstream release checksum | UNKNOWN for this artifact | Historical baseline only |
| FS-VFM ViT-S/16 FAS | Research repository/checkpoint; production rights not approved by Amanat | Existing pinned research checkpoint | RESEARCH_ONLY pending legal review | Historical baseline only |

No other discovered model was added: an attractive paper or repository alone
does not satisfy the checkpoint and preprocessing gates. Random weights and
unofficial conversions are explicitly rejected.

## anti-spoof-mn3 reproducibility record

| Field | Verified value |
|---|---|
| Name | `anti-spoof-mn3` (MobileNetV3 binary RGB anti-spoof classifier) |
| Paper context | [MobileNetV3](https://arxiv.org/abs/1905.02244); trained on [CelebA-Spoof](https://arxiv.org/abs/2007.12342) |
| Source repository | [`kprokofi/light-weight-face-anti-spoofing`](https://github.com/kprokofi/light-weight-face-anti-spoofing) |
| Distribution | [OpenVINO Open Model Zoo model card](https://github.com/openvinotoolkit/open_model_zoo/blob/master/models/public/anti-spoof-mn3/README.md) and [manifest](https://github.com/openvinotoolkit/open_model_zoo/blob/master/models/public/anti-spoof-mn3/model.yml) |
| Checkpoint source | [Official ONNX](https://storage.openvinotoolkit.org/repositories/open_model_zoo/public/2022.1/anti-spoof-mn3/anti-spoof-mn3.onnx) |
| Checkpoint size | 12,270,179 bytes |
| SHA-256 | `c4c99af04603b62d7e44f6f4daeb33e0daeccc696008c0b1d62f6f5cebbb3262` |
| Official manifest SHA-384 | `6de4534964b723397b3e8c995cadcf43bc007cc2f9930b95ae25f76adccece5d1d4d058d0b15117b9e4a9f758424f92a` |
| Framework | ONNX, ONNX Runtime CPU |
| Input | float32 NCHW `1x3x128x128` |
| Color | Original ONNX expects RGB; adapter converts OpenCV BGR to RGB |
| Normalization | `(pixel - [151.2405,119.5950,107.8395]) / [63.0105,56.4570,55.0035]` |
| Crop/alignment | Exact detector bbox, no alignment or bbox expansion |
| Resize | 128x128 cubic |
| Output | `1x2` probabilities; index 0 real, index 1 spoof; Amanat `live_score=output[0]` |
| License | [MIT for the original model](https://github.com/kprokofi/light-weight-face-anti-spoofing/blob/master/LICENSE) |
| PREPROCESSING_ASSUMPTION | none |

The strict load record is emitted into `pad_models_phase5.json`: no missing or
unexpected keys apply to ONNX, input/output shapes are validated, and inference
is rejected if the output is not a finite two-class probability vector.

## Reproduce

From `backend/`:

```bash
python -m scripts.fetch_pad_candidate anti_spoof_mn3
python -m scripts.benchmark_pad_models \
  --images pad_debug \
  --models anti_spoof_mn3 \
  --report pad_debug/pad_models_phase5.json \
  --csv pad_debug/pad_models_phase5.csv
```

`fetch_pad_candidate` accepts only reviewed catalog names, writes via a
temporary file, and verifies size, SHA-256 and the official SHA-384 before the
artifact replaces its destination. `deploy/models/` remains git-ignored.

## Scope warning

The benchmark above is screening evidence, not validation. The standardized
`backend/pad_validation` dataset is empty, and historical `pad_debug` lacks
laptop-video, print, cross-camera attack coverage and a held-out split.

`SCREENING_ONLY=true`  
`PAD_VALIDATED=false`  
`FSVFM_ENFORCEMENT_ALLOWED=false`
