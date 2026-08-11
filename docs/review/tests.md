# The test suite, reviewed

1197 tests: 1184 unit (~2m45s) and 13 integration (~1m45s). All green on this machine,
with ffmpeg, ffprobe, go2rtc, libVLC and `yolo11n.pt` present, so nothing skipped.

This is not a bad suite. It was written by somebody who had already been burned, and most
of the scar tissue is real: injected clocks nearly everywhere, fakes that are often
*harsher* than the device they stand in for, bounded waits with named ceilings, and
regression tests for the recorder failure that I verified by hand do fail when the defect
is put back. The problem is not laziness.

The problem is that the rigour is unevenly distributed, and the thin places are not random.
They are (a) the seams between two things that each have good tests, and (b) the *reporting*
half of a failure whose *causing* half has just been fixed. The pcm_mulaw failure lived in
exactly those two places, and both are still occupied.

Three live examples, each verified by running the shipped code:

- Wind the console's clock **backwards** — which this machine does, by hand, and which the
  codebase names as a hazard in three other files — and the recording indicator reads
  "recording" for ever, whatever the recorder is doing.
- Fill the folder with **1-byte** segments instead of 0-byte ones and the console reads
  "recording" again. The fix drew the line at exactly zero.
- Deleting the zero-byte guard entirely leaves **all 30 tests in the file that owns it**
  green. Exactly one test in 1197 notices, and it is in a different file.

That is the same failure class as the one that shipped, one increment along, today.

---

## 1. What this suite is genuinely good at

Say this first, because none of what follows means "start again".

- **Injected clocks, consistently.** `Supervisor(clock=…)` (`vmd/supervisor.py:57`),
  `SegmentRecorder(clock=…)` (`vmd/storage/recorder.py:113`), `ConsoleServices(clock=…)`,
  `DiskWatcher(executor=…, clock=…)` (`vmd/desktop/disk.py:376`). `tests/test_supervisor.py:4`,
  `tests/test_recorder.py:309`, `tests/test_desktop_services.py:317` are hand-wound clocks
  with docstrings saying why. A 30-iteration restart-storm test
  (`tests/test_desktop_services.py:2538`) runs in microseconds. Several tests step the clock
  **backwards** on purpose (`tests/test_record_main.py::test_retention_survives_the_clock_going_backwards`,
  `tests/test_desktop_services.py:1019`, `tests/test_desktop_live.py:879`,
  `tests/test_detect_events.py:130`).
- **Bounded waits, near-universally, with the reason written down.**
  `tests/test_background.py:17` `WEDGE_CEILING = 5.0` / `PATIENCE = 10.0`;
  `tests/test_desktop_services.py:569` `self.release.wait(5.0)`;
  `tests/test_desktop_window.py:644`; `vmd/desktop/live.py:1283`
  (*"the tests, which must fail rather than hang"*);
  `tests/test_detect_classify.py:44` puts the ceiling **in the stub itself**, "bounded even
  if the test forgets". `tests/test_background.py` is the model file in this repo: the
  elapsed-time assertions are independent of the code under test, so a regression that puts
  a read back on the caller's thread *fails* instead of hanging.
- **Fakes harsher than the device.** `tests/test_radio.py:403 RealFirmware` was built from a
  capture of the owner's actual radio and answers a wrong password with **HTTP 200 and a
  body** — the thing that broke the original code. `tests/test_ptz.py:463/:479/:493/:506`
  return SOAP faults behind 200s, login pages behind 200s, unacknowledged stops.
  `tests/test_desktop_playback.py:294 FakeEvents` filters by stream and *deliberately not by
  time*, "a reader that had already dropped those events would prove nothing". These are
  adversaries, not stand-ins that agree with the assertion.
- **The `VideoPane` fake and the Qt discipline around it.** Widget behaviour is driven by
  hand, not polled. `tests/test_desktop_video_vlc.py:146 _StubPlayer` reproduces libVLC's
  drain-at-teardown exactly (`:240`) — 100 flushed pictures read as live frames, a real and
  subtle bug, pinned in a unit test.
