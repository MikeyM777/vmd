# VMD — Video Motion Detection with AI Verification

**Date:** 2026-08-06
**Status:** Approved design

## 1. Purpose

A desktop video-surveillance analytic that watches 4 RTSP camera streams on a
regular laptop and raises an alarm when a **person** or **vehicle** appears.

Two goals dominate every design decision:

1. **Reliability** — the system runs unattended for days; a broken stream, a
   failed GPU backend, or a full disk must not stop the other cameras.
2. **Minimum false alarms** — an operator who gets nuisance alarms stops
   trusting the system. Every alarm passes a chain of independent gates.

## 2. Operating context

| Item | Value |
|---|---|
| Video source | 4× RTSP/ONVIF IP cameras. Also a file-input mode for testing on recorded clips. |
| Resolution / fps | Not yet fixed. Design assumes 1080p–4K, 15–25 fps, and adapts at runtime. |
| Scene | Outdoor, 24/7, including night with street lighting. |
| Target size | Person at **30–100 m** — roughly 15–40 px tall at 1080p. This is the hardest constraint and drives the crop-based detection design. |
| Camera motion | Fixed today. **PTZ in the deployed system** (phase 8). |
| Modality | Visible today. **Thermal added later** (phase 8) — architecture keeps model and thresholds per-camera so a thermal camera is a config change plus a model, not a rewrite. |
| Alarm output | Local desktop app: live view, on-screen alarm, sound, saved snapshot and clip. No remote notification in scope. |
| Alarm rule | Any person or vehicle anywhere in frame. No zones or tripwires. (Ignore-masks exist only to suppress known nuisance regions.) |

### Target hardware

Both machines must run the same codebase:

* **Machine A** — Dell, i7-1165G7 2.8 GHz, Iris Xe graphics, 16 GB RAM.
* **Machine B** — laptop with RTX 3060, 32 GB RAM.

Comparing the two is an explicit project goal.

**Virtual machines are not used for the comparison.** GPU access inside a VM
defeats the measurement: Iris Xe has no usable passthrough under
Hyper-V/VirtualBox, so OpenVINO falls back to CPU, and passing a laptop RTX 3060
through to a guest is unreliable on muxless Optimus hardware. Both VMs would end
up CPU-only and the benchmark would measure nothing real. Instead the same repo
runs **natively** on each laptop, and `--bench` produces a comparable CSV.
Environment reproducibility comes from a `uv` virtualenv with pinned
requirements. Docker with `--gpus all` is an option later on Machine B only.

## 3. Approach

### Rejected: full-frame detection every frame

Running a detector over every full frame of 4 streams is 100 inferences/sec,
which Iris Xe cannot sustain. Worse, at a 640 px detector input a person at
100 m shrinks to a handful of pixels and is simply not detectable.

### Chosen: motion-gated detection, with an optional tiled sweep

```
RTSP decode (analysis path throttled to 5-8 fps)
  -> motion gate: background subtraction on 1/4-scale grayscale (CPU, cheap)
  -> blobs -> crop each ROI from the FULL-RESOLUTION frame
  -> detector runs ONLY on crops, at native resolution
  -> tracker + N-of-M confirmation
  -> alarm state machine -> UI, sound, snapshot, clip, SQLite event
```

Why this shape:

* **Cost** — a quiet scene costs almost no inference, so 4 streams fit on Iris Xe.
* **Small targets** — a 20 px person inside a 100 px crop, upscaled to the
  detector's input size, is a normal-sized object to the model. This is the
  single largest accuracy gain available for 30–100 m targets.
* **False alarms** — motion alone fires on trees, rain, headlights and insects.
  Here a blob only becomes an alarm if the detector also calls it person or
  vehicle *and* the track survives. Two independent gates, different failure
  modes.

**Optional tiled sweep (SAHI-style):** the full frame is sliced into overlapping
tiles and each tile is detected, catching a target that is static or too slow
for the motion gate. It costs roughly an order of magnitude more compute, so it
runs on a timer (every N seconds) and only under the GPU profile.

**Profiles.** One codebase, one config key:

* `profile: cpu` — motion gate only. Default on Machine A.
* `profile: gpu` — motion gate plus periodic tiled sweep. Default on Machine B.
* `profile: auto` — detect hardware at startup and choose. User can override.

### Stack

Python 3.11, OpenCV + FFmpeg for decode, ONNX Runtime for inference
(OpenVINO execution provider on Iris Xe, CUDA/TensorRT on the 3060, CPU
fallback), ByteTrack for tracking, PySide6 for the UI, SQLite for events.

