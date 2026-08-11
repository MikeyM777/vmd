# Backlog

Actionable items pulled out of the reviews in this directory. Append; do not
rewrite. Each line says where it came from, so the reasoning behind it can be
read rather than re-derived.

Sizes: **S** ≈ under an hour, **M** ≈ an hour or two, **L** ≈ half a day.

---

## From `2026-08-12-console-review.md` — the console as he meets it

Rendered at 1366×768 and 1920×1080 and looked at. Screenshot names are relative
to that document's screenshot folder.

### Defects

- **S — A silenced faulted chip shows the healthy word.** When more than one
  thing has failed, only the top-ranked chip keeps its sentence and the rest fall
  back to their *glance* word — so a stopped recorder is a red box reading
  `recording`. Give each part a faulted short word (`NOT recording`,
  `no pictures`, `no detection`, `no link`). `window.py:629-667`,
  `window.py:1309-1344`. `20-broken-live-1366x768.png`.
- **S — A camera view that has failed is reported nowhere except an 11 px label
  on the dead picture.** The band reports services, never views, so one dead
  camera leaves four green chips. Add a chip for a configured view that is not
  playing, and say it inside the empty well as well as above it.
  `live.py:1698-1716`. `32-halfstream-live-1366x768.png`.
- **S — "Nothing has moved yet." is shown while detection is stopped.** The
  movement panel gives the reassuring one of the two things an empty list can
  mean, against `DESIGN.md`'s own empty-state rule. `live.py:206`, `live.py:1026`.
- **M — The band and the Link panel disagree about the same radio.** `_link_state`
  reads `signal_dbm` only, so a link at 88 % airtime with a good signal is a
  quiet green `link` chip above a red `FULL` panel. `window.py:255-299`,
  `window.py:1386-1410`. `41-linkfull-good-signal-live-1366x768.png`.
- **S — `Show me` pressed in fullscreen** leaves the console on Playback with no
  band, no tab bar and no visible way out. `show_footage` should leave fullscreen
  first. `window.py:1062-1096`. `52-fullscreen-then-show-me-1366x768.png`.
- **S — Text drawn over text.** Pressing **Ignore parts of the picture** on a
  stream card makes "How touchy:" land on the note above it, and two more lines
  collide below. Same failure the `StoragePanel` comment defends against.
  `settings_tab.py:368`, `:395`, `:508-509`.
  `80-settings-ignore-parts-open-1366x768.png`.
- **S — The Logs filter gives no sign which of `All` / `Warnings and errors` is
  on.** Plain, uncheckable, ungrouped buttons; the two renders are pixel
  identical. `playback.py:697` already names this fault while fixing it for
  itself. `logs.py:179-184`.
- **M — Timeline zoom lands on an empty window.** With no playhead, **1 hour**
  centres on the middle of the day, so a console that recorded overnight jumps to
  11:30–12:25 and draws an empty bar under a line still saying "1h 25m recorded".
  Centre on the nearest footage, and say when the window holds none.
  `playback.py:921-931`.
- ~~**S — `test_live_and_playback_read_the_same_movement` is failing on master.**~~
  **Done — `5693933`, while the review was being written.** After any heartbeat,
  `show_day` for a camera the catalogue had no segments for left the Camera
  selector empty and dropped every movement mark: `show_day` added the name, then
  `_reload` called `refresh_streams` again and cleared it. Introduced by
  `545bf10`. `playback.py:830-845`, `:855-865`, `:775-799`.
- **S — The date is drawn twice on Playback**, 40 px apart, in the day-picker
  button and the heading below it. `playback.py:641-656`, `:668-686`.
- **S — Two help paragraphs are printed once per camera view**, side by side and
  word for word identical. `settings_tab.py:368` (`CLASSIFY_HELP`), `:395`
  (`REGIONS_HELP`).
- **S — The Link panel says the airtime verdict three times** when `Details` is
  open. `radio/panel.py`, `link_summary` note plus `_traffic_lines`.
- **S — At 1366×768 the Recent movement table is cut through the middle of a row
  of glyphs** rather than at a row boundary. `live.py:979-1037`.
- **S — The zoom readout is a bare `42%`** with no noun on the control. The word
  "zoom" appears only in the failure caption, so the control names itself only
  when it is broken. `zoombar.py:216-223`.

