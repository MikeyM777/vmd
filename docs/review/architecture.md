# Architecture review

**Date:** 2026-08-11
**Scope:** the whole of `vmd/`, read against
`docs/superpowers/specs/2026-08-11-desktop-console-design.md` and
`docs/superpowers/specs/2026-08-11-detection-design.md`.
**Status:** a backlog. Nothing here was changed.

Written for somebody picking this up weeks from now with no memory of today. It
is deliberately split into **worth doing** and **worth knowing**, because most of
what is imperfect here is imperfect for a reason that was paid for in the field,
and a review that asks for everything gets ignored.

---

## The architecture as it actually is

The design documents describe a window that supervises two children. That is no
longer what this is.

**Four processes and a filesystem protocol.** `go2rtc`, `vmd.record_main`,
`vmd.detect_main` and the console window are four independent processes that
coordinate through small files beside `settings.json` and inside the recording
root:

| File | Written by | Read by |
|---|---|---|
| `settings.json` | console (Settings tab) | all four at start; the recorder also re-reads it on mtime (`vmd/record_main.py:791`) |
| `streaming.json` | console, for go2rtc (`vmd/streaming/go2rtc.py:814`) | recorder, detector, picker |
| `recorder.pid` + `.json` | **the recorder itself** (`vmd/record_main.py:343`) | console, logon task |
| `go2rtc.pid` + `.json` | console, on go2rtc's behalf (`vmd/streaming/go2rtc.py:834`) | console |
| `detector.pid` + `.started` | console (`vmd/desktop/services.py:835`) | console |
| `detection.json` | detector (`vmd/detect_main.py:320`) | console status line |

**Nobody owns the process lifecycle.** Three things can start a recorder: the
console's `Supervisor`, the `VMD Recorder` scheduled task
(`scripts/recorder_service.ps1`), and an operator. They are arbitrated *after the
fact* by the claim file, plus a deliberate 45-second delay in
`scripts/autostart.ps1`. That is a real design, it works, and it is written down
nowhere. It is the most surprising thing about this codebase.

**The one invariant everything is built around is "the camera is pulled once
across the radio link."** It is protected by convention in four places and
enforced nowhere (finding 2).

**The console window is a poller.** One `QTimer` at 2 s
(`ConsoleWindow.heartbeat`, `vmd/desktop/window.py:531`) drives supervision, pane
state, restart backoff, the movement list, the link panel, the storage panel and
the status band. Slow work is pushed onto threads and the heartbeat reads
whatever they last left behind. The model is sound and hard-won — but there are
still blocking calls on the GUI thread, on the heartbeat itself and on the Save
button (findings 3 and 4).

**`vmd/desktop/services.py` is not a module, it is three.** 1,772 lines holding a
generic Windows child-process supervisor with PID adoption (lines 335–1151), a
settings-fingerprint/apply engine (172–243, 1432–1560), and a status-reporting
layer that composes operator sentences (1198–1227, 1570–1772). Only the middle
one is "the processes the window looks after".

---

## Worth doing

Ordered by what I would fix first on a system whose owner says *"this project is
going to actually save life."*

### 1. The console process imports OpenCV and NumPy, and three comments insist it does not

**What.** `vmd/detect/__init__.py:36-38` eagerly imports `motion`, `runner` and
`pipeline`, and `vmd/detect/motion.py:12` is `import cv2`. So
`from vmd.detect.events import EventStore` — which the window does at
`vmd/desktop/window.py:507` and the recorder at `vmd/record_main.py:537` — drags
the whole detector stack in. Demonstrated:

```
$ python -c "import sys; from vmd.detect.events import EventStore; print('cv2' in sys.modules)"
True
```

**Why it matters here.** Three comments are written on the opposite assumption
and are load-bearing:

- `vmd/desktop/services.py:78-82` repeats the literal `"detection.json"` rather
  than importing it, "because importing `vmd.detect_main` would pull cv2, numpy
  and eventually the classifier's weights into the window's process, which must
  open on a laptop where none of that is installed".
- `vmd/desktop/services.py:125-135` duplicates `detected_streams`' rule as
  `detection_enabled` for the same stated reason.
- `vmd/record_main.py:531` defers the `EventStore` import "because `vmd.detect`
  pulls in the detector's whole stack, and a machine that only records must not
  need it installed to record".

All three copies were paid for and buy nothing. Worse: on a laptop without the
`detect` extra, `_open_events` swallows the ImportError
(`vmd/desktop/window.py:510`) and the operator silently loses the movement list
and every timeline mark, with the reason only in the Logs tab.

