# Production readiness

## Recognition enrollment

- Enroll 3-5 independent photos per student from slightly different angles.
- The API rejects unreadable images, blur, under/overexposure, multiple faces,
  very small faces, malformed vectors and zero/non-finite embeddings.
- A student is not `recognition_ready` until at least three valid samples exist.
- Use the student card to add new samples when registration quality is poor.
- Run `python backend/scripts/evaluate_recognition_threshold.py` only after the
  gallery has same-student and different-student pairs. Confirm the selected
  threshold on separate real entrance-camera captures.

## Presentation-attack protection

Temporal head movement is only a usability heuristic. It is not a certified
anti-spoofing system and must not be represented as protection from a printed
photo, screen replay or generated video. The camera pipeline now has a
fail-closed ONNX PAD gate after temporal liveness. Select and license a real PAD
model, confirm that its RGB/NCHW preprocessing and output contract match the
adapter, and calibrate its threshold against the actual cameras.
`PRODUCTION=1` refuses startup while `ANTI_SPOOFING_MODEL_PATH` is empty, and
startup fails if the configured ONNX model cannot be loaded.

## InsightFace model license

InsightFace library code and pretrained weights have different terms. The code
is MIT, while public pretrained packages such as `buffalo_l` are restricted to
non-commercial research unless separately licensed. Obtain written production
authorization or deploy your own appropriately licensed compatible ONNX model.
After verification, set `FACE_MODEL_LICENSE=commercial`; this flag records an
operator decision and does not itself grant a license.

Official references:

- https://github.com/deepinsight/insightface/blob/master/python-package/docs/model_zoo.md
- https://github.com/deepinsight/insightface/blob/master/server/LICENSING.md
- https://www.insightface.ai/

## Release gate

Before enabling `PRODUCTION=1`:

1. Cameras stay in `working` state during a representative full school day.
2. Every active student has 3-5 accepted samples.
3. FAR/FRR and recognition threshold are measured on held-out local captures.
4. A licensed PAD model passes printed-photo and replay-video tests.
5. The recognition model has written commercial authorization.
6. A recent PostgreSQL dump has a `verified` entry and an off-host copy.
7. Attendance schedules, shifts and holidays are configured.
8. Admin users verify CSV/XLSX reports and reasoned corrections.
