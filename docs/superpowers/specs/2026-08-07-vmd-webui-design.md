# VMD Web UI — Live View, Recording and Playback

**Date:** 2026-08-07
**Status:** Approved design
**Supersedes:** the desktop-app portions of `2026-08-06-vmd-design.md` (4×RTSP Qt grid). The
motion-gate and detection work from that spec remains valid and is now a separate,
later concern.

## 1. Purpose

A local web application that shows a live view of one multi-spectral PTZ camera,
lets the operator steer it, records both video streams continuously, and lets the
operator look back through what was recorded.

Detection and AI-driven camera movement are **out of scope for this spec** and are
designed separately. This spec covers the video plumbing and the operator interface
that everything else will later sit on.

## 2. The overriding requirement

**The live view must never break.**

Not "should rarely break". The application must never present a black box, a
permanent spinner, an error page, or a crashed window. Every other design decision in
this document yields to that.

What this guarantees, concretely:

| Situation | What the operator sees |
|---|---|
| Normal | Live video, sub-second delay |
| Link degraded | Live video at reduced quality, still playing |
| Preferred streaming path fails | Automatic fallback to a simpler, more robust path |
| Link fully down | The last good frame, held on screen, labelled `LINK LOST — last frame 14 s ago` with a live counter |
| Detection/AI process crashes | Video unaffected |
| Recorder crashes | Video unaffected |
| Retention/cleanup crashes | Video unaffected |
| Any component dies | Restarted automatically within seconds |

**The honest limit:** when the radio link is genuinely down, no software can display
live video. What the system can do — and must do — is never crash, never lie about
the state, hold the last known frame, and reconnect the moment it becomes possible.

This requirement is why the architecture is split into independent processes rather
than one program. The live-video path has no dependency on any other component, so no
other component can take it down.

## 3. Deployment context

| Item | Value |
|---|---|
| Camera | Teledyne FLIR Elara DX-Series, multi-spectral PTZ (thermal + visible on one head) |
| Thermal | 640×480 / 640×512 uncooled, fixed lens (model-dependent FOV), streams QVGA–VGA only |
| Visible | 4K 1/1.8" CMOS, 31× optical zoom, HFOV 61.8° → 2.15°, focal 6.5–202 mm |
| Camera control | FLIR SDK / FLIR CGI over HTTP. **ONVIF is not listed in the datasheet** — to be confirmed against the actual unit |
| Video transport | RTSP. Thermal: one H.264 channel. Visible: two independent H.264 channels |
| Radio link | Ubiquiti airMAX point-to-point, **over 15 km**: NanoStation Loco and LiteBeam |
| Laptop connection | Ethernet cable to the local radio. **No wifi on the laptop** |
| Bandwidth expectation | ~5 Mbps total, and variable with link conditions |
| Camera-to-target distance | ~700 m |
| Network exposure | **None.** Fully offline. No internet, no LAN sharing, no remote access |
| UI language | English, left-to-right |

### Why the optics matter to the software

At 700 m a 1.8 m person subtends 0.147°. That yields:

* **Visible at full zoom (2.15° HFOV):** 263 px at 4K, **132 px at 1080p**, 88 px at
  720p. Comfortable for recognition at any stream resolution we would realistically use.
* **Visible at wide (61.8°):** ~9 px. Useless for anything but framing.
* **Thermal, VGA stream:** 12 px (8° lens), 8 px (12° lens), 4 px (24° lens). Enough to
  see that something warm is present; **not** enough for a detector to classify it.

Two consequences bind this spec:

1. **Thermal must always be pulled at VGA, never QVGA.** At QVGA the person halves again
   and the thermal stream stops being useful at this range.
2. The eventual detect-then-identify design (thermal spots, visible zooms in) is the only
   arrangement the optics permit. The UI must make the current zoom level and its
   implication legible to the operator — see §6.

## 4. Architecture

Five independent OS processes plus the browser. Independence is the point, not an
implementation detail.