**Instead.** Strip `vmd/detect/__init__.py` to its docstring (or make the
re-exports lazy via module `__getattr__`), then delete the two duplicated rules
in `services.py` and import them. Keep the deferred import in `record_main` — it
is still right for the recorder's own reasons.

**Size:** an hour, including deleting the duplicates.

---

### 2. Three processes can each open their own connection to the camera, and nothing counts them

**What.** The founding constraint is a >15 km, ~5 Mb/s link, and every part of
the design says the camera is pulled exactly once. Three components
independently fall back to the camera's own URL when the local streaming server
does not look right:

- `vmd/record_main.py:554-570` `_source_for` — decided once at startup from
  `is_live(endpoint)`.
- `vmd/detect_main.py:195-207` `_source_for`, plus a **runtime** rotation in
  `vmd/detect/runner.py:449-473`.
- `vmd/desktop/picker.py:537-563` `_sources_for`.

The detector's rotation is the dangerous one, because it is sticky:

```python
if len(self.sources) < 2 or self._failed_opens < OPEN_FAILURES_BEFORE_FALLBACK:
    return
self._failed_opens = 0
self.sources.append(self.sources.pop(0))
self.url = self.sources[0]
```
— `vmd/detect/runner.py:464-468`

It rotates only on *failure*. Once it has rotated to the camera and the camera
answers, it stays on the camera for the life of the process. A single go2rtc
restart — which the console itself performs on every material settings change
(`vmd/desktop/services.py:1464`) — can move detection permanently onto a direct
link crossing, announced by one `logger.warning` in a 500-line ring.

Worst case today: go2rtc's pull + the recorder's fallback + the detector's
fallback = three copies of the stream across a link that "barely carries one"
(`vmd/desktop/live.py:6-9`).

**Why it matters here.** Not efficiency. The console spec says doubling the link
cost "is the difference between recording and losing the live picture as well".
Losing the live picture is the failure this system exists to not have.

**Instead.** Two steps; the first is most of the value.
1. Make it visible. The detector already publishes per-stream state — add
   `source: "local" | "camera"` to `detection.json` and to the recorder's status,
   and put a line in the status band when anything is on `camera`.
2. Make the rotation reversible: after N minutes on the fallback, try the local
   server again. Sticky-on-success is the bug.

**Size:** half a day for (1), a day for both.

---

### 3. The 2 s heartbeat reads the recordings folder on the GUI thread

**What.** `vmd/desktop/disk.py:19-24` states the house rule outright:

> Every question here touches the filesystem, and the filesystem is exactly what
> is broken in the cases that matter — a disconnected drive can leave a stat call
> blocked for many seconds. So none of it runs on the GUI thread and none of it
> runs on the two-second heartbeat.

Three things break that rule against the *same* folder:

| Call | Path | Reached from |
|---|---|---|
| `read_detection_status` → `Path(path).read_text(...)` (`vmd/desktop/services.py:1165`) | `storage.root / "detection.json"` (`services.py:1262`) | `heartbeat` → `status_parts` → `state()` → `detection_state()` |
| `EventStore.recent(RECENT_LIMIT)` (`vmd/desktop/live.py:801`) | `storage.root / "events.db"` (`vmd/desktop/app.py:134`) | `heartbeat` → `LiveTab.refresh` → `_refresh_events` |
| `SegmentIndex.all(stream)` (`vmd/desktop/playback.py:317`, `:273`) | `storage.root / "segments.db"` | the Playback date/stream slots |

And `state()` is called **twice per heartbeat**, because `_show_recording` →
`recording_now()` asks for it again independently (`vmd/desktop/window.py:551`
and `:770`).

There is also a settings file read *and write* on a Qt slot:
`ConsoleWindow.view_changed` (`vmd/desktop/window.py:592-597`) does
`load_settings` + `save_settings` on every view-chooser click and every number
key.

**Why it matters here.** The exact scenario `disk.py` was written for — the
recordings root pointed at a drive letter with nothing behind it, or at a UNC
path an operator typed — is the scenario in which the console now freezes every
two seconds. `storage_problem` (`vmd/desktop/settings_tab.py:516`) catches a
totally unreachable folder at Save time, but a folder that *becomes*
unreachable afterwards is precisely the case here.

**Instead.** `detection.json` and the events list are already "a slow question
with a cached answer" — that is what `BackgroundValue` is
(`vmd/background.py:67`). Wrap both; the heartbeat reads the `Reading` and its
age, which also gives the status line an honest "the detector's report is 40 s
old" instead of silently believing it. Cache `state()` for one heartbeat rather
than computing it twice. Make `view_changed` write on a worker, or debounce it.

**Size:** half a day.

---

### 4. Pressing Save can freeze the window for tens of seconds

