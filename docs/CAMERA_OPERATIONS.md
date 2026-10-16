# Camera operations

Each configured camera has one reader thread, one recognition thread, and one
persistent watchdog thread. Recognition and attendance writes do not block
the preview. The recognition mailbox holds only the latest frame, so a slow
model cannot create a growing queue. Reconnect attempts reuse these threads.

## States

- `HEALTHY`: a recent non-stale frame exists and there are no current read
  failures.
- `DEGRADED`: the stream is reconnecting, has an intermittent failure, or its
  repeated-frame fingerprint is approaching the stale threshold.
- `OFFLINE`: no valid frame has ever arrived or the last valid frame is older
  than `CAMERA_OFFLINE_TIMEOUT_SECONDS`.

OpenCV RTSP open/read timeout properties remain enabled. The watchdog reports
a read exceeding `CAMERA_READ_MAX_SECONDS` and publishes a timeout image. Only
the reader releases and reconnects the capture after the read returns: calling
OpenCV `release()` concurrently with `read()` can deadlock or crash the process.
USB backends that ignore read timeouts can still require a device or process
restart. Frame staleness
uses a small sampled Adler-32 fingerprint rather than hashing the full image.
Fixed capture timestamps alone do not trigger reconnection if images change.
Stale history is reset for each new capture; retry backoff is reset only after
a valid frame, rather than immediately after opening a connection.

The web client retries failed MJPEG streams with a delay capped at 30 seconds,
reconnects when the network or tab returns, and closes streams when leaving the
camera page. Status polling never overlaps and times out after 10 seconds.
A 401 response ends the login session explicitly instead of silently polling.

Development Compose runs Uvicorn with `--reload`: editing Python source restarts
the backend and interrupts all camera streams. Use the production Compose
configuration for continuous operation. Frontend changes require rebuilding
the frontend container; backend source is bind-mounted in development.

Diagnostics are available at:

```text
GET /api/v1/health/cameras
GET /api/v1/metrics
```

Neither endpoint returns camera URLs or RTSP credentials.

## Soak test

Run headless from the backend environment:

```bash
python backend/scripts/camera_soak_test.py \
  --camera "$CAMERA_KELDI_SOURCE" --duration 1h \
  --report camera-soak.json --csv camera-soak.csv --device-info
```

Short modes accept `5m`, `15m`, or any positive seconds. A run passes only when
at least one frame succeeds and all configured failure-rate, reconnect,
offline-time, and p95 latency limits pass. `--pipeline` adds diagnostic face
detection/PAD timing but deliberately never writes attendance.

Common RTSP faults include invalid URL-encoded credentials, incorrect vendor
stream paths, UDP loss, exhausted camera session limits, clock/network loss,
and frozen encoders that repeat the same decoded frame. Check counters and last
timestamps before restarting services. Never paste credential-bearing RTSP URLs
into tickets or logs.

The soak script is diagnostic evidence only. A synthetic or file-replay test is
not a replacement for the required one-hour onsite run from each real camera.
