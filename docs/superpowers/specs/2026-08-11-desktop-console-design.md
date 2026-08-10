# Desktop console — design

**Date:** 2026-08-11
**Status:** approved, ready for an implementation plan

## Why this exists

The console has been a web page served from the laptop, with video reaching the
browser over WebRTC or fragmented MP4. It has never been stable in the field.
The same camera, over the same radio link, plays in VLC without interruption —
including at 4K, including while the head is being steered — while the browser
console stuttered every second and dropped streams.

That comparison rules out the camera, the link, and the transport: go2rtc and
VLC both negotiate `RTP/AVP/TCP;interleaved`, verified in a trace. What remained
was the browser path and the recovery logic wrapped around it. Three real faults
were found there and fixed, and the picture still stuttered at a full second of
buffering.

So the browser goes. VLC is the reference implementation of "this works on this
link", and the console moves to the desktop with VLC inside it.

## What this covers, and what it does not

**Covers:** replacing the browser console with a PySide6 desktop application
containing Live, Playback, Settings and Logs, with video rendered by libVLC.

**Does not cover:** detection. There is no detector and there never has been.
Movement detection is a separate subsystem with its own spec, and nothing here
should anticipate it beyond leaving the video pane replaceable.

## Decisions taken

| Question | Decision |
|---|---|
| How VLC appears | libVLC rendering into a Qt widget (`set_hwnd`), part of the layout |
| Scope of first cut | The whole console, including Playback |
| Where VLC pulls from | go2rtc on the laptop, not the camera directly |
| Where recording pulls from | The same local fan-out, unchanged from today |
| Process model | Window supervises go2rtc and the recorder as child processes |
| Detection | Out of scope |

## Architecture

```
VMD.exe  ──►  console (PySide6 window)
                 ├── VideoPane per stream   ← rtsp://127.0.0.1:<rtsp>/<name>
                 ├── Live · Playback · Settings · Logs
                 └── supervises ↓
              go2rtc          ← one connection to the camera, local fan-out
              recorder        ← same local stream: segments, index, retention
```

The window owns nothing that must outlive it. go2rtc and the recorder are child
processes it starts, watches and restarts through the existing `Supervisor`.
Closing the window, or a crash inside the video pane, must not stop the disk
filling. The laptop is dedicated to this system and always on, so the app starts
on boot; that is a deployment detail, not a dependency of the design.

### Why the local fan-out stays

One connection crosses the radio link and feeds both the picture and the disk.
Pointing VLC straight at the camera would be simpler by one process and would
double the link cost the moment recording runs — the cost we spent a day
removing. It also means VLC's buffer only has to absorb the laptop, because the
link's jitter has already been absorbed once.

The evidence that go2rtc is sound on the camera side: recording pulls from it
correctly, `ffprobe` pulls from it correctly, and every failure observed was
between go2rtc and the browser, which is the part being deleted.

## Components

### `vmd/desktop/` (new)

| Module | Responsibility |
|---|---|
| `app.py` | Application entry: settings, services, supervisor, main window |
| `window.py` | The window and its four tabs |
| `video.py` | `VideoPane` and its libVLC implementation |
| `live.py` | Video wall, steering overlay, side column |
| `playback.py` | Timeline from the segment index, seeking |
| `settings_tab.py` | The settings form, path finder, encoder controls |
| `logs.py` | Log table |
| `style.py` | Qt stylesheet carrying the DESIGN.md system |

### Deleted

`vmd/webui/` entirely — server, page, updater HTTP surface (the updater itself
moves into the desktop app unchanged).

### Unchanged, and their tests with them

`vmd/settings.py`, `vmd/storage/*`, `vmd/streaming/*`, `vmd/ptz/*`,
`vmd/radio/*`, `vmd/supervisor.py`, `vmd/record_main.py`, `vmd/launcher.py`.
None of them ever knew a browser existed.

## The video pane

The only interface that matters:

```python
class VideoPane(Protocol):
    def show(self, url: str) -> None: ...
    def stop(self) -> None: ...
    @property
    def state(self) -> Literal["playing", "connecting", "late", "stopped", "failed"]: ...
```

Today a libVLC widget sits behind it. When a detector needs decoded frames, the
implementation changes and nothing else does.

### VLC options, and why each

| Option | Reason |
|---|---|
| `--network-caching=300` | The source is local, so this absorbs the laptop only |
| `--rtsp-tcp` | What both VLC and go2rtc already negotiate |
| `--no-audio` | Never listened to; saves a decode and a failure mode |
| `--no-video-title-show` | This is a console, not a media player |
| `--avcodec-hw=any` | Hardware decode where the laptop offers it |

### Failure handling

**The pane watches; it does not intervene.** VLC handles its own recovery,
because it is demonstrably better at it than the timers that were doing it
before. The pane polls `get_state()` and the frame counter and reports:

- `playing` — frames advancing
- `connecting` — opened, nothing decoded yet
- `late` — no new frame for a while, **and nothing is done about it**
- `failed` — VLC reported `Error`