**What.** `SettingsTab.save()` emits `saved` on the GUI thread; the slot is
`ConsoleWindow.settings_saved` (`vmd/desktop/window.py:601`), which calls
`ConsoleServices.apply` synchronously. On that path, all on the GUI thread:

- `storage_problem(...)` — `mkdir` + `write_bytes` + `unlink` of a probe file on
  the chosen folder (`vmd/desktop/settings_tab.py:539-548`, called at `:1053`).
- `Go2rtcService.apply` → `stop(force=True)` → `_stop_adopted`: `taskkill`
  (`subprocess.run`, `timeout=10`, `vmd/streaming/go2rtc.py:373`), then a
  `while ... time.sleep(0.1)` loop bounded by `ADOPTED_STOP_SECONDS = 2.0`
  (`go2rtc.py:709-711`), where each turn is a `tasklist` costing ~150 ms. The
  non-adopted path is `process.wait(timeout=5)` twice (`go2rtc.py:756`, `:760`).
- `ChildProcess.restart` → `stop(force=True)`: `_taskkill_tree`
  (`timeout=TREE_STOP_SECONDS` = 10.0), then `process.wait(timeout=10)`, then
  `process.wait(timeout=10)`, then `process.wait(timeout=5)`, then
  `wait_for_output(2.0)` — `vmd/desktop/services.py:931-955`. **Twice**: once for
  the recorder, once for the detector.
- then `start()` → `self._alive(adopted)`, a synchronous `tasklist`
  (`services.py:466`).

Worst case is tens of seconds of a completely frozen window, during which the
heartbeat does not run and the alarm strip cannot appear.

**Why it matters here.** This codebase has already learned this lesson twice and
written it down twice — the radio (`vmd/radio/service.py:26-35`) and PTZ
(`vmd/ptz/service.py:186-194`) were both moved off the GUI thread because "while
that ran the window did not repaint, the supervisor did not tick, and the alarm
strip could not appear". The Save path does the same thing, at the moment the
operator is standing at the machine waiting for an answer.

**Instead.** Make `ConsoleServices.apply` the third `QRunnable`-plus-signal in
this codebase; `SettingsTab` already has the machinery (`_ToolSignals` /
`_ToolJob`, `vmd/desktop/settings_tab.py:583-602`) for the camera tools. Disable
Save, run apply on the pool, deliver the problem list back through a signal —
`report_after_save` is already the seam for the answer.

While you are there: `vmd/desktop/services.py:1076` is the one
`subprocess.run(["tasklist", ...])` in the codebase with **no `timeout=`** (its
twin at `vmd/streaming/go2rtc.py:356` has `timeout=15`), and it is reachable
inline from the GUI thread via `services.py:419` and `:466`. Fifteen minutes.

**Size:** a day, mostly test rework — the current tests call `apply` and assert
on its return value synchronously.

---

### 5. The PID-claim protocol is implemented three times, three different ways

**What.** "Write a bare integer, write a JSON companion beside it, decide whether
the process behind that integer is really ours" exists three times:

| | recorder | go2rtc | console children |
|---|---|---|---|
| claim | `vmd/record_main.py:343` (`O_CREAT\|O_EXCL`) | `vmd/streaming/go2rtc.py:834` (plain write) | `vmd/desktop/services.py:835` (plain write) |
| companion | `.json` `RecorderIdentity` | `.json` `StreamingClaim` | **`.started`**, a fourth format |
| "is it ours" | boot time + image name (`:274`) | boot time + image name (`:487`) | **process start time via `GetProcessTimes`** (`:995`) |
| liveness | `tasklist /FO CSV`, `timeout=15`, no `creationflags` (`:242`) | `tasklist /FO CSV`, `timeout=15`, **with** `creationflags` (`:341`) | `tasklist`, no `/FO`, substring match, **no timeout** (`:1073`) |

`boot_time()` is byte-identical in `vmd/record_main.py:223` and
`vmd/streaming/go2rtc.py:322` — the second admits in its own docstring that it is
"spelled out here rather than imported from the recorder". `identity_path()` is
byte-identical at `:121` and `:285`.

**Why it matters here.** The failure this code exists to prevent is named at
`vmd/desktop/services.py:688-707`: the console adopts a stranger, reports
"recording", and nothing is written. That is the worst failure shape in the
system. Three implementations mean three chances for it, and they already differ
in strength — only the console verifies process *start time*; only the other two
verify the *image name*. Neither does both.

**Instead.** One `vmd/process.py`: `read_claim`, `write_claim`,
`claim_exclusively`, `release_claim`, `process_image`, `process_started_at`,
`pid_alive`, `kill_tree`, and one `is_ours(pid, claim)` applying *all three*
tests. Each caller keeps only its filename and its image whitelist. It must stay
standard-library-only — `vmd/record_main.py:246` is right that the
recording-only laptop has no psutil.