### Words

- **S — Delete `Must travel at least (dots)`**, or move it beside **How touchy:**
  and call it *"Ignore movement smaller than"*. It asks for a unit he cannot see,
  its placeholder names a control on another screen behind a fold, and its own
  tooltip says *"Leave it empty."* `settings_tab.py:1078`.
- **S — `go2rtc is not installed - run install.bat`** → *"Part of VMD is missing,
  so there are no pictures. Reinstall VMD."* Still unfixed from the last review.
- **S — `the services could not be asked what they are doing`** → *"VMD cannot see
  its own recorder and detector. Restart VMD."* `window.py:1291-1297`.
- **S — `ptz zoom_poll failed unexpectedly…`** in the Logs tab: a Python function
  name in the one screen he opens when something is already wrong.
- **S — Rename:** `Budget (GB)` → *"How much space VMD may use (GB)"*;
  `Scan this PC` → *"Look at this drive and suggest a size"*;
  `Fit the camera to the link` → *"Turn the picture down to what the link can
  carry"*; `Find the right path` → *"Find the camera's address"*;
  `Save it… / Clear` → *"Save the marked clip" / "Unmark"*; `Acknowledge` →
  *"Seen it"*; `Follow` → *"Jump to newest"*.
- **S — Three controls called almost the same thing** on one screen: two per-card
  **Watch for movement** and one **Watch for movement at all**. Make the master
  switch *"Watch for movement on any view"* and the card's *"Watch this view"*.
- **S — The Radio box explains a setting that is not on any screen** —
  *"It never goes below the lowest picture you allow"* is `bitrate.floor_kbps`.
  Show it or name the number.
- **S — `docs/FIRST-MORNING.md:361` says "the status line at the bottom".** There
  has been no status line at the bottom for a while.

### Improvements

- **M — Take him somewhere when a chip is red.** A faulted `streaming` or
  `recording` chip opens Logs; a faulted `link` or `detection` chip opens
  Settings; quiet chips stay inert. `window.py:629-667`.
- **S — Make `No pictures. Add a camera view in Settings.` a button.**
  `live.py:117`, `:724`.
- **M — Make `Find the right path` work with nothing configured**, and offer what
  it finds as buttons that write into the field. `diagnose.py:162-165`, `:200`.
  The largest single reduction in typing in the application, in the hour it is
  needed most.
- **S — Pin Save and its message to the bottom of the Settings tab**, outside the
  scroll area; draw `Saved.` in `--ok` with the `●` glyph and fade it; add
  `Ctrl+S`. There is not one `QShortcut` in the codebase.
- **S — Give the Playback picture an empty state.** On first run it is a black
  rectangle 1350×450 with its only explanation eleven pixels high in the corner
  of the window.
- **S — Disable the Playback transport until there is something to play.** With
  no recordings, `Play` and the six skip buttons are live and do nothing;
  `Save it…` is correctly disabled, which proves the pattern is available.
- **S — A legend under the Playback bar:** `green = recorded · red = something
  moved · amber = where you are`.
- **S — Name the two unlabelled pan arrows** beside the timeline zoom buttons.
- **S — A `Browse…` button beside the Storage folder.** `QFileDialog` is already
  imported and used by *Save a report*.
- **M — Give the Storage panel the treatment the Link panel got.** They sit one
  above the other; one is two bars, a word and a disclosure, the other is four
  sentences.
- **S — The two storage countdowns read as two warnings and only one is.**
  Retention deleting the oldest footage is the design working; the drive filling
  is not. Identical phrasing, identical colour.
- **S — `idle` is the largest thing in the side column**, on the panel that
  matters least. It should be the quietest thing there when nothing is moving.
- **S — The steering box invites keys that go nowhere** when the camera is
  unreachable, and `PtzService.status()` already knows.
- **S — Show the time under the pointer on the Playback bar.**
- **S — `Save it…` is offered over a marked range containing no footage.**
- **M — In fullscreen there is no health at all.** One faulted chip should
  survive into it; the quiet ones should not.
- **S — The stream address field accepts anything path-like** with no feedback
  until a black rectangle appears. A note beside the field would not have to
  refuse the save. `settings.py:99-127`.

### Open questions, not tasks