```
  CAMERA ──RTSP over radio link──►  LAPTOP  ──────────────►  BROWSER (localhost only)
                                      │
      ┌──────────────┬────────────────┼─────────────────┬──────────────────┐
      │              │                │                 │                  │
  streamer       recorder        retention          controller          backend
  (go2rtc)       (ffmpeg)        (cleanup)        (PTZ + bitrate)      (web app)
      │              │                │                 │                  │
 live video     5-min chunks     deletes old      camera + radio     serves UI + API
 to browser       to disk          chunks             HTTP APIs
```

**Streamer — go2rtc.** Off-the-shelf. Ingests RTSP, serves WebRTC to the browser at
sub-second latency, with MSE and MJPEG as progressively more robust fallbacks. We
configure it; we do not write it. Writing a streaming server would take months and be
worse.

**Recorder — ffmpeg, one process per stream.** Copies the incoming H.264 to disk
without re-encoding (`-c copy`), so it costs almost no CPU and loses no quality.
Writes 5-minute segments.

**Retention.** Enforces the storage budget and age rules, warns before deleting.

**Controller.** Talks to the camera's HTTP API for pan/tilt/zoom and encoder bitrate,
and to the radio's admin API for link statistics.

**Backend.** Serves the web UI and a small local API. Binds to `127.0.0.1` only.

**Watchdog.** A supervisor restarts any process that dies. The streamer is restarted
first and independently; it never waits on the others.

### Why processes, not threads

If the detector hangs, or ffmpeg deadlocks on a bad file handle, or the cleanup job
throws while walking the disk, a single-process design takes the video down with it.
Separate processes make that impossible. This directly implements §2.

### Technology choices

Python for the backend (matching the existing detection work), `go2rtc` for streaming,
`ffmpeg` for recording, SQLite for the event and segment index, plain HTML/CSS/JS for
the UI. No build step, no framework, no package registry at runtime — the machine is
offline and must stay installable from a folder.

## 5. Live view

**Layout.** One large video with a smaller second video inset in a corner — thermal and
visible. Clicking the inset swaps them. Layout buttons: visible only, thermal only,
side-by-side, picture-in-picture.

**Latency target: under 0.5 s**, delivered by WebRTC. This is required because the
operator steers by hand: with a 2-second delay you push right, see nothing, push again,
and overshoot badly. At 2.15° field of view an overshoot means losing the target
entirely.

**Fallback ladder.** WebRTC → MSE → MJPEG → held last frame. Each step is more robust
and less pretty than the last. The UI shows which mode is active. Degradation is
automatic and requires no operator action.

**Always-visible status:** stream health per sensor, current bitrate, current zoom, link
quality from the radio, disk state, and the age of the oldest retained footage.

## 6. Camera control

**Steering.** Arrow controls around the video plus zoom in/out, hold-to-move and
release-to-stop. A speed slider covering the camera's full range (0.1°/s to 90°/s pan,
0.1°/s to 60°/s tilt) — slow speeds matter for fine aiming at 700 m, fast for slewing.

**Click-to-centre.** Clicking a point on the video centres the camera on it. Computed
from the current field of view and the click offset. At long range this is far more
usable than arrow-nudging, and it is the same mechanism the future AI cueing will use.

**Zoom readout.** The UI continuously displays the current visible field of view **and
the resulting person height in pixels at the configured target distance**. The operator
should never have to guess whether the camera is zoomed enough to identify anything —
the number is on screen. Target distance is a setting, default 700 m.

**Presets are deliberately excluded.** The camera supports 256; the operator does not
want them.

## 7. Recording

Both streams recorded continuously, as sent by the camera, no re-encoding.

**Segments:** 5 minutes each, named by stream, date and time
(`visible/2026-08-07/2026-08-07_14-35-00.mp4`). Chosen so that deletion is a cheap file
unlink, a crash loses at most 5 minutes, and footage is browsable in the file explorer
without the application.