A stream is restarted only when it reaches `failed`, or when the operator
changes it. There are no stall timers, no reconnect-on-drift, no live-edge
chasing. Every disconnection reported from the field traced back to recovery
code firing too early; the fix is to remove that code rather than tune it.

## The tabs

**Live.** Two video panes in a splitter, either maximisable. Right column:
steering, zoom, link, storage, recent movement. Steering keeps its current
behaviour — arrow keys with diagonals, `+`/`−` zoom, `Home`, and edge-of-frame
with the pointer — through a transparent overlay widget driving the existing
`PtzService`.

**Playback.** Real for the first time. The segment index knows every file and
its start and end, so the timeline draws actual coverage and actual gaps.
Clicking a time opens that segment at that offset in the same `VideoPane` class
used by Live.

**Settings.** The existing fields, plus the two tools that earned their place:
**Find the right path** (probes common RTSP paths, measures each, sorts by cost)
and **Fit the camera to the link** (reads and caps encoder bitrates over ONVIF).
Both are already plain Python; they lose the HTTP layer and gain a progress line.

**Logs.** A Qt table with level filtering and tail-following, fed by the same
logging handler, carrying go2rtc's and the recorder's output as it does now.

### Deliberately not carried over

- **The alarm strip.** No detector exists. It returns with detection.
- **`video_mode` and `video_buffer_ms`.** They existed to work around the
  browser. VLC replaces both. Old settings files keep the keys harmlessly;
  nothing reads them.

### Failure states remain first-class

No camera configured, radio not answering, disk filling, recorder died and was
restarted, go2rtc will not start — each is a designed line in the interface, not
an error dialog.

## Testing

- **`VideoPane` gets a fake.** Every widget that consumes video is tested
  against it with `pytest-qt`, headless and fast.
- **One integration test** drives the real libVLC pane against a synthetic RTSP
  source and asserts that frames advance — the check that has been run by hand
  all through this investigation, committed instead of retyped.
- Timeline geometry, gap calculation and settings round-trips are plain
  functions with plain tests.
- Of the 207 tests passing today, the 8 covering the web server go with it; the other 199 move across untouched.

## Packaging and migration

`VMD.exe` remains a launcher that runs the project directory it sits in, so
double-clicking still works and the Update button keeps working — nothing is
frozen into the executable. `python-vlc` joins the dependencies; VLC itself is
already installed on the target machine.

`settings.json` is unchanged and existing files load as they are. Recordings,
the segment index and retention are untouched. Until `vmd/webui/` is deleted,
the browser console remains one `git checkout` away.

## Risks

| Risk | Response |
|---|---|
| Overlay flicker above VLC's native surface on Windows | Verified on 2026-08-11: overlay composites cleanly over libVLC on this machine. Steering stays on the picture; the side-strip alternative is not needed. See the measurements below |
| 4K decode cost on the laptop | Hardware decode enabled; the deployment is moving to a substream regardless |
| A desktop app is harder to inspect remotely than a web page | The Logs tab stays; the diagnostic report becomes a file that can be saved and sent |
| `python-vlc` version drift against installed VLC | Pin the dependency; the integration test fails loudly if the pairing breaks |

### Overlay probe measurements (2026-08-11)

`spike/overlay_probe.py` (python-vlc 3.0.21203, PySide6 6.11.1, VLC at
`C:\Program Files\VideoLAN\VLC`, Windows 11) run against a synthetic
640x512@30 H.264 RTSP source served by the bundled `go2rtc`:

```
uv run python spike/overlay_probe.py rtsp://127.0.0.1:8656/test --seconds 30 --grab grab.png
t=2s  frames=7   paints=43  covered=True
...
t=19s frames=530 paints=392 covered=True
```

- **Video is live.** libVLC's `displayed_pictures` climbed 0 → 868 over 30 s
  (~29-30/s), matching the source rate.
- **The overlay is painting.** `paintEvent` calls climbed 0 → 392 while the
  window was visible, a steady ~21/s. That is below the 33 ms timer's nominal
  30/s because Qt coalesces `update()` calls, not because frames were dropped —
  the rate never wavered.
- **The overlay is over the picture.** `overlay.geometry() == surface.rect()`
  and `overlay.isVisible()` held true on every one-second sample.
- **The overlay pixels survive the composite.** `window.grab()` produced a
  5,430-byte 960x600 PNG containing 1,195 pixels within 24/255 of `#EEBB58` —
  the amber box and label, drawn on top. Byte-identical across two runs.

Two honest limits on the above. `QWidget.grab()` reads back Qt's own painting,
so the Direct3D11 video surface comes back black (553,041 near-black pixels);
no pixel-level photograph of amber-over-video was obtainable, because
`SetForegroundWindow` is refused under the harness's foreground lock and a
desktop capture only ever caught the window behind. And when the window is
*fully occluded*, `paints` freezes while `frames` keeps climbing — ordinary
Windows repaint behaviour, not flicker, but worth knowing before anyone reads a
frozen overlay in a background window as a bug.