- **`DESIGN.md:214` promises an arriving alarm makes a sound. There is no sound
  anywhere in the program.** Decide which document is wrong. If the sound is
  wanted it is one `QSoundEffect` fired from `live.py:1111`, once per event,
  never a loop. Needs someone at the real laptop to say whether it has speakers.

### Still open from `experience.md`, confirmed on the screen today

1c (band chips inert) · 1d (`NO_VIEWS_NOTE` is not a button) · 5 (Save below the
fold, `Saved.` in muted grey, no `Ctrl+S`) · 6a (`Remove` has no undo) · 7 rows 2
and 4 · 9 (Logs filter) · 10 (the wall does not even its panes — there is no
`setSizes` anywhere in `live.py`) · 11 (steering contradicts itself) · 12b–12g,
12i.

### Fixed in that pass

- `Roughly 1 minutes left` → `1 minute` / `1 hour`. `disk.py`, `_duration` and
  the new `_plural`.
- A refused save named a Python attribute path. `settings_tab.py`,
  `_first_problem` plus `FIELD_LABELS` and `WANTED_NUMBER`:
  `Delete older than (days): "two weeks" is not a whole number.`

---

## From the hunt of 2026-08-12 - the seams between the five things that landed

Found by trying to break the work of the 11th and 12th rather than by reading
it: the rebuilt Link panel, the per-lens zoom, the fullscreen live mode, the
rebuilt Playback tab and the automatic bitrate loop. Ordered worst first inside
this section, and each entry says how it is reached, because that is the part a
reader cannot re-derive.

The first two are the same shape, and it is this project's signature one: a
check that proves something adjacent to what matters.

**One item in the section above is already fixed.** The `545bf10` regression -
`show_day` naming a camera the catalogue has no segments for, and `_reload`
clearing it again - was found by the same run and fixed in `5693933`, with a test
that asks for a day for a stream with nothing on disk and asserts the tab is
still showing it.

---

## 1. A row in the catalogue is taken as proof there is footage

`vmd/desktop/playback.py:1168-1216`, `1218-1245`, `1562-1566`

Nothing between the index row and the sentence checks that the file exists or
that the picture ever appeared:

```python
target = seek_target(self._segments, when)
if target is None: ...
self._point(self._pane, target.path, target.offset_seconds, first=True)
...
note = f"Playing {self.shown_streams()[0]} from {clock} - {name}, ..."
```

`_point` calls `pane.show(url)`, which for libVLC is fire and forget - it logs
and returns, and there is no result. `_draw_controls` then derives "playing" from
`self._showing` alone, so the transport, Mark start and Mark end all come alive
over a black rectangle. `_play_at` returns `True`, which is what
`window.show_footage` reads as "the alarm has been answered".

**How it is reached, and VMD creates this state itself.**
`vmd/storage/retention.py:365-374` unlinks the file first and explicitly
tolerates failing to remove the row afterwards. Add an interrupted retention
pass, a recordings folder on a drive that is not mounted, or anything that
quarantines a file, and Playback claims footage that is not there. This is the
module's own stated red line - `timeline.py:224`, "a blank is the one answer this
console may never give".

**What I would do.** The pane already knows: `VlcVideoPane.state` becomes
`"failed"` once libVLC gives up (`vmd/desktop/video.py:436-444`) and
`frames_seen` counts real frames (`:283`, `:510`). Playback never asks either.
The follow timer, which now stops when the playhead is not on footage, is the
natural place to notice a picture that failed and to replace the sentence.

**Careful:** the cheap version - stat the file before claiming it - would fail
about twenty existing tests, which build index rows over `.mp4` paths they never
create (`a_recorded_day`, `tests/test_desktop_playback.py:887`). Asking the pane
is additive and does not.

---

## 2. A saved clip's length is the length that was planned, not the length that was written

`vmd/desktop/export.py:278-292`

```python
if getattr(finished, "returncode", 1) != 0: ...
if not destination.exists():
    return _failed(destination, "The clip could not be saved: nothing was written.")
said = f"Saved {_duration(plan.covered_seconds)} of {stream} to {destination}."
```

Two unchecked claims in three lines:

* **`exists()` is not `st_size > 0`.** ffmpeg creates its output before it writes
  anything, so a zero-byte file passes this and is reported as a success.