**Size:** a day for the extraction, a day for the test migration (every class
currently injects its own `image_of` / `booted` / `alive` / `kill_tree`). Do it
before adding a fourth supervised child.

---

### 6. Six password redactors, two mask tokens, three different rule sets

**What.**

| Where | Algorithm | Forms covered | Mask |
|---|---|---|---|
| `vmd/desktop/logs.py:75-82` | regex on `scheme://user:pass@` | n/a | `****` |
| `vmd/detect/runner.py:92-108` | **byte-identical regex**, copied on purpose | n/a | `****` |
| `vmd/streaming/diagnose.py:40-78` | replace known secrets, longest first | raw, `quote(safe="")` | `****` |
| `vmd/desktop/settings_tab.py:1369` | replace known secrets | raw, `quote(safe="")` | `****` |
| `vmd/desktop/picker.py:566` | replace known secrets | raw, `quote(safe="")` | `****` |
| `vmd/radio/airos.py:110-129` | replace known secrets | raw, `quote`, **`quote_plus`** | `***` |
| `scripts/_common.ps1:428` and `:459` | both algorithms again, in PowerShell | `EscapeDataString` | `********` |

Only `airos` knows `quote_plus`. Only `diagnose` sorts longest-first, so that a
password containing another password is masked whole.

**Why it matters here.** `CameraTools.write_report`
(`vmd/desktop/settings_tab.py:1342`) exists specifically to produce a file "meant
to be handed to somebody else". I could **not** find a live leak today — `airos`
redacts its own messages before they escape, and the report path is
double-covered by `diagnose.redact` and `_secrets`. The hazard is entirely
future: the next person who adds a message on a path guarded by the weaker
redactor will not know there are six.

**Instead.** One `vmd/secrets.py` with `forms_of(password)` (raw, `quote`,
`quote_plus`, longest-first), `scrub(text, settings)` and `scrub_urls(text)`. It
must not import Qt, which means `vmd/detect/runner.py` can import it rather than
copying it. Leave the PowerShell copy — it cannot import Python.

**Size:** half a day.

---

### 7. Dead code that reads as live

Each of these is something an inheritor will spend time understanding before
discovering nothing calls it.

- **`vmd/updater.py` — 212 lines, entirely unreachable.** Nothing in `vmd/`
  imports it; the only references outside `tests/test_updater.py` are in the
  design spec, which claims "the updater itself moves into the desktop app
  unchanged"
  (`docs/superpowers/specs/2026-08-11-desktop-console-design.md:89`). There is no
  Update button; the window has four tabs (`vmd/desktop/window.py:459-463`). It
  is also the only code in the tree that assumes a network:
  `vmd/updater.py:119` runs `uv sync --extra detect` **without `--offline`** —
  the one uv invocation in the repo that omits it, against the explicit rule in
  `VMD.bat:44-51` and `vmd/launcher.py:151-157` — with
  `TIMEOUT_SECONDS = 600` (`:23`, applied at `:186`), and `vmd/updater.py:175`
  tells an air-gapped operator to "check the internet connection on this
  machine".
- **`PtzService.status()`, `.encoders()`, `.set_encoder()`** —
  `vmd/ptz/service.py:47, 78, 103`. No callers outside tests; only
  `fit_encoders_to_link` survived the browser console
  (`vmd/desktop/settings_tab.py:1337`). `status()` does a blocking `connect()`
  while holding `PtzService._lock`, which the PTZ command thread also takes — a
  latent GUI stall if anyone calls it from a slot.
- **`Supervisor.health()`** — `vmd/supervisor.py:159-207`. No caller; it was the
  web API's health endpoint. Note `settled` / `short_lived` / `flapping` are
  **not** dead — `_say_if_flapping` still uses `_reason`.
- **`vmd/streaming/check.py`** — 194 lines, a CLI nothing imports. It is the only
  caller of `Go2rtcService.sources()` (`vmd/streaming/go2rtc.py:986`), so that is
  dead too.
- **`vmd/desktop/services.py:29`** imports `is_live` and never uses it.

**Instead.** Delete `check.py`, `Supervisor.health`, `sources()`, the three PTZ
methods and the unused import. For `updater.py`: wire it to a button or delete it
and correct the spec — but do not leave it in the third state, where a future
reader assumes the Update button exists.

**Size:** two hours, plus a decision about the updater.

---

### 8. `video_mode` and `video_buffer_ms` are not as dead as the spec says

**What.** The spec says *"Old settings files keep the keys harmlessly; nothing
reads them."* Two things read them:

