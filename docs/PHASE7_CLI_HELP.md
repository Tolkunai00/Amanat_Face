# Phase 7: exact CLI help

Captured from the installed Python environment, in backend, 2026-10-09.
All six help commands completed successfully. Output below is unchanged apart from line endings.

## python -m scripts.capture_recognition_dataset --help

```text
usage: capture_recognition_dataset.py [-h] --camera CAMERA
                                      [--camera-id CAMERA_ID] --person-id
                                      PERSON_ID --role {gallery,probe}
                                      --session-id SESSION_ID --condition
                                      {front,left,right,far,low_light}
                                      [--output OUTPUT]
                                      [--sample-interval SAMPLE_INTERVAL]
                                      [--max-images MAX_IMAGES]
                                      [--min-blur MIN_BLUR]
                                      [--min-brightness MIN_BRIGHTNESS]
                                      [--no-preview]

Capture a gallery or probe session without enrollment/attendance side effects.

options:
  -h, --help            show this help message and exit
  --camera CAMERA       Local index or RTSP/HTTP camera source
  --camera-id CAMERA_ID
                        Stable camera label; defaults to --camera
  --person-id PERSON_ID
  --role {gallery,probe}
  --session-id SESSION_ID
  --condition {front,left,right,far,low_light}
  --output OUTPUT
  --sample-interval SAMPLE_INTERVAL
  --max-images MAX_IMAGES
                        0 means until operator quits
  --min-blur MIN_BLUR
  --min-brightness MIN_BRIGHTNESS
  --no-preview
```

## python -m scripts.recognition_dataset_audit --help

```text
usage: recognition_dataset_audit.py [-h] [--dataset DATASET]
                                    [--manifest MANIFEST] [--report REPORT]
                                    [--metadata METADATA]

Audit recognition collection coverage and gallery/probe leakage.

options:
  -h, --help           show this help message and exit
  --dataset DATASET
  --manifest MANIFEST
  --report REPORT
  --metadata METADATA
```

## python -m scripts.capture_pad_dataset --help

```text
usage: capture_pad_dataset.py [-h] --camera CAMERA [--camera-id CAMERA_ID]
                              --label {live,spoof} [--person-id PERSON_ID]
                              [--attack-type {phone_photo,phone_video,laptop_photo,laptop_video,print}]
                              --session-id SESSION_ID
                              [--condition {front,left,right,far,low_light}]
                              [--source-identity SOURCE_IDENTITY]
                              [--display-or-print-device DISPLAY_OR_PRINT_DEVICE]
                              [--device-type DEVICE_TYPE]
                              [--screen-brightness SCREEN_BRIGHTNESS]
                              [--paper-type PAPER_TYPE]
                              [--print-size PRINT_SIZE] [--notes NOTES]
                              [--split {development,test}] [--output OUTPUT]
                              [--sample-interval SAMPLE_INTERVAL]
                              [--max-images MAX_IMAGES] [--min-blur MIN_BLUR]
                              [--min-brightness MIN_BRIGHTNESS] [--no-preview]

Capture standardized live and presentation-attack PAD evidence.

options:
  -h, --help            show this help message and exit
  --camera CAMERA       Local index or RTSP/HTTP camera source
  --camera-id CAMERA_ID
                        Stable camera label; defaults to --camera
  --label {live,spoof}
  --person-id PERSON_ID
                        Required for live captures
  --attack-type {phone_photo,phone_video,laptop_photo,laptop_video,print}
                        Required for spoof captures
  --session-id SESSION_ID
  --condition {front,left,right,far,low_light}
                        Required for live captures
  --source-identity SOURCE_IDENTITY
                        Required for spoof captures
  --display-or-print-device DISPLAY_OR_PRINT_DEVICE
                        Stable physical device ID; required for spoof
  --device-type DEVICE_TYPE
  --screen-brightness SCREEN_BRIGHTNESS
                        Manual value only; omit when unknown
  --paper-type PAPER_TYPE
                        Manual value only; omit when unknown
  --print-size PRINT_SIZE
                        Manual value only; omit when unknown
  --notes NOTES
  --split {development,test}
  --output OUTPUT
  --sample-interval SAMPLE_INTERVAL
                        Seconds between saved samples; frames remain one
                        attack-session evidence group
  --max-images MAX_IMAGES
                        0 means until operator quits
  --min-blur MIN_BLUR
  --min-brightness MIN_BRIGHTNESS
  --no-preview
```

## python -m scripts.pad_dataset_audit --help

```text
usage: pad_dataset_audit.py [-h] [--dataset DATASET] [--report REPORT]

Audit PAD attack-session coverage without decoding biometric media.

options:
  -h, --help         show this help message and exit
  --dataset DATASET
  --report REPORT
```

## python -m scripts.calibrate_recognition --help

```text
usage: calibrate_recognition.py [-h] [--dataset DATASET] [--csv CSV]
                                [--json JSON] [--threshold THRESHOLD]
                                [--margin MARGIN] [--manifest MANIFEST]
                                [--phase {calibration,test}]

Calibrate ArcFace identity decisions on a separate, read-only dataset. This
command emits candidates only. It never imports or writes production env
files, thresholds, enrollment embeddings, the database, or attendance records.

options:
  -h, --help            show this help message and exit
  --dataset DATASET
  --csv CSV
  --json JSON
  --threshold THRESHOLD
                        Required for held-out test; selected on calibration
                        data
  --margin MARGIN       Required for held-out test; selected on calibration
                        data
  --manifest MANIFEST
  --phase {calibration,test}
```

## python -m scripts.benchmark_pad_models --help

```text
usage: benchmark_pad_models.py [-h] --images IMAGES
                               [--models {minifas,fsvfm,anti_spoof_mn3} [{minifas,fsvfm,anti_spoof_mn3} ...]]
                               [--provider PROVIDER]
                               [--minifas-checkpoint MINIFAS_CHECKPOINT]
                               [--fsvfm-checkpoint FSVFM_CHECKPOINT]
                               [--anti-spoof-mn3-checkpoint ANTI_SPOOF_MN3_CHECKPOINT]
                               [--device {cpu,cuda}]
                               [--phase {calibration,test}]
                               [--threshold THRESHOLD] --report REPORT --csv
                               CSV
                               [--video-sample-seconds VIDEO_SAMPLE_SECONDS]
                               [--max-frames-per-video MAX_FRAMES_PER_VIDEO]

Compare diagnostic PAD providers on identical group-aware evidence.

options:
  -h, --help            show this help message and exit
  --images IMAGES
  --models {minifas,fsvfm,anti_spoof_mn3} [{minifas,fsvfm,anti_spoof_mn3} ...]
  --provider PROVIDER   Explicit future adapter module:factory
  --minifas-checkpoint MINIFAS_CHECKPOINT
  --fsvfm-checkpoint FSVFM_CHECKPOINT
  --anti-spoof-mn3-checkpoint ANTI_SPOOF_MN3_CHECKPOINT
  --device {cpu,cuda}
  --phase {calibration,test}
  --threshold THRESHOLD
                        model=value; mandatory for held-out test
  --report REPORT
  --csv CSV
  --video-sample-seconds VIDEO_SAMPLE_SECONDS
  --max-frames-per-video MAX_FRAMES_PER_VIDEO
```

