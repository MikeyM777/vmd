# Detection — design

**Date:** 2026-08-11
**Status:** drafted for implementation after the desktop console

## What this is for

The console shows and records. It does not yet do the thing the system is named
after: notice that something moved and say so.

The requirement, in the owner's words: *"I don't really care whether a person
walked there or a rhinoceros. I want to know something changed, something is
happening in that area. A dog too."* And the counterweight: *"not wind, not
natural movement like trees moving, not birds."*

That shapes everything below. This is a **movement** detector with a classifier
attached, not an object detector. The classifier never decides whether to raise
an alarm; it only says what the moving thing probably was.

## What was already established, by measurement

Work done earlier in `spike/` settled the architecture, and this design does not
revisit it:

- **Motion-gated crop detection beats full-frame detection on every axis.**
  Background subtraction finds moving blobs, each blob is cropped from the
  full-resolution frame, and the classifier runs on the crop at native
  resolution. Measured against full-frame YOLO on the same footage: **46× fewer
  detections of parked cars** (which are not moving and must not be reported),
  **14× faster**, and better recall on small distant figures, because a 40-pixel
  person survives cropping and does not survive downscaling a whole frame to
  640.
- **Distance is not the limit; darkness is.** On MEVA surveillance footage the
  same pipeline held **93% recall at 35–78 pixels**. On the owner's own night
  footage it reached 50% recall at zero false alarms. The failures were dark
  frames, not small ones.
- **A per-class alarm cooldown is a trap.** One junk detection silenced every
  real one for the length of the cooldown. Cooldowns belong to tracks, not
  classes.

## Architecture

A separate process, exactly like the recorder, for the same reason: **the
console must not be able to stop detection, and detection must not be able to
stop the console.** It reads from the local streaming server, writes events to
SQLite, and the window reads that table.

```
go2rtc  ──► detector process ──► events.db ──► console (Live tab, alarm strip)
   │                                    └────► clips/  (optional short clip per event)
   └──────► recorder ──► segments
```

One more local consumer of go2rtc costs the radio link nothing — the camera is
still pulled once.

## The pipeline, per frame

1. **Decode** — one stream, at the frame rate the camera sends. The detector
   reads with OpenCV from `rtsp://127.0.0.1:<port>/<stream>`.
2. **Background subtraction** (MOG2) → a foreground mask.
3. **Blobs** — contours above a minimum area, merged when they overlap.
4. **Reject what is not worth looking at**, in this order, cheapest first:
   - **ignore mask**: operator-painted regions (a road, a swaying tree, a flag)
   - **geometry**: a blob whose size is impossible for its position — this is
     the bird rule, expressed honestly as "above the horizon line, nothing is
     ground traffic"
   - **global motion**: when more than a set fraction of the frame is moving at
     once, the camera itself is moving (PTZ, or wind shaking the mast). Every
     blob in that frame is discarded rather than reported
5. **Track** — associate blobs across frames by position and size.
6. **Confirm** — a track becomes an event only after it has been seen in **N of
   the last M frames** and has **moved further than a minimum distance**. Wind
   in foliage produces blobs that flicker in place; a person, a dog or a vehicle
   travels.
7. **Classify (optional, never gating)** — crop the track's box from the frame
   and run YOLO11n on it. The label and confidence are attached to the event.
   **A track that the classifier cannot name is still an event.** At 700 m a
   person is about 13 pixels on the thermal sensor: unclassifiable, and still
   exactly what the operator needs to know about.

## What is configurable, and what is not

| Setting | Why it exists |
|---|---|
| Which streams are detected | Thermal and visible fail differently; the operator chooses |
| Sensitivity (low / normal / high) | One control mapping to blob area and confirmation counts, not five sliders |
| Ignore mask, per stream | The only reliable answer to a specific swaying tree |
| Horizon line, per stream | The bird rule needs to know where the ground is |
| Run the classifier | Off for thermal by default: 13 pixels is not identifiable |
| Minimum travel | The wind rule |

**Not configurable:** whether a confirmed track raises an event. It always does.
The classifier has no veto.

## Events

```python
@dataclass(frozen=True)
class Event:
    id: int
    stream: str
    started: float          # epoch seconds
    ended: float
    box: tuple[int, int, int, int]   # in frame coordinates
    travelled_px: float
    label: str              # "" when the classifier did not run or could not tell
    confidence: float       # 0.0 when unlabelled
    clip_path: str          # "" when no clip was kept
```

Stored in `events.db` beside `segments.db`, same WAL settings, same retention
discipline: events are deleted with the footage they refer to, so the list never
points at a file that has been reclaimed.

## What the console shows

- **Live**: the alarm strip returns — it was removed when the detector did not
  exist. A confirmed event outlines the pane and names the stream and the time.
  Acknowledging clears it.
- **Recent movement**: a real list, with a blank confidence column when the
  classifier could not name the thing, and a note saying that blank means
  unidentified rather than uncertain.
- **Playback**: event marks on the timeline, clickable, seeking to five seconds
  before the event.

## Failure handling

The detector process is supervised exactly like the recorder. If it dies it is
restarted; if it will not stay up, the console says so rather than pretending
detection is running. A stream the detector cannot open is reported per stream —
detection continuing on the thermal while the visible is unreachable is normal
and must not read as total failure.

**Detection stopping must never stop recording.** They are separate processes
and share nothing but the local stream.

## Testing

- The pipeline stages are pure functions over arrays, tested with synthetic
  frames: a moving rectangle produces a track; a flickering rectangle in place
  does not; a whole-frame shift produces nothing.
- The confirmation rule is tested as a state machine, without images.
- The ignore mask and horizon rules are geometry, tested as geometry.
- One end-to-end test against a generated video with a known moving object,
  asserting exactly one event with a plausible box.
- The classifier is stubbed everywhere except one integration test, because
  loading YOLO weights takes seconds and proves nothing about the plumbing.

## Deliberately out of scope

- Re-identifying the same object across streams or across time
- Any automatic PTZ response to an event — the slew-to-cue idea is a separate
  design and a much larger one
- Classifying thermal imagery: the model is trained on visible-light images and
  a 13-pixel thermal blob is not a photograph