- **Round-trip invariants no stub can satisfy.** `tests/test_desktop_settings_tab.py:350`
  (every offered sensitivity survives a real save/load); `:677` (rows removed, added and
  reordered, each stream's detection tuple read back off disk — the actual historical
  position-matching bug); `:732` (a `Q:\` drive: asserts the words, asserts it is *not*
  "Saved.", asserts no traceback, **and** asserts nothing was written).
- **Real OS answers where they matter.** `tests/test_desktop_services.py:1892` tests
  PID-reuse adoption against this process's genuine creation time, in both directions.
  `tests/test_desktop_services.py:1051` is an eight-case malformed-status-file matrix where
  every case carries a *fresh* timestamp, "so that the freshness check cannot quietly stand
  in for the shape check". That is the right instinct, precisely applied.
- **Regression tests that are load-bearing, verified by mutation.** §3.
- **The acceptance test exists and works.** `tests/test_recorder_integration.py:214` runs
  the real console services, real go2rtc, and the real recorder as a real child process,
  against a synthetic camera **that sends pcm_mulaw**, then asks the operator's question:
  is there footage on this day and can Playback find it. Every wait bounded, every failure
  message quoting what the child said. When I put the audio defect back it failed with
  `NOT recording - nothing has ever been written`. Worth more than several hundred of the
  unit tests around it.
- **`tests/test_detect_airgap.py`.** Offline flags checked in a *fresh interpreter* (`:67`),
  the weights file checked *before* the ultralytics import (`:112`), credentials scrubbed.
  Right threat model, right level.
- **`tests/test_imports.py`.** Cheap, catches a whole class, written because that class
  escaped twice.

---

## 2. What it is blind to

**(a) The reporting half of the failure it just fixed.** Recording now genuinely works and
is genuinely tested. Whether the console *tells the truth about it* is broken in three ways
that the suite is green on (§4.1). This is the same shape as before: the console said
"recording" for a whole day. The cause was fixed; the sentence was not.

**(b) Seams between processes.** Every process here is well tested alone. The recorder
writes `segments.db`, the console reads it. The detector writes `events.db`, the console
reads it. go2rtc serves, the recorder pulls. Of those three joins exactly one — the
recorder's — has an end-to-end test, and it only got one *after* the failure. The
detector→console alarm path has none: the Live tab is tested against "a reader with the
EventStore's shape and none of its SQLite" (`tests/test_desktop_live.py:55`), and the one
real-SQLite test (`tests/test_desktop_window.py:413`) writes the event **before** the window
opens. Nothing exercises a second process committing a row into a database the console
already holds open. On a perimeter alarm, that is the seam that matters.

**(c) Character sets and encodings.** The password-redaction regression was fixed and its
test rewritten with a "tricky" password chosen from the alphabet the fix already handles
(§4.3).

**(d) The disk as a physical object.** Retention is a pure function of the *indexed* byte
total and the configured budget (`vmd/storage/retention.py:26`). It never asks how much room
the drive has. `detect_free_bytes` exists (`vmd/settings.py:479`) and is used only to draw a
number in the console — the unattended process that actually deletes footage never consults
it. No test covers a budget larger than the drive, a drive filled by something else, ENOSPC,
a permission-denied stream folder, or a locked/corrupt sqlite file. `tests/test_index.py`
has no concurrency test at all, and deleting `PRAGMA journal_mode=WAL` and `busy_timeout`
from `vmd/storage/index.py:52-53` is a **green mutation**.

**(e) The measured claims the detector is built on.** The source is full of numbers — "7/8
person spans at 30 fps", "93% recall at 35-78 px", "46× fewer detections of parked cars".
The ground truth is in the repo (`footage/walk_3mbps.mp4.labels.json`, `footage/meva/`) and
scored by a hand-run script (`spike/score.py`). `grep -rn "labels.json" tests/` returns
nothing. Every positive-detection test is one uniformly bright rectangle on flat grey, alone
in the world. A recall regression is invisible to the suite.

**(f) The record of what happened.** `grep -rn "FileHandler\|RotatingFile" vmd/` → nothing.
`vmd/desktop/app.py:153` configures logging to stderr, on a program launched without a
terminal, and the whole operator-visible log is a 500-entry RAM ring
(`vmd/desktop/logs.py:32`). After any crash on the always-on laptop there is no forensic
record at all. Not a test gap as such — but it is the reason the pcm_mulaw diagnosis took a
day, and the suite cannot notice its absence.

---

## 3. What I actually checked

Not mutation testing across the suite — too slow. I copied `vmd/`, `tests/` and
`pyproject.toml` into a scratch tree (nothing in the shared repo was touched), broke one
thing at a time, and ran the tests that should have noticed.

| # | Mutation | Result |
|---|---|---|
| 1 | Drop `-an` from `build_command` | **Caught** — `test_recorder.py::test_the_command_records_no_audio` |
| 2 | Drop `-map 0:v:0` | **Caught** |
| 3 | `find_closed_segments` indexes the file ffmpeg still has open | **Caught** — 7 tests, 2 files |
| 4 | `_end_of` never clamps to the next segment's start | **Caught** |
| 5 | `held_back` always `False` (the restart storm returns) | **Caught** |
| 6 | `_adopt_orphans` skips the newest file again (**the original defect**) | **Caught** — 3 tests |
| 7 | `_notice_empty_segments` counts the live file too | **Caught** |
| 8 | `is_live` always `True` | **Caught** |
| 9 | **Delete the zero-byte guard in `vmd/desktop/disk.py:176`** | **`test_desktop_disk.py`: all 30 pass.** One test in the whole suite catches it, in another file (`test_desktop_services.py:2745`) |
| 10 | `-segment_format mp4` → `mpegts` (files still named `.mp4`) | **SURVIVES** — unit *and* integration |
| 11 | Drop `-reset_timestamps 1` | **SURVIVES** |
| 12 | Drop `-strftime 1` | **SURVIVES** |
| 13 | Drop sqlite `WAL` + `busy_timeout` | **SURVIVES** |
| 14 | Reintroduce the real audio defect (`-c copy`, no `-an`), run **integration** | **Caught** — 3 integration tests fail, including the acceptance test |

Run directly against shipped code, all confirmed by execution:

- `read_disk(..., now = now - 1 day)` on a folder written a second ago →
  `writing=True, write_problem=None`. At `now - 1 year`, still `True`.
- A **1-byte** `.mp4` → `writing=True, used_bytes=1`.
- `ConsoleServices` with `storage.root = Q:/never-existed` and a live `FakeProcess` →
  `state()["recording"] is True`, `reason = "recording"`.
- `tests/test_desktop_fullscreen.py`'s `in_the_worst_state()` produces the stream label
  `'thermal  -  connecting'` (22 chars) and `_restarts == {'thermal': 1, 'visible': 1}` —
  nowhere near `GIVING_UP_AFTER = 6`. The 66-char sentence the fixture's own comment says
  it is measuring never appears.
- `vmd.radio.airos.redact()` leaves a password fully intact when it contains `"`, `\`, or
  any non-ASCII character.
- `vmd.ptz.encoder.fit_to_link` allocates **1708 kb/s against a 1000 kb/s ceiling** for a
  five-stream camera.
- `day_bounds(2026,10,25)` is 25 hours; `vmd/desktop/playback.py:134` draws its hour ruler
  as `hour / 24.0 * width`. The `12` label lands 30 minutes away from real noon and the
  25th hour has no tick.
- A typo'd `@pytest.mark.integraton` runs **in the unit suite** — warning only, and
  `addopts = "-q"` buries it.

Mutations 10-12 are the honest edge of the recorder's coverage: the tests assert the
*command string*, the integration tests assert *a playable file with one video stream*, and
a container or timestamp change fits between them. Low probability; noted, not urgent.

---

## 4. Findings, worst first

Size key: **S** ≈ under an hour, **M** ≈ half a day, **L** ≈ a day or more.

### 4.1 — "Recording" is still a lie the suite cannot detect · **P0** · **M**

Three independent holes in the one sentence that told the operator nothing was wrong for a
whole day. All three verified against shipped code.

**(a) A clock stepped backwards means "recording" for ever.**
`vmd/desktop/disk.py:238`

```python
    silence = now - newest
    if silence <= limit:
        return True, None
```

A negative `silence` passes. This machine's clock is set by hand and corrected by NTP after
boot; the codebase names that hazard in three other places and tests it in two of them
(`tests/test_desktop_services.py:1019`, `tests/test_desktop_live.py:879`) — never here.
Every clock in `tests/test_desktop_disk.py` moves forward only (`:323`).

**(b) A 1-byte segment reads as footage.** `vmd/desktop/disk.py:176` — `if stat.st_size <= 0:
continue`. The fix drew the line at exactly zero; ffmpeg writing a partial header and dying
is the same failure one increment along. No test uses a segment smaller than 4 MB
(`tests/test_desktop_disk.py:53`). Deleting the guard leaves all 30 tests in that file green
(verified); the single test that catches it lives in `tests/test_desktop_services.py:2745`.

**(c) `ConsoleServices` with no disk reading yet reports process liveness as footage.**
`vmd/desktop/services.py:1664` — `reading = self.disk.reading` is `None` until the watcher
has polled, and the check below it is skipped. Verified: a storage root of
`Q:/never-existed` plus a live fake process gives `recording: True, reason: "recording"` —
the exact pre-fix semantics that `recording_state()`'s docstring exists to abolish. **13
`ConsoleServices(...)` sites omit `disk=`**, including the shared helper
`tests/test_desktop_services.py:351` used by ~20 tests. So
`tests/test_desktop_services.py:306` `assert state["recording"] is True` and `:494` prove
that a process object exists, and nothing else.

*Instead:* one parametrised test over `recording_state()` with a real polled `DiskWatcher`:
clock stepped back an hour; a folder of 1-byte files; a root that does not exist; a folder
whose only stream directory raises `PermissionError`. Assert `running is False` and that the
reason names the cause. Then make `disk=` a required argument of `ConsoleServices` so the 13
sites cannot omit it.

### 4.2 — Nothing tests the detector→console alarm across a process boundary · **P0** · **M**

`tests/test_desktop_live.py:55` — *"A reader with the EventStore's shape and none of its
SQLite."* — and `tests/test_desktop_window.py:413`, which writes the event and closes the
store *before* the window opens.

The console opens one long-lived `EventStore` at construction (`vmd/desktop/window.py:402`,
`:509`) and holds it for the life of the window. The detector is a separate process writing
to the same file. Whether the console's existing connection sees rows another process
commits — WAL visibility, no stale read transaction, `recent()` issuing a fresh query — is
never exercised. Every half is green.

That is the recorder failure's exact structure, aimed at the alarm instead of the archive,
and the alarm is the half of this product that is meant to save someone.

Adjacent and equally untested: a pane stuck in `"connecting"` for ever is neither restarted
nor escalated (`vmd/desktop/live.py:1111` — connecting/late/stopped all fall through), and
`grep -rn "connecting" tests/` finds nothing outside `test_desktop_video.py`.
`FakeVideoPane` has no way to hold that state. This is the go2rtc-up / camera-401 case the
source comments say already cost a day: a muted grey "connecting" for ever, no alarm.

*Instead:* an integration test — a child process writes an event into `events.db` while the
console holds it open; `window.heartbeat()`; assert `alarm_visible()` and the stream and time
in `alarm_text()`. Plus a `FakeVideoPane.pretend_connecting()` and a test that a pane held
there is escalated.

### 4.3 — The password-redaction regression test cannot fail against the current defect · **P0** · **S**

`tests/test_radio.py:20`

```python
TRICKY_PASSWORD = "p@ss word/1"
```

`redact()` (`vmd/radio/airos.py:110`) knows three encodings: plain, `quote(safe="")`,
`quote_plus`. `_login_api` (`vmd/radio/airos.py:628`) posts `json.dumps(...)`, and JSON
escaping is a fourth. Verified by running it:

```
'p@ss word/1'  -> '{"error": "rejected ***"}'          redacted
'a b"c\d'      -> '{"error": "rejected a b\"c\\d"}'    INTACT
'סיסמה'         -> '{"error": "rejected סיסמה"}'         INTACT (as \uXXXX)
```

This is the original defect one encoding along: the chosen password contains only characters
`redact` already handles, so `test_the_password_is_never_in_the_message_in_either_form`
(`tests/test_radio.py:345`) **cannot fail**. Same password reused at
`tests/test_probe_radio.py:28`.

*Instead:* parametrise every redaction test over an adversarial set and assert that no
*decoding* of the output yields the password, rather than that a literal is absent.

### 4.4 — Passwords reach files meant for strangers, by three routes, none tested · **P0** · **S**

- `spike/probe_radio.py:817` — `print(f"  [ ] {note}")`. Every other print in that function
  redacts (`:802`, `:829`, `:846`, `:853`, `:860`, `:863`). `notes` are built at `:467` from
  the radio's own echoed body, so a radio that quotes the form it rejected puts a
  percent-encoded password on the line. The regression test
  (`tests/test_probe_radio.py:432`) covers `exchange` only; the helper at
  `tests/test_probe_radio.py:47` takes a `notes` argument and **no test ever passes one**.
- `vmd/streaming/diagnose.py:248` — `lines.append(f"  typed : {stream.url}")`, verbatim.
  `secrets_of` (`:53`) and `SettingsTab._secrets` (`vmd/desktop/settings_tab.py:1377`) both
  know only the two password *fields*, so `rtsp://admin:s3cret@10.0.0.2/ch2` typed into the
  address box lands whole in the saved report. The test that looks like it covers this
  (`tests/test_desktop_camera_tools.py:87`) builds its URL *from* `camera.password`, so it
  only ever exercises the field already known.
- `vmd/streaming/check.py:63` — `print(f"  password : {camera.password}")` by explicit
  policy (`:8`), with **zero tests of any kind** and a `time.sleep(4)` and real sockets
  besides.

*Instead:* one test that walks every operator-facing output function with a settings object
whose passwords are the adversarial set from 4.3 — including one embedded in a stream URL —
and asserts none appears. One test closes the whole category.

### 4.5 — Nothing stops a test hanging, and ~43% of the unit suite waits on sockets · **P0** · **S**

Measured with `--durations=30`:

| test | cost | waiting for |
|---|---|---|
| `tests/test_radio.py:374`, `:615`, `:233` | 12.15s, 12.12s, 12.11s | `AirOsRadio("192.0.2.99:8", …)` |
| `tests/test_radio.py` (cache test) | 4.50s | same |
| `tests/test_ptz.py:214`, `:236`, `:207` | 4.36s, 2.16s, 2.15s | `192.0.2.99:81` |
| `test_record_main.py::test_falls_back_to_the_camera_when_the_streaming_server_is_gone` | 1.52s | `is_live` timeout, real socket |
| `tests/test_detect_main.py`, `tests/test_desktop_picker.py` | 1.51s, 1.53s | same |
| `tests/test_streaming.py:869` | 1.50s | `API_TIMEOUT`, real socket |

`192.0.2.0/24` is TEST-NET-1: a black hole. Every one of those waits out the **full**
`airos.TIMEOUT = 6.0`, twice, because nothing ever answers. Roughly 70s of ~165s spent
proving that a SYN to nowhere times out.

Two consequences, and the second is the one that matters:

1. Slow for a bad reason. The behaviour under test is *error classification* ("an
   unreachable radio must not be accused of a bad password"), which needs no socket at all.
2. **There is no `conftest.py` anywhere in the repo and `pytest-timeout` is not installed.**
   Nothing bounds a test that blocks. This is the structural cause of "three mutations hung
   the suite instead of failing it", and it has not been addressed — only the three specific
   mutations were. Supporting evidence: `tests/test_desktop_logs.py:305` and `:307` are bare
   `join()` calls with no timeout, and `logger.removeHandler` at `:308` is not in a
   `finally`, unlike every other test in that file. `tests/test_radio.py:592 PATIENCE = 15.0`
   sits 3 seconds above a measured 12.13s wait: raise `airos.TIMEOUT` from 6 to 8 and it
   flips into a confusing failure.

*Instead:* `pytest-timeout` in the dev group, `timeout = 60` / `timeout_method = "thread"` in
`[tool.pytest.ini_options]`; a `tests/conftest.py` with an autouse fixture failing any test
that opens a socket to anything but loopback; the `192.0.2.99` tests rebuilt on an injected
transport. Expect the unit suite well under 90 seconds.

### 4.6 — The settings form says "Saved." for four states that break recording · **P1** · **M**

Verified by execution: `save()` returns `True` and the message reads `"Saved."` for each of

| typed | result |
|---|---|
| address = `thermal` | stored as-is — `vmd/settings.py:122` accepts a bare word (`len(scheme) == 1` is the drive-letter case) |
| address = `rtsp://` (no host) | saved, while the form's *own* diagnostic says "This address has no host in it." |
| Budget = `0.0000001` GB | `budget_bytes = 107` — retention deletes the archive as fast as it is written |
| Folder = *(blank)* | root silently becomes `…\recordings` — `vmd/desktop/settings_tab.py:1114` |

And `min_travel_px = "99999"` saves clean, switching perimeter detection off entirely: the
model refuses negatives only (`vmd/settings.py:321`) and the only tested value is `-3`
(`tests/test_desktop_settings_tab.py:571`).

The address tests use the empty string (`tests/test_desktop_settings_tab.py:70`) and the
budget tests use `-5` (`:87`) — friendly values, the same pattern that shipped the zero-byte
bug. The write probe is no better: `vmd/desktop/settings_tab.py:546` writes a **zero-byte**
file, which succeeds on a 100%-full volume, and `detect_free_bytes` is never called there.

*Instead:* a table-driven test of every settings field over plausible-but-wrong values —
a bare word, a scheme with no host, a budget three orders of magnitude out, a blank folder,
a travel threshold larger than the frame — asserting the message is *not* "Saved." and that
nothing was written. `tests/test_desktop_settings_tab.py:732` already does this correctly
for the `Q:\` drive; copy that shape.

### 4.7 — The fullscreen suite measures the wrong sentence · **P1** · **S**

`tests/test_desktop_fullscreen.py:204`

```python
    for _ in range(8):  # past GIVING_UP_AFTER, which is the longest stream line
        live.refresh()
```

`pretend_failed()` is called once, *before* the loop. Refresh #1 restarts the pane and
`vmd/desktop/video.py:73` sets `_state = "connecting"`; refreshes #2-#8 read `connecting` and
overwrite the label, and `vmd/desktop/live.py:1133` bails on all of them anyway. Verified:
the label is `'thermal  -  connecting'` (22 chars) and `_restarts == {'thermal': 1,
'visible': 1}` against `GIVING_UP_AFTER = 6`. The 66-character `GIVEN_UP_WORDS` sentence
never appears. **All 19 stream-line layout assertions in the file are measuring a 22-char
string in place of a 66-char one.** `LiveTab` takes a `clock:` argument
(`vmd/desktop/live.py:442`) but `ConsoleWindow.build_live` never forwards it, so the seam
exists and the test cannot reach it.

Two further softenings in the same file: `speakers()` (`:291`) skips widgets of zero width
or height, so a field squeezed to 5 px is caught but one collapsed to **0** is dropped
before the checks see it; and `zip()` at `:383` and `:446` makes an empty tab bar pass
`assert problems == []`.

*Instead:* forward the clock into `LiveTab`, call `pretend_failed()` inside the loop, and
assert the label text *contains* `GIVEN_UP_WORDS` before measuring anything. Replace the
zero-size skip with an explicit "collapsed to nothing" problem.

### 4.8 — Rules whose tests pass because something else does the work · **P1** · **M**

The wind-rule pattern, still present in four places:

- `tests/test_detect_pipeline.py:124` — *"A pan, or wind shaking the mast. Every blob in the
  frame is discarded."* With `is_global_motion` forced to `False` the pipeline still returns
  **0 detections**: the rolled noise frame makes one huge blob that `implausible_size`
  rejects as `too_large`. `assert pipeline.frames_suppressed > 0` (`:145`) stays green even
  against a half-broken rule that counts the frame and keeps the boxes, and `assert fired is
  True` (`:141`) calls `is_global_motion` on blobs from a *separate* `MotionFinder` — the
  function asserting on its own input. The case the rule exists for (a PTZ slew: many medium
  blobs, none individually `too_large`) is untested.
- `tests/test_detect_motion.py:53` — replacing `merge_overlapping` with the identity function
  gives an identical result: the rectangles overlap *in the image*, so OpenCV already
  returned one contour. The case the rule exists for (`vmd/detect/motion.py:38`, "a person
  split by a fence post") never reaches the merge through `blobs()`.
- `tests/test_detect_motion.py:33` — the six-frame warm-up that caused the original wind
  failure still lives in this file's helper (`frames=8`), though
  `tests/test_detect_pipeline.py:35` raised its own to `WARMUP_FRAMES = 200` with a full
  explanation. Fed the flicker pattern, an 8-frame-warmed finder yields `[1,0,1,0,0,0]` —
  the subtractor swallows it after two cycles, exactly as before. Every future test written
  with this helper inherits the vacuity.
- `tests/test_detect_pipeline.py:68` — the wind test itself **is** load-bearing today
  (verified: neutralise the travel rule and it fails). But nothing pins it there. The
  neighbouring test at `:242` asserts `pipeline.blobs_seen > 0`; the file's self-declared
  most important test does not. One tuning change to `var_threshold` or `min_area` returns it
  to "passes because nothing moved", silently.

*Instead:* every suppression test gets a companion assertion that the input actually reached
the rule. Two lines each for `:68` and `:124`, and they are the two that matter.

### 4.9 — The integration tests skip themselves into green · **P1** · **S**

`tests/test_recorder_integration.py:16`, `:234`; `tests/test_desktop_video_vlc.py:28`,
`:355`; `tests/test_streaming.py:284`; `tests/test_detect_classify.py:826`.

On a machine without ffmpeg, ffprobe, go2rtc, libVLC or `yolo11n.pt`, the only tests that
would have caught the pcm_mulaw failure silently vanish and the run still reports success.
There is no CI in this repo, so the suite is only run by hand, and a hand-run on a fresh
checkout gets a green bar with the acceptance test absent.

Related: `addopts = "-q"` does not deselect `-m integration`, so a plain `pytest` runs the
real go2rtc spawns and the two `time.sleep(3)` fixtures every time — 271s rather than 165s,
which is the sort of thing that makes people stop running it.

*Instead:* honour `VMD_REQUIRE_INTEGRATION=1` — every one of those skips becomes a failure —
and print a session-end line naming which external tools were found and which integration
tests were therefore skipped. The second alone would have made the absence visible.

### 4.10 — Threshold tests taken far from the threshold · **P1** · **M**

- `tests/test_ptz.py:296` — `fit_to_link` with **two** configs at a 5000 kb/s ceiling, then
  `assert sum(...) <= 5000`. Two things cannot fail: the `max(256, …)` floor at
  `vmd/ptz/encoder.py:215` never binds with two large streams, and the assertion is against
  the ceiling rather than the `* 0.75` margin the function promises, so deleting the margin
  is a green mutation. With five streams the floor genuinely breaks the ceiling — verified:
  **1708 kb/s allocated against a 1000 kb/s link**. On a ~5 Mb/s radio link this is the
  function that decides whether panning knocks the thermal picture out.
- `tests/test_detect_filters.py:63` — threshold 4.8 px, input 2 px; the "plausible" case is
  20 px against a 4.8-120 px window. (The *height* rules at `:93-94` are tested at the
  boundary — that is the pattern to copy.)
- `tests/test_detect_motion.py:107` — `min_area=2000` against a ~40 px contour, 50× under,
  while the shipped default `DEFAULT_MIN_AREA = 40` sits at that speck's size and is never
  tested at its own boundary.
- `tests/test_desktop_disk.py` drive-colour tests all use `budget_gb=100.0` (`:228`, `:241`,
  `:252`), and the alarm margin is derived from the budget
  (`vmd/desktop/disk.py:317`) — so with a small or disabled budget and 200 MB free the panel
  is calm. There is no absolute floor and no test of one.

### 4.11 — Retention knows a budget, not a disk · **P1** · **M**

`vmd/storage/retention.py:26`, `RecordingService._apply_retention` in `vmd/record_main.py`, and
both retention test files.

`plan_retention` takes `budget_bytes` and `retention_days` and nothing else. `used_bytes` is
the sum of *indexed* segments, excluding zero-byte files, `*.ffmpeg.log`, `events.db`,
`segments.db` and its WAL, and anything else on the volume. So a 500 GB budget on a 250 GB
drive means the drive fills, retention never fires, ffmpeg starts failing, the console
reports it (good) and nothing fixes it. Untested, as is ENOSPC on the index write, and a
locked or corrupt `segments.db`.

Meanwhile the console asserts the opposite: `vmd/desktop/disk.py:287` prints "the oldest
footage is being deleted to keep recording" unconditionally on `headroom <= 0`, while
`vmd/storage/retention.py:153` logs `could not delete` and carries on. `_stuck_deletions`
exists in `RecordingService.status()` and does not reach that sentence.

### 4.12 — `--ff-only` is the updater's whole safety story and is untested · **P1** · **S**

`vmd/updater.py:11` calls it that. `tests/test_updater.py:78` passes with `--ff-only`
deleted, because plain `git pull` against *local edits* produces the same "would be
overwritten by merge" wording that `_pull_failure` (`vmd/updater.py:164`) matches. No test
ever creates a **diverging local commit**, so the `"not possible to fast-forward"` branch
(`:170`) is dead in the suite.

Worse for this deployment: `vmd/updater.py:119` runs `uv sync --extra detect` with no
`--offline --frozen`, `_run` timeout 600s — pressing Update on the air-gapped laptop can be
a ten-minute silent hang. The whole `uv sync` branch (`:117-127`) is never executed by any
test, because the fixture repo has no `pyproject.toml`. `tests/test_launcher.py:72` exists
to enforce exactly this rule elsewhere.

### 4.13 — Tests that assert current behaviour rather than required behaviour · **P2** · **S**

- `tests/test_desktop_logs.py:195` — `tab.follow_checkbox.setChecked(False)`, then `:208`
  asserts "a scrolled-up operator must not be yanked to the bottom". The shipped default is
  checked (`vmd/desktop/logs.py:181`), and `:295` is `follow or at_bottom` — so in the
  configuration the operator actually has, they *are* yanked every 2s, mid-incident. The
  requirement is stated correctly and verified only where it does not apply.
- `tests/test_desktop_services.py:1842` — `assert in_flight.name not in adopted`, with a
  docstring saying "if this ever passes, `_adopt_orphans` has changed and this note is
  stale". Self-aware, and still a defect written down as a specification.
- `tests/test_detect_runner.py:808` — `assert detector.config.ignore_mask is first`. Object
  identity is an implementation detail; a correct implementation that repaints an equal mask
  fails this.
- `vmd/desktop/playback.py:134` draws the hour ruler as `hour / 24.0 * width` while the
  coverage bars normalise by the true span. `day_bounds` was fixed for DST and given three
  tests (`tests/test_desktop_timeline.py:73`, `:82`, `:90`); the ruler was not, and no test
  compares the two. On 2026-10-25 at 1000 px the `12` label sits 30 minutes away from noon
  and the 25th hour has no tick.

### 4.14 — Assertions that cannot fail · **P2** · **S**

Individually small; collectively they inflate the count and dilute the signal. Verified
examples:

- `tests/test_desktop_window.py:167`, `:206`, `:409`, `:500` — `assert "recording" in
  text.lower()`, which passes on `"NOT recording - restarted 20 times in the last 2
  minutes"`. Four status-line tests satisfied by the catastrophic message.
- `tests/test_desktop_disk.py:195` — `assert any("deleted" in text.lower() for text in
  alarmed + [t for t, _ in lines])`. Concatenating the full line list makes the colour filter
  inert; recolouring the sentence to `PALETTE["muted"]` leaves all 30 tests green.
- `tests/test_desktop_logs.py:117` — `set_level_filter("WARNING")`, the single input at which
  the broken implementation (`vmd/desktop/logs.py:268`: any non-`"ALL"` value gives
  WARNING+ERROR+CRITICAL) and the correct one agree.
- `tests/test_desktop_logs.py:261` — a 20-iteration timing loop against an early return
  (`vmd/desktop/logs.py:289` signature check), asserting `< 50 ms` on a dict compare.
- `tests/test_desktop_logs.py:312` — `assert len(final) == 50` asserts what `deque(maxlen=50)`
  guarantees; deleting the lock leaves it green.
- `tests/test_desktop_steering.py:74` — `assert -1.0 <= pan <= 1.0` against
  `vmd/desktop/steering.py:35`'s own clamp, 10 201 times. `:102` is an algebraic identity.
- `tests/test_desktop_timeline.py:143` and `:44` follow structurally from
  `vmd/desktop/timeline.py:71-74`.
- `tests/test_desktop_camera_tools.py:84` — asserts a secret is absent from a file written by
  a stub two lines above that never contains it. `:68` asserts the identity function.
- `test_record_main.py::test_status_reports_what_the_ui_needs` — key presence only (`assert "streams" in status`, `assert "used_bytes" in status`, …); every value could be wrong.
- `tests/test_detect_main.py:241` — `assert state["blobs"] >= 0` on a non-negative int by
  construction.
- `tests/test_detect_classify.py:486` — `all([])` is `True`; if the worker finished or the
  thread name changes, this passes having checked nothing.
- `tests/test_ptz.py:244` — `assert "zoom" not in status or status["zoom"] is None`, in a test
  named `..._when_the_camera_reports_one`. The fake never reports one.
- `tests/test_ptz.py:230` — `test_the_password_is_never_sent_in_clear_text` inspects request
  *bodies*; the clear-text risk is the `Authorization: Basic` **header**
  (`vmd/ptz/onvif.py:181`), which `FakeCamera` never records and never provokes.
- `tests/test_ptz.py:236` — lines 238-239 set `ptz.capability.profile = ""` and then never use
  `ptz`; the raise comes from unreachability. `vmd/ptz/onvif.py:253` is untested.
- `tests/test_updater.py:110` — matches `git pull` in a *string literal*
  (`vmd/updater.py:107`), not the command executed.
- `tests/test_desktop_services.py:2542` and `:1320` assert against the module's own constants;
  set `SPAWN_LIMIT = 10000` and the storm test still passes.

30 occurrences of bare `assert x is not None` across `tests/`; most are guards before a real
assertion, some are the whole test (`tests/test_probe_radio.py:133`,
`tests/test_discovery.py:65`, `tests/test_package.py:5`).

### 4.15 — Fakes kinder than reality · **P2** · **M**

- `tests/test_desktop_services.py:39 FakeProcess` has no `pid` and no `stdout`, so
  `vmd/desktop/services.py:931`'s tree-kill path is skipped entirely and the output reader
  never runs. `wait(timeout=None)` returns `0` unconditionally: no fake process anywhere
  raises `TimeoutExpired` from `wait()` except `StubbornProcess` (`:209`).
- **Every playback test's `.mp4` does not exist on disk** — no playback test ever creates a
  file, and `vmd/desktop/playback.py:458` never checks. The test named for this case,
  `tests/test_desktop_playback.py:463`, deletes no file; it omits the *index row*. The real
  field case (`vmd/storage/retention.py:163` deletes the file and keeps the row) is the
  opposite, and is untested.
- An unreadable stream folder is byte-identical to an empty archive
  (`vmd/desktop/disk.py:179` swallows `OSError`): `Budget: 0 KB of 100.0 GB used (0%)`, all
  in calm ink. No permission-denied fixture anywhere.
- `tests/test_desktop_camera_tools.py:50` models a 144-second network probe as
  `find_paths=lambda s, on_progress: []` in 10 of 12 tests; `:171` has the unreachable
  camera answer in zero milliseconds.
- `tests/test_desktop_window.py:43 FakeServices.state()` returns a shape production never
  produces — no `"storage"`, no `"recording_state"`, though `vmd/desktop/services.py:1575`
  always writes both. Every window test exercises the `.get()` fallbacks.
- `tests/test_desktop_camera_tools.py:40` — the one codec fixture is still one video codec,
  no audio line, no second stream. The tests were rewritten after the incident; the fixture
  was not.
- `FakeCapture` (`tests/test_detect_runner.py:46` and two siblings) always returns instantly
  and always returns a whole frame; `frame()` is `np.zeros((8, 8))`, i.e. every runner test
  runs against exactly the blank frame `vmd/detect/runner.py:571` exists to detect.

### 4.16 — `Supervisor.health()` is tested and consumed by nothing · **P2** · **S**

`tests/test_supervisor.py:193-273` is excellent — flapping asserted in both directions plus
recovery. But `grep -rn "flapping\|short_lived\|settled\|\.health()" vmd/` finds no caller
outside `vmd/supervisor.py`. `RecordingService.status()` reports only `supervisor.restarts`
— the very number the module docstring says is meaningless alone — and
`vmd/desktop/services.py` implements its own `FLAP_WINDOW` logic instead. The
`_say_if_flapping` log line does reach the operator, so this is not dead code; but the
structured verdict the tests are written around reaches no UI.

Also unguarded: `vmd/supervisor.py:90` reads `entry.service.running` inside `tick()` with no
`try`, while `health()` guards the identical call at `:169`. A raising `running` property
propagates out and skips every remaining service, breaking the file's headline promise. Tests
cover `start()` raising (`tests/test_supervisor.py:106`) and `stop()` raising (`:120`);
nothing covers `running` raising.

### 4.17 — Suite mechanics · **P2** · **S**

`pyproject.toml:37-43` — `addopts = "-q"` and a registered `integration` marker, and that is
all.

- No `--strict-markers`: verified, `@pytest.mark.integraton` runs **in the unit suite** with
  a warning that `-q` hides.
- No `-W error` / `filterwarnings`, so deprecations accumulate unseen.
- No `conftest.py` anywhere: nowhere to put a socket guard, a global timeout, a `TZ` pin, or
  a shared process fake. `FakeProcess` is redeclared in `tests/test_recorder.py:9`,
  `tests/test_record_main.py:30`, `tests/test_streaming.py:28` and
  `tests/test_desktop_services.py:39` — four slightly different fakes of one object, each
  free to drift kinder than the others.
- No ordering randomisation, so order dependence between the 2700-line files is invisible —
  and there is a real one: `vmd/desktop/logs.py:132` compares handlers by identity, so every
  `ConsoleWindow` appends another root-logger handler and never removes it
  (`vmd/desktop/window.py:397`). `tests/test_desktop_fullscreen.py` builds 19; alphabetical
  collection puts it before `test_desktop_logs.py`, whose unbounded `join()` at `:305` then
  runs with ~20 handlers attached.
- `tests/test_desktop_timeline.py:79` and `:87` (`== 25 * 3600`, `== 23 * 3600`) are true
  only in a zone whose DST transitions fall on those dates. Verified: correct here because
  the machine is on Jerusalem time. Nothing pins `TZ`.
- No CI. The suite runs when somebody remembers.

### 4.18 — Unit tests that are really integration tests · **P2** · **S**

Unmarked, so `-m "not integration"` runs them anyway:

- `tests/test_desktop_services.py:270` runs a real `taskkill /F /T /PID 999999`, and
  `DeadOnArrival.pid = 31337` (`:334`) is fed to code that runs a real
  `tasklist /FI "PID eq 31337"` — 13 real subprocesses across five tests, and the reason
  `test_a_detector_that_will_not_stay_up_is_reported_not_hidden` costs 1.7s. It is safe on
  Windows only by the undocumented invariant that PIDs are multiples of four; on any Linux
  runner 31337 is an ordinary PID, `_pid_alive` becomes `os.kill(pid, 0)`, and
  `assert detection["restarts"] >= 4` (`:455`) fails.
- `test_record_main.py::test_main_runs_a_single_pass_over_a_file_source` and
  `::test_main_claims_the_recorder_and_gives_it_back` call `main()` with no injected `spawn`, so they
  launch a **real ffmpeg** against a garbage file — green either way, because the `_stage`
  guard swallows it.
- `::test_records_from_the_local_streaming_server_when_it_is_running` binds a real listening
  socket; `::test_falls_back_to_the_camera_when_the_streaming_server_is_gone` waits the full
  1.5s `is_live` timeout.
- `tests/test_streaming.py:814` spins up a real `ThreadingHTTPServer`; `:869` binds a port,
  closes it, then connects to it — a race with anything else on the machine.
- `tests/test_desktop_video_vlc.py:190`, `:221`, `:240` construct a real `VlcVideoPane` and
  `import vlc` with no `skipif`. Without libVLC, `import vlc` calls `sys.exit(1)` and these
  error rather than skip.

### 4.19 — `frame_index` density is an undocumented contract · **P2** · **S**

`vmd/detect/runner.py:394` increments `_frame_index` only on a successful read, and
`confirmed()` counts in index units. Feed a gapped index (a link dropping 2 frames in 3) and
detection returns **0** — the detector goes blind. Nothing in `tests/test_detect_pipeline.py`
ever feeds a non-contiguous index; `tests/test_detect_runner.py:485` pins contiguity only
incidentally. One line in `runner.py` — switching to a PTS or `CAP_PROP_POS_FRAMES` — leaves
the perimeter unwatched with the suite green.

### 4.20 — Wall-clock performance assertions · **P3** · **S**

`tests/test_detect_pipeline.py:309` (`elapsed_ms < 20.0`),
`tests/test_desktop_services.py:2721` (`spent < 1.0`) and `:2135` (`elapsed < 0.5`),
`tests/test_desktop_window.py:810` (`slowest < 0.3`), `tests/test_streaming.py:440`.
These are the ones to look at first when somebody reports an intermittent failure. Several
are defensible — they bound a sleep that was deliberately removed — but they should be an
order of magnitude looser than the thing they are guarding against.

---

## 5. The tests I would add first, in order

1. **"Recording" must mean footage.** One parametrised test over `recording_state()` with a
   real polled `DiskWatcher`: clock stepped back an hour; a folder of 1-byte segments; a
   storage root that does not exist; a stream folder that raises `PermissionError`. Assert
   `running is False` and that the reason names the cause. Then make `disk=` required on
   `ConsoleServices`. *Would have caught:* three live defects that are green today, each of
   which reproduces the console-said-recording half of the failure that started this review.

2. **The alarm crosses the process boundary.** A child process writes a row into `events.db`
   while the console holds it open; `window.heartbeat()`; assert `alarm_visible()` and the
   stream and time in `alarm_text()`. *Would have caught:* the detector→console seam, which
   today has exactly the "each half is green" structure the recorder had on the day it
   shipped 24 empty files. Integration-marked, bounded at ~10s.

3. **Redaction against an adversarial alphabet, everywhere at once.** Parametrise over
   `p@ss word/1`, `a b"c\d`, `pa#ss?q=1&r`, `pw%20x`, a non-ASCII password, and one embedded
   in a stream URL; run every operator-facing output — `airos.redact`, `probe_radio` notes
   and report, `diagnose`, `SettingsTab` report, `streaming/check` — asserting no *decoding*
   of the output yields the password. *Would have caught:* the live JSON-escaping leak, the
   unredacted `notes` line, the typed-URL password in the saved report, and `check.py`'s
   whole policy.

4. **`pytest-timeout` at 60s, plus a `conftest.py` socket guard and a pinned `TZ`.** Not a
   test; the thing that makes every future test fail instead of hang. *Would have caught:*
   the three mutations that hung the suite — as 60-second failures with stack traces rather
   than a wedged terminal.

5. **`VMD_REQUIRE_INTEGRATION=1` turns every external-tool skip into a failure**, plus a
   session-end line naming what was found. *Would have caught:* the acceptance test being
   absent on a fresh machine, which is the state in which a green bar means least.

6. **The settings form refuses what breaks recording.** Table-driven over a bare-word
   address, a scheme with no host, a 0.0000001 GB budget, a blank folder, `min_travel_px =
   99999`; assert the message is not "Saved." and nothing was written. *Would have caught:*
   five ways an operator with no terminal can silently switch the system off.

7. **The suppression companions.** `assert pipeline.blobs_seen > 0` in
   `tests/test_detect_pipeline.py:68`, and a real PTZ-slew case (many medium blobs, none
   `too_large`) at `:124`. *Would have caught:* the original wind failure, and stops both
   tests quietly returning to it.

8. **The fullscreen suite measures the sentence it names.** Forward the clock into `LiveTab`,
   call `pretend_failed()` inside the refresh loop, assert `GIVEN_UP_WORDS` is on screen
   before measuring. *Would have caught:* 19 layout assertions silently checking a 22-char
   label instead of a 66-char one.

9. **A drive smaller than the budget still gets footage deleted.** Inject
   `detect_free_bytes`; assert `_apply_retention` frees space when the drive is nearly full
   though the budget is not exceeded. *Would have caught:* the unattended process filling a
   disk it was never told the size of.

10. **Two `SegmentIndex` connections, one writing and one reading.** *Would have caught:* the
    WAL/`busy_timeout` pragmas being deleted — a green mutation today, and `database is
    locked` between the recorder and the console in the field.

11. **Recall against the labelled footage already in the repo.** Score `footage/walk_3mbps.mp4`
    against `walk_3mbps.mp4.labels.json` and assert a floor (7 of 8 spans, as
    `vmd/detect_main.py:100` claims). Integration-marked, skipped without the footage.
    *Would have caught:* any regression in the one thing the detector exists to do,
    currently measured only by a script somebody remembers to run.

12. **A diverging local commit makes the updater refuse**, and `uv sync` runs with
    `--offline`. *Would have caught:* `--ff-only` being removed, and a ten-minute silent hang
    on the air-gapped laptop.

---

## 6. Speed and flakiness

**Unit, ~2m45s.** About 70s of it is real network waits (§4.5) — a black-holed IP, four
`is_live` connects, real `tasklist`/`taskkill` subprocesses, and `git` in
`tests/test_updater.py` (3.0s setup plus several 1.4-1.5s calls). Removing the `192.0.2.99`
waits alone takes roughly a quarter off the wall clock. `tests/test_desktop_picker.py:315`
costs 3.21s on a `release.wait(3)` plus a blind `qtbot.wait(200)`.

**Integration, ~1m45s for 13 tests, and mostly slow for good reasons.** The acceptance test
is 32s; three recorder tests are ~12s each because they record real 12-second clips in real
time (`-re`). That is the price of testing the actual thing. The avoidable cost is the two
blind `time.sleep(3)` fixture waits in `tests/test_desktop_video_vlc.py:56` and `:384` — and
that a plain `pytest` runs them, because `addopts` does not deselect the marker.

**Most likely intermittent failures**, in order:
1. `tests/test_desktop_video_vlc.py` — go2rtc given a fixed 3s to come up on a laptop that
   also runs a console, a recorder and a detector. Poll the API instead.
2. `tests/test_desktop_services.py` on any non-Windows runner — PID 31337 (§4.18).
3. `tests/test_desktop_timeline.py:79`/`:87` on any runner not in Israel (§4.17).
4. `tests/test_streaming.py:869` — binds a port, closes it, then connects to it.
5. `tests/test_detect_pipeline.py:309` and the other wall-clock assertions under load.
6. `tests/test_radio.py:592` — `PATIENCE = 15.0` against a measured 12.13s.
7. `test_record_main.py::test_falls_back_to_the_camera_when_the_streaming_server_is_gone` —
   assumes `127.0.0.1:59999` refuses rather than drops; it
   currently takes the full 1.5s timeout, so it is already not being refused.
8. `tests/test_desktop_playback.py:107` — exact float equality after an advisory `resize(200,
   24)` on a widget that measures 272 px once anything lays it out. The file's own comment at
   `:391` admits the layout has the last word.

The libVLC integration run also emits several hundred lines of `stale plugins cache` and
`cannot initialize COM` from libVLC itself. Harmless, and it buries real output.

---

## 7. Would the five defects be caught if reintroduced?

| Defect | Regression test | Verdict |
|---|---|---|
| pcm_mulaw / MP4 / zero-byte segments | `test_recorder.py:276`, `:283`; `test_recorder_integration.py:68`, `:94`, `:214` | **Load-bearing, both levels.** Verified: putting `-c copy` back fails 3 integration tests including the acceptance test, and 2 unit tests. The strongest coverage in the repo — *for the cause*. The **symptom** is a different story: see §4.1, where a 1-byte file and a backwards clock both still read as "recording", and deleting the zero-byte guard leaves 30 of 31 relevant tests green. |
| Wind must not raise an alarm | `test_detect_pipeline.py:68` | **Load-bearing today, decorative tomorrow.** Fails if the travel rule is neutralised, but nothing asserts the flicker reached the rule. One tuning change returns it to the original vacuity. Two lines to fix. |
| Password redaction | `test_radio.py:345` (`TRICKY_PASSWORD`) | **Not load-bearing.** The password was chosen from the alphabet `redact` already handles; the live JSON-escaping leak passes it, as does a non-ASCII password. |
| Mutations that hang instead of failing | — | **Not addressed.** The three specific mutations were fixed; nothing prevents the fourth. No `pytest-timeout`, no `conftest.py`, ~70s of real socket waits and two bare `join()` calls still in the unit suite. |
| The newest orphaned segment is not indexed | `test_record_main.py::test_the_last_segment_of_a_renamed_stream_is_adopted_not_lost`, `::test_a_lone_orphan_segment_is_adopted`, `::test_a_segment_something_still_has_open_is_never_adopted` | **Load-bearing.** Verified: restoring `candidates[:-1]` fails all three, and the Windows open-handle guard is tested against a real handle. |

Two of five are solid, one is solid-but-fragile, one is not load-bearing, and one was never
a test problem and still is not fixed. And the one that is best covered is covered on its
cause while its symptom — the sentence on the screen — has three live holes.

---

## 8. Method, and what a second pass should look at

Everything above was read directly or delegated and then re-verified: every P0 and every
claim marked "verified" in §3 was reproduced by running the shipped code or by mutating a
private copy of the tree. Two claims that arrived in review were checked and **discarded**
as wrong: that `SettingsTab.saved` is uncovered end-to-end (it is covered, through
`vmd/desktop/window.py:457` and `tests/test_desktop_window.py:235`), and that
`tests/test_streaming.py`'s credential test uses a tame password (it does not — `p@ss:w/rd`
with the expected percent-encoding is exactly right).

What a second pass should take further:

- `tests/test_desktop_settings_tab.py` and `tests/test_desktop_picker.py` were read for the
  specific questions here rather than assertion by assertion.
- The Qt teardown story: handle leaks (`tests/test_desktop_playback.py:511`, `:529`, `:544`,
  `:565` close their index outside `finally`), the root-logger handler leak (§4.17), and
  `settings_tab.py:1286`'s 5-second `waitForDone` against a 144-second probe, which on
  timeout leaves a worker that will later touch deleted C++ objects.
- Whether any of the ~13 `ConsoleServices` sites that omit `disk=` are asserting something
  that is only true because of it.