```python
f"video         : {settings.video_mode}, {settings.video_buffer_ms} ms buffer",
```
— `vmd/desktop/settings_tab.py:1299`, in `_report_header`: the first six lines of
the diagnostic report that gets sent to whoever is helping.

And `vmd/settings.py:379-384` still *validates* `video_buffer_ms`, so a settings
file with `video_buffer_ms: 9999` refuses to load and the console falls back to
defaults — over a field nothing uses. `vmd/settings.py:356-366` still documents
`video_mode` as "How the live picture reaches the browser."

`tests/test_desktop_settings_tab.py:253-262` **pins** it: the test asserts that
`video_mode` survives a save round trip. The test is right about the general rule
— a field the form does not show must survive — and wrong about the example.

**Instead.** Drop both fields from `Settings`, drop the line from
`_report_header`, and point that test at a field that is genuinely off-screen and
live (`bitrate.ceiling_kbps`; the report already prints it). Pydantic ignores
unknown keys, so existing `settings.json` files keep loading.

**Size:** an hour.

---

### 9. `ConsoleServices` and `ConsoleWindow` duck-type their own collaborators

**What.** The console asks its own objects whether they have methods:

```python
adopt = getattr(self.streaming, "adopt", None)
if adopt is not None:
    adopt(endpoint)
else:  # a streaming service handed in by a test, without a claim
    self.streaming.api_port = ...
```
— `vmd/desktop/services.py:1328-1337`, and again at `:1347` (`replace`), `:1374`
(`unadoptable`) and `:1477` (`adopted`).

`ConsoleWindow` does the same for `apply` (`vmd/desktop/window.py:622`),
`shutdown` (`:806`), `close` (`:814`) and `report_after_save` (`:656`), and reads
`state()` through defensive `.get()` chains "because the services are handed in"
(`:716`, `:736`).

**Why it matters here.** Every one of those comments says the fallback exists for
a test double. So the production branch and the test branch are different code —
and the fallback branch, the one that skips adoption and just assigns ports, is
the branch that produced a real field failure: a second go2rtc across the radio
link (see the comment at `:1325`). A typo in a method name today takes the silent
branch and nothing says so.

**Instead.** Declare a `StreamingService` `Protocol` beside `Go2rtcService`, make
the fakes implement it, call the methods directly. Same for the window: a
`Services` protocol with `tick/state/apply/local_url`. The existing `VideoPane`
Protocol (`vmd/desktop/video.py:32`) is the model — it works, and nobody
getattr-probes a pane.

**Size:** half a day.

---

### 10. `vmd/desktop/live.py` is a widget that also supervises streams

**What.** `LiveTab` owns the pictures, the steering, the alarm strip, the
movement list, the view chooser and the side column — and a per-stream restart
supervisor with an exponential backoff ladder, a "forgiven after N good readings"
rule, a log throttle and a give-up state
(`vmd/desktop/live.py:124-167`, `1085-1164`).

That last part is `Supervisor` for panes, written a second time, inside a
`QWidget`, keyed by five parallel dicts (`_restarts`, `_next_try`, `_playing_for`,
`_urls`, `_status`) that `apply()` then has to carry carefully across a rebuild
(`:942-971`).

**Why it matters here.** It is the code most likely to be edited by somebody
changing the layout, and it is the code where an early-firing retry has already
cost this project a day. It has no home of its own and cannot be tested without a
`QWidget`.

**Instead.** Lift the ladder into a plain `StreamRestarts` class (name → state,
injected clock, `should_restart(name, state) -> bool`), tested without Qt.
`LiveTab.refresh` becomes: read pane state, ask the object, call `pane.show(url)`.
Nothing on screen changes.

**Size:** half a day.

---

### 11. Six restart/backoff policies — document them, do not merge them

| Where | Rule |
|---|---|
| `vmd/supervisor.py:58` | fixed 2 s between attempts; `FLAPPING_AFTER = 3` short-lived starts |
| `vmd/desktop/services.py:104-118` | 120 s window, `FLAP_LIMIT = 3`, `SPAWN_LIMIT = 5` |
| `vmd/storage/recorder.py:30-31` | 120 s window, `RESTART_LIMIT = 5` stillbirths |
| `vmd/desktop/live.py:138-142` | exponential 2→60 s per stream, forgiven after 5 good heartbeats |
| `vmd/detect/runner.py:41-42` | exponential 1→30 s reopen, per stream |
| `scripts/autostart.ps1` | 45 s fixed delay so the console loses the race deliberately |

Each is well reasoned in situ, and they govern genuinely different things — a
process, an ffmpeg, a decoder, a task trigger. **Do not merge them.** What is
worth an hour is one shared helper for the shape that really is identical in
three places:

