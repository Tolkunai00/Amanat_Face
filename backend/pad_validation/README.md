# PAD validation dataset (data is not committed)

```text
development/
  live/personX/camera1/session01/front/*.jpg
  spoof/phone_photo/attack_session_001/*
  spoof/phone_video/attack_session_002/*
  spoof/laptop_photo/attack_session_003/*
  spoof/laptop_video/attack_session_004/*
  spoof/print/attack_session_005/*
test/
  ...same layout with disjoint attack sessions/devices/sources...
```

Every spoof session directory must contain `metadata.json`:

```json
{
  "attack_session_id": "attack_session_001",
  "attack_type": "phone_photo",
  "source_identity": "personX",
  "display_or_print_device": "inventory-device-id",
  "camera_id": "camera1",
  "capture_time": "2026-10-08T10:00:00.000Z",
  "notes": null,
  "device_type": null,
  "screen_brightness": null,
  "paper_type": null,
  "print_size": null
}
```

Development and test must be split by attack session, device and source—not by
frames. Images, videos, manifests and device identifiers are private runtime data
and must not be committed.