## 4. Architecture

```
main process (PySide6 UI)
 |- CameraWorker x4 (one thread each)
 |    RTSP reader -> ring buffer (drop old frames, never queue up)
 |    MotionGate (background sub on 1/4-scale gray, morphology, blob filter)
 |    -> ROI crops
 |- InferenceService (single shared worker thread, batches crops)
 |    ONNX Runtime session; EP = OpenVINO | CUDA | CPU
 |- Tracker + AlarmStateMachine (per camera)
 |- EventStore (SQLite + jpg snapshot + pre/post mp4 clip)
```

Decisions:

* **One shared inference service**, not one per camera. The accelerator stays
  busy, and the model is loaded once instead of four times.
* **Drop-frame policy.** The reader never blocks. If the pipeline falls behind,
  analysis frames are dropped so the system stays on live video rather than
  drifting into the past.
* **Threads, not processes.** A dead RTSP stream is contained by
  reconnect-with-backoff inside its own worker; process isolation buys little
  here and complicates frame sharing.
* **The UI is downstream of everything.** No analysis module imports UI code.

## 5. False-alarm suppression chain

An alarm must pass every gate, in order. Each gate's decision is recorded on the
event.

| # | Gate | What it removes |
|---|---|---|
| 1 | Blob geometry — min/max area, aspect ratio, size-versus-position sanity | insects on the lens, rain streaks, whole-frame lighting shifts |
| 2 | Ignore mask — per-camera polygons drawn by the operator | permanent nuisance movers (road, tree line, flag) |
| 3 | Global-motion check — if more than 40 % of the frame moves at once, alarms are suppressed and the background model is reset | camera shake, PTZ movement, IR day/night switch, headlight wash, lights on/off |
| 4 | AI classification — the crop must be classified `person` or `vehicle` at or above a per-class, per-camera confidence threshold | animals, shadows, foliage, reflections |
| 5 | Track N-of-M — the object must be classified in **K of the last M** frames on the same track (default 3 of 5) | single-frame flukes, detection flicker |
| 6 | Track age and displacement — the track must live at least T ms and either move at least D px or be newly appearing | static-object misdetections, parked objects re-detected |
| 7 | Cooldown and dedup — one alarm per track, plus a per-camera cooldown of N seconds | alarm spam from a single intruder |

Two controls are exposed to the operator: a **sensitivity preset**
(Low / Normal / High) that maps to a bundle of the thresholds above, and a
per-camera confidence slider. Everything else lives in `config.yaml`.

**Gate-decision logging.** Every candidate — accepted or rejected — records why:
`rejected at gate 4: class=dog conf=0.71`. Tuning is then evidence-driven rather
than guesswork, and the event view can show the reasoning for any alarm.

## 6. Modules

```
vmd/
  config.py        load and validate config.yaml (pydantic), profile resolution
  sources/
    rtsp.py        RtspReader: reconnect with backoff, drop-frame ring buffer
    file.py        FileReader: mp4 playback, real-time or as-fast-as-possible
  motion/
    gate.py        background subtraction, morphology, blob extraction
    filters.py     geometry filter, ignore mask, global-motion detection
  detect/
    backend.py     ONNX Runtime session factory, execution-provider selection
    detector.py    crop preprocessing, batched inference, NMS, class mapping
    tiler.py       tiled full-frame sweep (gpu profile only)
  track/
    tracker.py     ByteTrack wrapper, one instance per camera
    alarm.py       N-of-M state machine, cooldown, gate-decision log
  storage/
    events.py      SQLite writer, snapshot jpg, pre/post-roll mp4 clip, retention
  ui/
    main_window.py 2x2 live grid, per-camera status, alarm banner and sound
    events_view.py event list, snapshot and clip playback, gate log
    mask_editor.py draw ignore polygons per camera
  bench/
    runner.py      --bench headless mode, CSV metrics
  app.py           wiring, thread lifecycle, shutdown
```

Each module has one job and a narrow interface: the motion gate turns frames
into blobs, the detector turns crops into detections, the alarm machine turns
detections into events. Each is testable without the others and without a
camera.

## 7. Data

**`config.yaml`**

* `cameras[]`: name, rtsp_url, enabled, sensitivity preset, classes to alarm on,
  confidence thresholds, ignore_mask polygons, analysis fps, crop scale.