```python
self._spawned_at = [at for at in self._spawned_at if at >= cutoff]
```
— `vmd/desktop/services.py:563`, `vmd/desktop/services.py:1418`,
`vmd/storage/recorder.py:242`.

**Size:** an hour for the helper.

---

## Worth knowing (I would leave these alone)

- **The three `_default_spawn` functions** (`vmd/desktop/services.py:1129`,
  `vmd/streaming/go2rtc.py:1060`, `vmd/storage/recorder.py:75`) genuinely need
  three different pipe policies — bytes/`bufsize=0`, text/`bufsize=1`, and
  DEVNULL-plus-a-file-handle-with-`TZ=UTC`. Merging them produces a function with
  three modes. Leave.

- **`CREATE_NO_WINDOW` is set in some spawns and not others** — missing on
  `vmd/storage/recorder.py:90` (ffmpeg), `vmd/desktop/services.py:1076` and
  `:1104`, `vmd/record_main.py:256`, `vmd/desktop/picker.py:676`, and the
  `diagnose` probes. Nothing flashes in practice, because the parents were
  themselves given `CREATE_NO_WINDOW` and the children inherit that hidden
  console. It is an inconsistency, not a defect. Know this before anyone "fixes"
  it by giving ffmpeg a console of its own — `vmd/desktop/services.py:868-877`
  explains why that broke shutdown last time.

- **The `.pid.json` / `.pid.started` split** looks silly until you read
  `vmd/desktop/services.py:762-779`: the bare-integer format has two other
  parsers and must stay a bare integer. Keep the split; unify only the companion
  *format*, under finding 5.

- **Two clocks everywhere** — `clock=time.monotonic` for durations, `now=time.time`
  for cross-process timestamps (`vmd/desktop/services.py:1256`,
  `vmd/detect/runner.py:145-150`). Correct and deliberate on a laptop whose clock
  is set by hand. Do not collapse them.

- **`Go2rtcService._pump_output` drops its thread handle**
  (`vmd/streaming/go2rtc.py:1038`), so it is the one child pump never waited on
  at shutdown — contrast `vmd/desktop/services.py:669` / `:685`. It also mutates
  `self._recent` (a `deque(maxlen=8)`) from the pump thread while `status()`
  reads it on the GUI thread (`go2rtc.py:1029` vs `:562`). Benign under the GIL,
  but it is the only unguarded cross-thread slot in the codebase; everything else
  uses a lock or a signal. Worth a comment more than a change.

- **The airOS login tries two schemes × three flows at 6 s each**
  (`vmd/radio/airos.py:524`, `TIMEOUT = 6.0` at `:52`). The `break` at `:556`
  exits only the inner loop, so a truly unreachable radio costs ~12 s — exactly
  the figure `vmd/radio/service.py:33` documents, and it is paid on a background
  thread. Correct as it stands.

- **Injected clocks, `FakeVideoPane` (`vmd/desktop/video.py:54`), and
  `DiskWatcher` not being a `QObject` (`disk.py:368-374`, so `ConsoleServices` can
  drive it without an event loop)** are seams that describe the design rather
  than patching around a testing problem. Keep all three.

- **`Ultralytics/settings.json`** on the dev machine carries `"sync": true` and a
  MAC-derived `uuid`. I checked: it is gitignored (`.gitignore:37`) and
  `scripts/offline_kit.ps1:257` explicitly excludes the `Ultralytics` directory
  from the kit, and `vmd/__init__.py:56` sets `YOLO_OFFLINE=1` before ultralytics
  can be imported. Nothing to do; worth knowing it exists so nobody adds it to
  the kit.

- **The verbose comments.** Unusual, and the best asset in the repo: nearly every
  constant carries the field incident that chose it. Do not let a tidying pass
  strip them.

---

## Where the documents and the code disagree