* **`plan.covered_seconds` came from index rows.** `-c copy` through the concat
  demuxer can exit 0 having skipped a segment it could not read - and the
  recorder is killed mid-segment routinely, see `SegmentIndex.bounds`
  (`vmd/storage/index.py:171-174`). The operator then reads *"Saved 1h 00m of
  thermal to E:\clip.mp4"* over a four-minute file. That is this module's own
  docstring, verbatim: "A clip he believes is ten minutes when it is four is
  worse than no clip."

**What I would do.** `bin/ffprobe.exe` ships beside `bin/ffmpeg.exe`. Measure the
written file and say the measured figure; when it cannot be measured, say so
rather than falling back to the planned one silently. This wants a second
injected callable beside `run=` so the existing fakes are not asked to answer two
different questions, which is why I did not do it in the time I had.

`tests/test_desktop_export_integration.py` already proves a real clip decodes, so
this is about the *sentence*, not about whether export works.

---

## 3. The Settings tab opens a second connection to the camera

`vmd/desktop/settings_tab.py:1905`

```python
return CameraTools(ptz=PtzService(settings), find_paths=find_paths, diagnose=diagnose)
```

`vmd/desktop/app.py:118-127` says why this must not happen, in as many words:

> One object each, not two: a second `RadioService` would log in to the radio a
> second time, and a second `PtzService` would hold a second connection to a
> camera that hands out very few of them.

The console builds one `PtzService` and hands it to the services and to the Live
tab; the Settings tab builds its own. Two ONVIF sessions to one camera over a
link at 88% of its airtime, and two objects with two ideas of which profile token
is which lens.

**Not fixed because `settings_tab.py` was off limits during this pass.** The fix
is to hand the wiring's `ptz` in, the way `window.py` hands it to `LiveTab`.

---

## 4. A remembered login is never reconsidered

`vmd/ptz/onvif.py:380-384`

```python
attempts = (
    [(self.capability.auth, self._auth_opener)]
    if self._auth_opener is not None
    else list(self._openers())
)
```

Once a style has been accepted it is the only one ever tried again. If that
opener later gets a 401 - the operator changes the camera password, the camera
reboots into a different auth mode, a firmware update - the loop `continue`s past
its single attempt and falls out to `raise PtzError(last_error)`. `_auth_opener`
is never cleared, so every command from then on fails the same way for the life
of the process, and restarting the console is the only cure.

**What I would do.** On a 401/403 against the remembered opener, clear
`_auth_opener` and fall back to the full list once. Cheap, and it turns a
permanent failure into one slow command.

---

## 5. A stop can still wait one ONVIF timeout behind a zoom readback

`vmd/ptz/service.py:235-249`, `vmd/ptz/lenses.py`

`PtzService.zoom_poll` holds `_lock` for the whole of `Lenses.poll()`, which is
up to one SOAP call per lens, and it runs on the same sender thread as the
steering. `PtzCommands`' guarantee is that a stop waits behind "at most one
command already on the wire" - and a zoom readback is one command in the mailbox
that is several calls on the wire.

Discovery no longer retries on every heartbeat (fixed today), so on a camera that
cannot be read this is now one attempt every 30 s rather than every 2 s. What
remains: for up to 8 s out of every 30, a stop the operator owes the head waits.
On an unreachable camera the head is not moving either, so this is only a hazard
on a camera that is reachable and slow.

**What I would do.** Let `zoom_poll` take and release `_lock` per lens the way
`_OneAtATime` does for the encoder writes, and have `Lenses.poll` check a
"something else wants the camera" flag between lenses. Or simply give the readback
a shorter timeout than a command: it is a slider, and a command is a motor.

---

## 6. A settings save during a bitrate write leaves the loop above the new ceiling

`vmd/ptz/autobitrate.py:189-200`, `364-399`

`apply_settings` clamps `_target` into the new floor/ceiling. A write already on
the executor finishes afterwards and assigns `self._target = wanted` from the old
settings, un-clamped. So: the operator lowers the ceiling from 8000 to 3000 while
a write to 6000 is in flight, and the loop is left believing the camera is at
6000 - above the ceiling he just set. `_up` then refuses to move (3000 <= 6000)
and `_down` steps to 4200, still above 3000. Nothing re-clamps until the next
save.