**Segment index:** SQLite table recording path, stream, start time, end time, size and
whether the segment closed cleanly. This is what the playback timeline reads; it never
scans the disk.

**Link dropouts** produce a gap in the index rather than a corrupt file. The recorder
reconnects with backoff and starts a fresh segment. Gaps are drawn on the timeline, so
lost coverage is visible rather than silent.

**Re-compression was considered and rejected.** Tiered "shrink old footage overnight"
storage would roughly halve disk use, at the cost of CPU, complexity, quality loss and
a new class of failure. The simpler rule — record raw, delete old — was chosen
deliberately.

## 8. Storage and retention

**Budget.** On first run the application detects the drive and its free space, and
displays what it found. If detection fails, the operator types the figure. The operator
then sets **how much space the program may use**. The application never exceeds that
budget, so it cannot fill the disk and destabilise the machine.

**Two independent deletion rules, either of which may be disabled:**

1. **Age** — delete segments older than N days.
2. **Space** — when the budget is reached, delete oldest segments until under it.

At ~5 Mbps for both streams, expect **~54 GB/day**. A 600 GB budget therefore holds
roughly 11 days. The space rule, not the age rule, will normally be the binding one —
the deployment may run for months, so the system must behave correctly when the disk is
the limit rather than the calendar.

**Warning before loss.** When within approximately 24 hours of deleting footage, a
banner appears: `Storage 92% full. Footage from 22 March will be deleted in about
18 hours.` This gives the operator a chance to export first. If ignored, deletion
proceeds and recording continues — **the system never stops recording to preserve old
footage.**

**Drive removal** pauses recording with a prominent warning and resumes automatically
when the drive returns. Existing files are never modified.

**Always displayed:** days of footage held, space used against budget, and the timestamp
of the oldest retained footage.

## 9. Adaptive bitrate

**Problem.** When the radio link degrades, a camera still pushing 5 Mbps produces
freezes, macroblocking and recording gaps. Voluntarily dropping to 2 Mbps produces
continuous, watchable video instead. Lower quality beats broken video.

**Inputs, in order of preference:**

1. **Radio statistics** from the airOS admin API — signal strength, capacity, airtime.
   This is leading indicator: the radio degrades before the video visibly breaks.
   Requires the operator to supply the radio's address and credentials (§11).
2. **Video statistics** — packet loss, dropped frames, and received bitrate versus the
   camera's configured bitrate. If the camera claims 5 Mbps and 3 arrives, the link is
   the bottleneck. Always available; reacts later than the radio signal.

**Control loop**, evaluated every 10 seconds:

* Degradation → step **down** promptly through 5 → 4 → 3 → 2 → 1 Mbps.
* Sustained health for several minutes → step **up** one level, cautiously.
* Hysteresis prevents oscillation: recovery must persist before any increase.

**Operator control:** automatic or manual mode, with a configurable floor and ceiling.
The current bitrate, the last change and its reason are shown on screen and written to
a log, so that poor footage can be attributed to the link rather than to the camera.

**Failure isolation:** if the bitrate controller errors or cannot reach the camera, it
logs and stops adjusting. It must never affect the live stream — see §2.

## 10. Playback

A Playback tab, in the same window.

**Timeline.** Select a date and a stream to get a full-day timeline showing solid
coverage where footage exists, **gaps where it does not**, and **marks where events
occurred**. Clicking anywhere seeks; clicking a mark seeks to 5 seconds before it.

**Where events come from.** Detection is out of scope here (§14), so this spec defines
only the *consumer* side: the timeline reads an `events` table (time, source, label,
optional thumbnail) and draws whatever it finds. Rows may later be written by the
camera's own analytics, by the detection pipeline, or by an operator marking a moment
by hand. If nothing writes to that table, the timeline simply shows no marks and
everything else works unchanged. Nothing in playback depends on a detector existing.