| The document says | The code does | Which I believe |
|---|---|---|
| `desktop-console-design.md:89` — the updater "moves into the desktop app unchanged" | `vmd/updater.py` exists, is tested, and is imported by nothing in `vmd/` | **The code.** There is no Update button and no tab for one. Fix the doc or wire the button (finding 7). |
| `desktop-console-design.md:163-166` — `video_mode`/`video_buffer_ms`: "nothing reads them" | `vmd/desktop/settings_tab.py:1299` prints both into the operator's report; `vmd/settings.py:379` validates one | **The document's intent.** Delete the fields (finding 8). |
| `desktop-console-design.md:40` — "Window supervises go2rtc and the recorder as child processes"; "the window owns nothing that must outlive it" | The children outlive the window and are adopted; a scheduled task starts the recorder independently; the recorder arbitrates via its own claim file | **The code**, and the doc is simply older than the deployment. This is the most important thing missing from the design documents. |
| `desktop-console-design.md:94-96` — `record_main.py` and `streaming/*` are "unchanged … none of them ever knew a browser existed" | `vmd/record_main.py:737` still says "status() is about to become a web API"; `Supervisor.health()` exists only for that API | **The code.** Cosmetic, but it is where two dead functions came from. |
| `detection-design.md:53-55` — "One more local consumer of go2rtc costs the radio link nothing — the camera is still pulled once" | `vmd/detect/runner.py:449` moves detection onto the camera directly, permanently | **The document.** The invariant is right; the code violates it (finding 2). |
| `desktop-console-design.md:102-108` — the pane protocol is `show/stop/state`, and "when a detector needs decoded frames, the implementation changes and nothing else does" | Callers special-case `isinstance(pane, QWidget)` (`vmd/desktop/live.py:933`, `:996`; `vmd/desktop/playback.py:226`) and probe for an out-of-protocol `release()` (`vmd/desktop/live.py:927`) | **The code**, and the Protocol should be widened to say so: a pane is a widget and it can be released. Honest and cheap. |
| `disk.py:19-24` — "none of it runs on the GUI thread and none of it runs on the two-second heartbeat" | `read_detection_status` and `EventStore.recent` both do, against the same folder | **The document.** Finding 3. |

---

## Threading: the rules a new contributor needs

There is **one coherent intent and six implementations of it**. The intent is
written down once, at `vmd/background.py:1-24`, and nothing points at it from
anywhere else.

**The intent.** The GUI thread never waits for anything that can be slow. Slow
questions are asked on a worker, the worker stores an answer, the 2 s heartbeat
reads it and draws it. Every stored answer carries its age, so nothing can
present a four-minute-old reading as the state of the world now
(`vmd/background.py:43-64`).

**The six mechanisms:**

1. **Poll-a-cached-value daemon thread** — `BackgroundValue`
   (`vmd/background.py:67`). One question, one thread, at most one read in flight,
   answer polled. Used by the radio (`vmd/radio/service.py:59`) and by "is that
   adopted PID still there" (`vmd/desktop/services.py:441`,
   `vmd/streaming/go2rtc.py:677`). Joined with a 2 s bound on close.
2. **Latest-value mailbox daemon thread** — `PtzCommands`
   (`vmd/ptz/service.py:185`). Hand-rolled `Event` mailbox, coalescing last-wins,
   *except a stop is never dropped*; respawns itself if lost (`:302`); polled from
   `vmd/desktop/live.py:1269`. Joined with a 2 s bound.
3. **Fire-and-forget one-shot daemon thread with a lock-guarded result slot** —
   `DiskWatcher` (`vmd/desktop/disk.py:439`), throttled to 30 s with an
   `_in_flight` guard. **Never joined; it has no shutdown path at all.**
4. **Daemon pipe pump → `logging` → lock-guarded deque** —
   `vmd/desktop/services.py:668` and `vmd/streaming/go2rtc.py:1038`, landing in
   `LogBuffer` (`vmd/desktop/logs.py:100-126`). The first is joined
   (`wait_for_output`, 2 s); the second is not even retained.
5. **`QThreadPool` + `QRunnable` + Qt `Signal`** — the camera tools
   (`vmd/desktop/settings_tab.py:583-602`, `_pool.setMaxThreadCount(1)`) and the
   frame picker (`vmd/desktop/picker.py:485-501`). The only mechanism that pushes
   work *to* the GUI thread instead of being polled from it. Both block on close:
   `waitForDone(5000)` (`settings_tab.py:1286`) and `waitForDone(3000)`
   (`picker.py:988`).
6. **Deadline-and-discard daemon thread** — `BudgetedClassifier`
   (`vmd/detect/classify.py:386-393`), detector process only: a thread per
   classification, `job.done.wait(budget_s)`, answer discarded if late.

Plus the detector's own per-stream worker threads
(`vmd/detect_main.py:219`, joined with a 10 s bound), and three `QTimer`s as the
whole scheduler: the 2 s heartbeat (`vmd/desktop/window.py:479`), the 900 ms
recording blink (`:487`), and 250 ms per video pane (`vmd/desktop/video.py:173`).

**The rules, as I would tell them to somebody new:**

- **Never touch a widget from a non-GUI thread.** Nothing does today, and that is
  worth protecting. The two sanctioned ways back are: store a value and let the
  heartbeat read it, or emit a Qt `Signal` from a `QObject`. Do not invent a
  third.
- **Never use `concurrent.futures`.** Its `atexit` hook joins workers at
  interpreter exit and has already hung a closed console — said twice, at
  `vmd/background.py:20-23` and `vmd/ptz/service.py:281-284`.