**How to reach it:** press Save while the loop is mid-write, which is a several-
second window on this link and is exactly when an operator is fiddling with the
bitrate settings.

**What I would do.** Clamp in `_write` under the same lock, from the settings as
they are when the answer arrives rather than as they were when it was sent. Note
that the loop is then claiming a figure the camera does not have - which argues
for the larger fix, which is to seed `_target` from what the camera reports
rather than from what was commanded.

**Not fixed** because the clean version needs `BitrateSettings`, and
`vmd/settings.py` was off limits during this pass.

---

## 7. The whole of a month is read onto the GUI thread to mark a calendar, and only for one camera

`vmd/desktop/playback.py:1522-1560`

```python
segments = self._day_between(stream, month_start, month_end)
for segment in segments:
    began = datetime.datetime.fromtimestamp(max(segment.start, month_start))
```

**Cost.** This runs inside `_reload`, so on every day change - including the
auto-repeat of the "Day before" button - every camera change, and every "Show
me". One month of five-minute segments is about 8,900 rows per stream, each built
into a `Segment` and put through two `fromtimestamp` calls, on the thread that
draws the window, to produce at most 31 bits of information. It is the pattern
`streams()` and `between()` were written to remove (`vmd/storage/index.py:128-156`).

**Wrong answer.** `_reload` passes `shown[0]`, and line 1532 clears the format for
every date first, so in "Both together" a day on which only the second camera
recorded is drawn as an empty day - in the calendar the operator uses to find
footage.

**What I would do.** A `SELECT DISTINCT` over the month, per shown stream, and
mark the union.

---

## 8. The movement marks on the timeline swallow half the window at the zooms that exist to avoid them

`vmd/desktop/playback.py:1018-1026`, constants at `169-171`

```python
seconds_per_pixel = (self.view_end - self.view_start) / max(width, 1)
return max(MARK_TOLERANCE_SECONDS, seconds_per_pixel * MARK_CLICK_PIXELS)
```

The stated rule is "the target is at least as big as the thing drawn ... a third
wider than the red", and
`test_the_mark_is_at_least_as_big_to_click_as_it_is_to_look_at` encodes it as
`target_pixels <= 2 * MARK_WIDTH`. That test only ever runs at whole-day zoom.
On a 1200 px bar:

| zoom | s/px | tolerance | target |
|---|---|---|---|
| whole day | 72 | 144 s | 4 px |
| 1 hour | 3.0 | **30 s** | **20 px** |
| 10 minutes | 0.5 | **30 s** | **120 px** |

At ten minutes each event owns 60 s of a 600 s window, so five events make half
the window unreachable for an ordinary seek - and the operator zoomed in
*because* he needs an exact second near the event. The status line reads as
though the click worked.

**The code already knows this happens.** The comment above the constants ends
"zooming changes what a pixel is worth and nothing else here, so at ten minutes
across a 1200 px bar a pixel is half a second and the floor is what binds". So
this is not an oversight; it is a decision whose justification did not survive
the feature that arrived after it. The reason given for the floor is that "below
half a minute the pointer is asking for a precision the day bar was never
offering" - which is true of the *day* bar and is exactly what the ten-minute
zoom exists to stop being true.

**What I would do.** Keep the floor for the whole-day view, where it was argued
for, and let the drawn width bind at the zooms that offer the precision - i.e.
make the floor a floor in pixels rather than in seconds. Then run the existing
invariant test at all three zooms and at two widths, which is the gap that let
this through.

---

## 9. Marking a start and an end at the same moment is answered with a lie

`vmd/desktop/export.py:195-203`, `vmd/desktop/timeline.py:356-357`

`clip_plan` drops any piece where `piece_end <= piece_start`, so a zero-length
range yields no parts, and `export_clip` answers with the sentence reserved for a
range with no footage in it: *"there is no recording on thermal in the part of the
day that was marked"* - about a moment he is watching. He presses Mark start and
then Mark end before the playhead has moved, which is one click apart.

**What I would do.** Say "the marked piece has no length" and refuse. One
sentence, in `export_clip`, before the parts are consulted.

---

## 10. Three implementations of "is that PID alive"

`vmd/desktop/services.py:_pid_alive`, `vmd/streaming/go2rtc.py:process_image`,
`vmd/record_main.py:process_image`