Time runs left to right, as in every media player.

**Controls:** play/pause, frame step, speed ¼×–8×, and ±10 s / ±1 min jumps. Fast speeds
matter — most of the footage is an empty field at night.

**Cross-stream:** at any moment the operator can switch between what the thermal and the
visible saw at the same instant. At 700 m this is the core value: thermal shows that
something was there, visible shows what it was.

**Export.** Mark in and out points and write the range out as a normal MP4 for copying
to removable media. This is the sanctioned route for footage to leave the machine before
retention deletes it.

**Known limitation:** because segments are stored raw and un-indexed internally, seeking
resolves to the nearest segment boundary and plays forward. Jumps are accurate to a
second or two, not frame-exact. The alternative — re-indexing every segment — costs CPU
that §7 deliberately declines to spend.

## 11. Settings

All operator-supplied, stored locally, none hardcoded:

* **Camera:** IP address, username, password, RTSP paths for each stream.
* **Radio:** IP address, username, password. Optional — the system works without it,
  using video statistics alone for bitrate control.
* **Storage:** drive path, budget in GB, retention days, and enable/disable per rule.
* **Bitrate:** automatic or manual, floor, ceiling.
* **Target distance:** default 700 m, used for the person-height readout.

## 12. Failure handling

| Failure | Behaviour |
|---|---|
| RTSP stream drops | Streamer reconnects with backoff; UI holds last frame with an honest label; recorder starts a new segment on return |
| Radio link degrades | Bitrate steps down automatically; UI shows link quality and current bitrate |
| Radio unreachable | Bitrate control falls back to video statistics; a notice is shown; nothing else changes |
| Camera HTTP API unreachable | Steering controls disable with an explanation; live video and recording unaffected |
| Disk full | Retention deletes oldest; recording never stops |
| Drive unplugged | Recording pauses with a prominent warning; resumes automatically; live view unaffected |
| Any process crashes | Watchdog restarts it; the streamer is restarted first and independently |
| Browser closed | Everything keeps running; recording is not tied to the UI being open |

## 13. Testing

* **Unit:** retention rules against a synthetic segment index (age rule, budget rule,
  both, neither); the bitrate state machine driven by fabricated link statistics,
  including the anti-oscillation behaviour; the segment index; the person-height
  calculation against the optics table in §3.
* **Integration:** record from a simulated RTSP source, kill it mid-recording, confirm a
  clean gap in the index and automatic recovery; fill a simulated budget and confirm
  oldest-first deletion with the warning appearing first.
* **Failure injection, and the most important tests here:** kill each process in turn and
  confirm the live view is unaffected; sever the network mid-stream and confirm the last
  frame is held with a correct counter rather than a black box; unplug the drive during
  recording. §2 is the requirement most likely to be quietly broken by a later change, so
  it gets explicit tests.

## 14. Out of scope

Detection and alarms (existing, separate work), AI-driven camera movement, multi-camera
support, any network or remote access, mobile or tablet layouts, user accounts, cloud
anything, re-compression or tiered storage, camera presets, and RTL/Hebrew localisation.

## 15. Open questions

1. **Exact camera model** (DX-608 / 612 / 624 / 650, or the 320×240 variants). Determines
   thermal field of view and therefore how much ground one position covers and whether a
   person is 4 px or 13 px in thermal.
2. **Camera control protocol** — whether the unit accepts ONVIF PTZ or requires FLIR CGI.
   The datasheet lists only the FLIR APIs.
3. **Radio statistics access** — whether these airOS units expose `status.cgi` or SNMP to
   an authenticated local client.
4. **The camera's own analytics.** The operator reports the camera can detect and track
   targets by itself. If those alerts are retrievable over the API, a substantial part of
   the future detection work may be replaced by consuming them. Worth investigating before
   building detection on this platform.
5. **Sustained link throughput** over the 15 km path, which sets the realistic bitrate
   ceiling. To be measured, not assumed.