* `profile`: `auto` | `cpu` | `gpu`.
* `model`: path per modality, input size, class map.
* `storage`: db path, clips dir, retention days, retention max GB.
* `alarm`: sound file, cooldown seconds, auto-clear seconds.

**SQLite `events` table**

`id, camera, ts_start, ts_end, class, max_conf, track_id, bbox,
snapshot_path, clip_path, gates_log (json), acknowledged`

**Retention:** clips are kept for N days or up to a maximum total size,
whichever binds first; oldest are deleted first. Default 14 days. Event rows
survive clip deletion.

## 8. User interface

Single PySide6 window.

* **Live grid 2×2.** Each tile shows the stream with overlays — green motion
  blobs, red confirmed detections with class and confidence — plus a health
  badge (live / reconnecting / dead) and current fps.
* **Alarm.** The tile border flashes red, a configurable WAV plays, and a banner
  reads `PERSON — Cam 2 — 21:14:07` with an **Acknowledge** button. The alarm
  latches until acknowledged or auto-clears after the configured interval.
* **Events panel.** Newest first: thumbnail, camera, class, confidence, time.
  Clicking opens the snapshot and plays the clip; expanding shows the
  gate-decision log.
* **Per-camera settings dialog.** Sensitivity preset, class toggles, confidence
  slider, ignore-mask drawing on a live snapshot, and a test-fire button.
* **Debug view** (off by default). Shows the motion mask, crop rectangles, and
  rejected blobs annotated with their rejection reason. This is the primary tool
  for tuning 30–100 m detection.
* **Status bar.** Active profile (CPU / OpenVINO / CUDA), inference ms, total
  fps, dropped frames, CPU / GPU / RAM usage.

**File-input mode** uses the same window and the same pipeline against a
recorded video, so tuning done on a recording transfers directly to live
operation.

## 9. Failure handling

* **RTSP drop** — reconnect with exponential backoff (1 s to 30 s). The tile
  shows "reconnecting"; other cameras are unaffected. A stream dead for more
  than 60 s raises its own alert, since that indicates camera failure or
  tampering.
* **Decode errors** — the frame is skipped and counted; an error rate above
  50 % sustained for 30 s forces a reconnect.
* **Inference backend init failure** — logged prominently, fall back to CPU, and
  the UI shows a "running degraded" banner rather than failing silently.
* **Pipeline overload** — analysis frames are dropped first so the live view
  stays smooth; effective analysis fps is logged and shown.
* **Disk pressure** — retention purge runs on a timer and on write failure;
  oldest clips go first, event rows remain.
* **Crash recovery** — config and events live on disk; restarting the app
  restores all cameras and history.

## 10. Testing

* **Unit tests** — motion gate against synthetic frames containing a moving
  rectangle; geometry and mask filters; the N-of-M state machine driven by
  fabricated detection sequences (fully deterministic, no video required);
  retention purge; config validation.
* **Integration tests** — file source through the complete pipeline over a
  recorded clip, asserting event count and timing against a hand-labelled
  ground-truth JSON.
* **Regression corpus** — a directory of short clips, half true positives (a
  person walking at range, day and night) and half nuisance footage (empty
  scene, moving trees, headlights, rain). A script reports true positives, false
  positives and misses. Every tuning change is measured against this corpus.
  This is the mechanism by which "fewest false alarms" becomes measurable rather
  than a matter of opinion.

## 11. Benchmarking the two laptops

`--bench` runs headless over the regression corpus and writes a CSV:
fps per stream, inference latency p50 and p95, CPU %, RAM, GPU %, and
TP / FP / miss counts. Running it natively on both laptops produces a direct
comparison table, and the same numbers reveal how many streams each machine can
sustain and which profile it should default to.

## 12. Build phases

1. RTSP reader, file reader, live 2×2 UI. No analysis.
2. Motion gate and debug view.
3. Detector on CPU, crop pipeline.
4. Tracker, N-of-M, alarm state machine, sound, events, clips.
5. Ignore-mask editor, sensitivity presets.
6. OpenVINO and CUDA backends, `--bench`, two-laptop comparison.
7. Tiled sweep mode under the GPU profile.
8. Later: PTZ handling and thermal modality.

Phases 1–7 are the scope of this spec. Phase 8 is named so the architecture
accommodates it, but it is not designed here.

## 13. Out of scope

Remote notification (Telegram, email, webhook), multi-user access, web UI,
recording of continuous video (only event clips are stored), face or licence
plate recognition, zones and tripwires, and more than four simultaneous streams.