- **Every wait is bounded and returns a bool, never raises.**
  `BackgroundValue.close(2.0)`, `PtzCommands.close(2.0)`,
  `ChildProcess.wait_for_output(2.0)`, `DetectionService.wait(10.0)`.
- **Every worker thread is a daemon**, because the children outlive the window on
  purpose.
- **libVLC's threads never reach Qt.** The pane polls a frame counter on a
  `QTimer` rather than subscribing to VLC events
  (`vmd/desktop/video.py:171-175`), and `release()` is once-only because a double
  release is a C-level crash, not an exception (`:224-241`).
- **In the detector, one sqlite connection per thread**, created on that thread
  (`vmd/detect_main.py:227-235`). Do not share an `EventStore` across the stream
  threads.

**The two holes in the rule, and they are findings 3 and 4:** the heartbeat reads
the recordings folder, and Save runs `taskkill` and process waits, both on the
GUI thread. Until those are fixed, "nothing blocks the GUI thread" is a rule with
holes in it — and a new contributor will find the holes by copying the Save path.

---

## Coupling: what a second camera or a second console would break

**A second camera.** `Settings.camera` is one object with one host, one username,
one password (`vmd/settings.py:189`). Everything downstream is keyed by *stream
name* in a flat namespace: go2rtc's config (`vmd/streaming/go2rtc.py:226`), the
recording layout (one directory per stream under one root), the events table,
`wall_view`, `detection.json`. Two cameras that both call a stream `ch1` collide
silently in three places; `CameraSettings._names_are_distinct`
(`vmd/settings.py:195`) is the only guard, and it would go from a nicety to
load-bearing. `PtzService` and `RadioService` are singletons built from
`settings.camera.host` / `settings.radio.host`, and `target_distance_m` is a
single global scalar (`vmd/settings.py:407`). The change that unlocks it is
making `camera` a list and adding a camera id beside every stream name. A week,
and I would not do it speculatively.

**A second console on the same folder.** Mostly works, and deliberately —
adoption exists for exactly this. See the uncertainty below.

---

## What I am unsure about

- **Two consoles open at once.** Adoption handles the recorder and the detector
  cleanly, because the recorder arbitrates its own claim. I could not convince
  myself about go2rtc: `Go2rtcService.apply` on console B calls
  `stop(force=True)`, which `taskkill`s the server console A holds a `Popen`
  handle to. A's `_reap` would then log "go2rtc exited" and A's `Supervisor`
  would start a *third* one on a new port, while A's panes still point at the old
  one. I did not test this, and something I did not read may prevent it.
- **Whether `_pid_alive`'s substring match can produce a false positive.**
  `str(pid) in result.stdout` (`vmd/desktop/services.py:1082`) against
  `tasklist /FI "PID eq N" /NH`. The filter should mean only the right row comes
  back, so I believe it is safe — but it is the weakest of the three liveness
  checks and I did not exercise it against a localised Windows.
- **The overlay-flicker measurement in the console spec** (lines 204–236) was
  taken with `spike/overlay_probe.py` on the development machine, and the spec
  says outright that no pixel-level photograph was obtainable. I have no reason
  to doubt it and no evidence from the target hardware.
- **Whether the detector's per-stream threads really keep the GIL out of the
  way.** `vmd/detect_main.py:123-127` argues decoding releases it. Plausible for
  cv2, unverified here, and it matters on a laptop also decoding two streams for
  the screen. Worth one measurement before anyone adds a third detected stream.
- **Splitting `vmd/desktop/services.py`** (1,772 lines) three ways is probably
  right, and I have not listed it as a finding because a move-only split
  invalidates every `file:line` in this document and in a hundred comments. It
  should ride along with finding 5 rather than happen on its own.

---

## If you only have one day

1. **Finding 1** — make `vmd/detect/__init__.py` lazy and delete the two
   duplicated rules it was forcing. One hour, provable with a one-line check, and
   it makes three misleading comments true.
2. **Finding 3** — get `detection.json` and the events list off the GUI thread
   and behind `BackgroundValue`, and stop computing `state()` twice a heartbeat.
   Half a day. It closes the gap between what `disk.py` says the rule is and what
   the heartbeat does, on exactly the folder that goes away.
3. **Findings 7 and 8** — delete the dead code and the two vestigial settings, and
   correct the two lines in the console spec that are now wrong. Two hours, and it
   is the biggest single reduction in what the next person has to read before they
   can trust anything.

Finding 4 (Save on the GUI thread) is what I would schedule next; finding 5 (one
claim protocol) is what I would do before adding a fourth supervised child; and
finding 2 is the one I would not let slip, because it is the only one that can
quietly cost the live picture.