`risks.md` §14 named two of them. The third one was fixed today - it had no
timeout, no `CREATE_NO_WINDOW`, and matched the PID by substring - and fixing it
made the duplication concrete rather than theoretical: the same `tasklist`
invocation is now written out three times, and only one of the three checks the
PID column.

`record_main.process_image` and `go2rtc.process_image` both take the first quoted
row of the answer regardless of which PID it names. That is only safe while the
`/FI` filter is doing its job, which is an assumption nobody states.

**What I would do.** One `vmd/proc.py` with `process_image(pid)` in it, imported
by all three. `record_main` is a separate process and may not import from
`vmd/desktop/`, which is why the copies exist - a module at the root has neither
problem. `_write_json_atomically` wants the same home, and is already named in
`docs/review/README.md` as wanting it.

---

## 11. "Could not tell" is still invisible to the liveness check

`vmd/desktop/services.py:_pid_alive`, `vmd/background.py`

`BackgroundValue` now keeps the last good answer when a read raises, and lets it
age - so `liveness_age` grows and `LIVENESS_UNANSWERED_SECONDS` can fire. But
`_pid_alive` catches its own failure and returns `True` ("unanswerable reads as
still there"), so the reading is recorded as a fresh success and the age resets
anyway.

The two rules are each right on their own and they cancel out. `process_image`'s
three-valued answer - a name, `None`, or `""` for "could not tell" - is the shape
that would let both hold at once.

**What I would do.** Have `_pid_alive` raise on an unanswerable question rather
than answering it, and have the two direct callers (`ChildProcess.start`, and the
wait loop at `:983`) treat the exception as "still there". Then the reader's
answer ages, which is what the check measures.

---

## 12. `vmd/updater.py` reaches the internet, on a machine that must not

`vmd/updater.py:106`, `:119`

```python
pull = self._run(["git", "pull", "--ff-only"])
...
sync = self._run(["uv", "sync", "--extra", "detect"])
```

`git pull` reaches github.com; `uv sync` without `--offline`, `--frozen` or
`--no-sync` reaches pypi.org. Line 175 says *"Could not reach GitHub."* out loud.

Nothing imports it - the only reference outside the module is
`tests/test_updater.py` - so it is dormant. It is also fully functional and one
import away from being wired to a button, on a laptop whose whole security model
is that it is offline. `vmd/launcher.py:158` and `VMD.bat:47` show what the rest
of this project does: `uv run --offline --frozen --no-sync`.

**What I would do.** Delete it. If an update path is ever wanted it will be a
USB stick and `offline-install.bat`, which already exist.

---

## 13. `Ultralytics/settings.json` is committed, with telemetry armed and a machine-derived id

`C:\dev\VMD\Ultralytics\settings.json`, and a second copy at
`Ultralytics\Ultralytics\settings.json`

```json
"uuid": "3c971c08b0b41f20a28afdf0a6697263095b54fd8a856cfeee7c921a14487f56",
"sync": true,
```

`sync: true` is one half of the conjunction that arms ultralytics' Google
Analytics beacon; the other half is `ONLINE`, which `vmd/__init__.py` holds false
with `YOLO_OFFLINE=1`. So it is inert today and it is one environment variable
from not being. The file also carries dev-machine paths and an id derived from
this machine, and `.gitignore` covers `yolo11n.pt` but not the directory.

**What I would do.** `"sync": false`, ignore the directory, and look at why there
are two of them - a doubled `Ultralytics/Ultralytics/` suggests `YOLO_CONFIG_DIR`
is being appended to rather than set.

---

## 14. Two spike tools post camera credentials through whatever proxy the machine has

`spike/probe_camera.py:61-66`, `spike/identify_camera.py:97-103`

`urllib.request.build_opener(...)` with digest and basic handlers and no
`ProxyHandler({})`. Exactly the defect fixed in `vmd/streaming/go2rtc.py` today
and in `vmd/radio/airos.py` and `vmd/ptz/onvif.py` before that -
`proxy_bypass("192.168.1.251")` is False, so `http_proxy` or the Windows registry
sends the camera's password to whatever it names.

`spike/probe_radio.py:340` already has the handler, which is what makes these two
an oversight rather than a policy.

**What I would do.** One line each, and the same test the radio has.

---

## 15. go2rtc's other network-capable modules are not switched off, only WebRTC is

`vmd/streaming/go2rtc.py:371-386`

`build_config` empties `webrtc.listen` and `webrtc.ice_servers`, and the reason is
written down. `strings bin/go2rtc.exe` also yields
`wss://tracker.openwebtorrent.com`, `224.0.0.251:5353` (mDNS) and ngrok symbols,
and `srtp` defaults to listening on `:8443` on every interface. All of them need
a config key to do anything and none of those keys is present, so they are inert -
but go2rtc merges its compiled-in defaults with the file it is given, and a
version bump that changes a default is the exact failure the `ice_servers: []`
"belt and braces" comment was written against.

**What I would do.** Empty `webtorrent`, `ngrok`, `homekit`, `hass` and `srtp`
explicitly, and extend
`test_the_config_names_no_host_outside_this_machine` to assert the keys are
present and empty rather than absent.

---

## 16. Smaller things, each with what it costs

* **`unique_path` overwrites the 999th clip and stats up to 998 paths on the GUI
  thread** (`vmd/desktop/export.py:158-172`, called from
  `vmd/desktop/playback.py:1486` before the worker starts). The bounded loop is
  documented as protecting evidence, and after 998 collisions it returns a path
  that exists - and `clip_command` passes `-y`.
* **`coverage_bars` divides by the window's span with no guard**
  (`vmd/desktop/timeline.py:134`), unlike `time_at`, `_playhead_fraction` and
  `_ticks`, which all use `max(span, 1.0)`. Not reachable through the tab today;
  it is a public pure function and `coverage_bars([seg], t, t)` raises.
* **"0s recorded on thermal" when only the other camera recorded that day**
  (`vmd/desktop/playback.py:877-888`): `recorded` sums `self._segments` only, and
  the "nothing at all" branch is skipped because the second camera has segments.
* **Skipping unpauses.** `_point` calls `set_paused(False)` unconditionally
  (`vmd/desktop/playback.py:1244`), so "back 10 sec" while paused starts the
  footage running and the operator loses the frame he had stopped on.
* **`_export_signals` is appended to and never cleared**
  (`vmd/desktop/playback.py:607`, `1510`): one object per saved clip, on a console
  that runs for months.
* **A second `VlcVideoPane` is built on every machine** (`:537`, `_another`),
  with its own 250 ms timer, even where there is one camera. Only `closeEvent`
  releases it.
* **`SegmentIndex.oldest()` and `gaps()` are still `SELECT`-everything reads over
  `self.all()`** (`vmd/storage/index.py:190-219`) - the pattern `streams()` and
  `between()` were rewritten to remove. Not on a GUI path today.
* **The Live tab keeps decoding for a tab nobody is looking at.**
  `risks.md` §14 already has this; I checked and it is still true -
  `hideEvent` stops the steering only, and `frame.isVisibleTo(self)` is still
  true when the whole tab is hidden.
* **`PtzService.status()` holds the camera lock across `connect()` and
  `position()`.** Nothing on the GUI thread calls it today. If anything ever
  does, it is the freeze that was taken off `apply` this morning, by a different
  door.

---

## What I looked for and could not settle

* **Whether entering fullscreen while a zoom is in flight breaks anything.** I
  could not make it: `FullscreenLive` reparents nothing, `set_fullscreen` only
  hides the side column, and the zoom bars are children of the video frames,
  which are untouched. I could not test it against a real libVLC pane, which is
  the only place the reparenting hazard the module was written about could
  appear - so this is "found nothing", not "proved nothing happens".
* **Whether the panes really stop decoding when a view is switched away.**
  `_apply_view` calls `pane.stop()` and the state word changes, but a frame
  counter and a state string are what this project has twice been wrong about.
  Settling it needs the deployment laptop and a look at its processor time.
* **Whether a `sqlite3` connection recovers after a media-removal I/O error.**
  `risks.md` §3 asks the same question and it still has no answer. It decides
  whether the recorder needs a reconnect path or a retry.
* **What the automatic bitrate loop does to a real camera over a real link.**
  Everything about it here is tested against a clock the test holds and a camera
  that answers instantly. The one thing that matters - whether an operator
  watching notices the picture being turned down - cannot be found in this
  repository.
