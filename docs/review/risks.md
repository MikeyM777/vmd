# Latent failure risks

Read of `vmd/` in full, plus `scripts/recorder_service.ps1` because it is a second
way the recorder is started and it changes what the recorder sees.

Ranked by expected harm: how bad it is when it happens, times how likely it is to
happen on this machine, times how long it stays invisible. A silently dead
recorder outranks a leaked handle; a fault that announces itself outranks one
that is merely unlikely.

Nothing here is fixed. Where I am not sure something is real I say so, and say
what evidence would settle it.

---

## 1. The recorder decides where to read from once, from a TCP connect, and can never change its mind

`vmd/record_main.py:437`

```python
endpoint = read_endpoint(endpoint_path or DEFAULT_ENDPOINT_PATH)
self._endpoint = endpoint if endpoint and is_live(endpoint) else None
```

`self._endpoint` is assigned in `__init__` and nowhere else. `_source_for`
(`vmd/record_main.py:554`) reads it, and `_build_recorders`
(`vmd/record_main.py:482`) calls `_source_for` again on every settings change —
always against that same, frozen answer.

**The scenario, and it happens after every reboot.** `scripts/recorder_service.ps1`
starts the recorder at logon, before any human has opened the console. There is no
go2rtc yet, so `is_live` is false, so `_source_for` takes the other branch:

```python
logger.info("recording %s directly from the camera", stream.name)
return stream.url
```

The operator opens the console some time later; go2rtc starts and opens *its* own
connection to the camera. From that moment every stream crosses the 15 km, ~5 Mb/s
radio link **twice, for months** — which is precisely the contention the whole
architecture exists to prevent (`vmd/streaming/go2rtc.py:1`, `vmd/desktop/live.py:5`,
`vmd/desktop/picker.py:18`). Nothing anywhere reports it. Recording works. The
picture stutters, the link panel says the link is full, and every part of the
system blames the link.

The one line that says so — `"recording %s directly from the camera"` — is written
to a pipe that in this case has no reader: the console did not spawn this recorder,
so its stdout goes to `bin\logs\recorder.out.log`, which the operator has no way to
open.

**The other half.** If go2rtc ever ends up on a different RTSP port than the one in
the frozen endpoint (`free_port` in `vmd/streaming/go2rtc.py:121` returns an
OS-chosen port whenever 8554 is taken — a ghost go2rtc that would not die, anything
else on the machine), the adopted recorder keeps pointing ffmpeg at a dead loopback
port for ever. It cannot recover: the recorder deliberately outlives the console,
and the console adopts it rather than restarting it.

**The detector already fixed exactly this**, in exactly these words
(`vmd/detect/runner.py:449`):

> Which one is used was decided once at start-up from a port answering, and never
> revisited - so a streaming server that restarted on another port, or one left
> over from an older settings file, took detection off this stream for the life of
> the process while the camera was reachable throughout.

`StreamDetector` carries a `fallback_url` and rotates addresses after
`OPEN_FAILURES_BEFORE_FALLBACK`. The recorder — the more important of the two
processes — still has the bug verbatim.

**What I would do.** Give `SegmentRecorder` the same rotation: two sources, the
local one first, rotate after N consecutive stillbirths. Separately, re-read
`streaming.json` on the pass that notices a stalled or held-back stream. And, so
the degraded-but-working case is *visible* rather than merely fixed, put the chosen
source into `status()` and into a line the console can show: "recording ch1 from
the camera, not from this machine — the link is carrying it twice".

---

## 2. The clock, in both directions

The laptop is offline and its clock is set by hand. Two things read wall time and
act irreversibly on it.

**Forward: the archive is deleted.** `vmd/storage/retention.py:51`

```python
cutoff = now - retention_days * DAY_SECONDS
for segment in ordered:
    if segment.end < cutoff:
        plan.delete.append(segment)
```

`now` is `time.time()` from `_apply_retention`. An operator who has set "delete
older than 30 days" and then sets the date wrong by a year loses every recording on
the machine in the next retention pass — files unlinked, index rows deleted, and
`_reclaim_events` drops the matching movement events too. There is no undo and no
confirmation. The age rule is off by default (`retention_days: int | None = None`),
which is the only thing keeping this off the top of the list; the Settings tab
offers the box (`vmd/desktop/settings_tab.py:785`) and the operator is expected to
use it.

**Backward: segments are overwritten and the index does not notice.** ffmpeg names
files with `-strftime` (`vmd/storage/recorder.py:181`). A clock set back an hour
produces filenames that already exist on disk; ffmpeg's segment muxer opens them
for writing and truncates. Then:

* `SegmentIndex.add` is `INSERT OR IGNORE` on a `path UNIQUE` column
  (`vmd/storage/index.py:67`), so the row keeps the *old* start, end and size;
* `self._seen` already holds the path (`vmd/record_main.py:786`), so
  `_index_new_segments` skips it and `_index_final_segments` skips it.

Result: Playback offers a file whose contents are an hour of different footage from
what the index claims, retention deletes by the wrong timestamp, and the coverage
bar draws hours that are no longer there. `TZ=UTC` (`vmd/storage/recorder.py:79`)
protects against daylight saving; it does not protect against a hand-set clock.

Smaller consequences of the same jump: `_notice_empty_segments`
(`vmd/record_main.py:641`) sorts by mtime and exempts only the last file, so a
backwards jump can put the file ffmpeg currently holds inside `files[:-1]`, where
its zero size reads as a broken segment — one permanent false ERROR and
`status()["healthy"]` false for the life of the process, because `_empty_segments`
is never reset.

**What I would do.** Retention: refuse a pass whose `now` has moved more than, say,
a day since the previous pass, log it loudly, and only act on the second
consecutive pass that agrees — a genuine clock correction survives, a typo does
not. Segments: before indexing, if the path is already in the index with a
different size, treat it as a new file (re-key on `path + start`) and say so. The
codebase already reasons carefully about this everywhere else it matters
(`EventStore.recent` orders by id and explains why, `StreamDetector` keeps two
clocks); retention and segment naming are the two places that still trust the wall
clock with something irreversible.

---

## 3. A single sqlite failure kills indexing and retention for the life of the recorder process

`SegmentIndex` is opened once in `RecordingService.__init__`
(`vmd/record_main.py:446`) and is only ever replaced in `_move_archive`, i.e. when
the operator changes the recording folder. There is no reconnect path.

**Scenario:** the recordings folder is on an external or network drive (the
Settings tab lets the operator choose any folder, and `storage_problem` only checks
that it is writable *now*). The drive blips — a USB reseat, a share that drops with
the link. sqlite raises `disk I/O error`; the connection is dead for good. From
then on:

* `_stage("indexing", ...)` and `_stage("retention", ...)` swallow the exception
  every pass (`vmd/record_main.py:665`), logging 3 times and then once per 100 —
  correct as damage control, and it means the failure is a whisper;
* ffmpeg keeps writing segments happily, because ffmpeg does not use the database;
* the console keeps saying **"recording"**, because `recording_state` reads the
  *folder* (`vmd/desktop/disk.py:224`), not the index — the very change that was
  made to stop the console lying;
* nothing is ever deleted again, so the budget is not enforced and the drive fills;
* Playback shows nothing new for ever.

The end state is a full disk on a machine that reported itself healthy the whole
way there, which is the shape of failure this system is least able to afford.

**Uncertainty:** I have not proved that a Python `sqlite3` connection is
permanently unusable after a media-removal I/O error rather than recovering when
the drive returns. Evidence that would settle it: open a `SegmentIndex` on a
removable drive, pull it, run a few passes, plug it back in, and see whether
`index.all()` starts working again. If it recovers, this drops several places.

**What I would do either way.** Count consecutive failures of the indexing and
retention stages; after a handful, `self.index.close()` and reopen it. And make it
visible: a stage that has failed N times running is worth a sentence the console can
show, not only a log line — the recorder's `status()` already counts
`_stage_failures` and nothing reads it (see §14).

---

## 4. go2rtc is the only supervised child with no rule for giving up

Every other restart loop in this codebase learned the lesson and wrote it down:

* `vmd/desktop/services.py:118` — `SPAWN_LIMIT`, for the recorder and the detector;
* `vmd/storage/recorder.py:31` — `RESTART_LIMIT`, for ffmpeg;
* `vmd/desktop/live.py:138` — `RESTART_FIRST_DELAY` / `RESTART_BACKOFF_MAX`, for the
  video panes.

`Go2rtcService` has none. `Supervisor.tick` calls `start()` on anything not running
every heartbeat, with `restart_delay=2.0`. A go2rtc that exits immediately — a
half-copied binary, a config it will not parse, a port collision it loses — is
spawned every two seconds for months. And `_reap` writes an ERROR every one of
those cycles (`vmd/streaming/go2rtc.py:610`):

```python
if time.monotonic() - self._launched_at < SETTLE_SECONDS:
    logger.error("go2rtc exited immediately (%s): %s", code, ...)
```

That is 30 lines a minute into a 500-line ring (`vmd/desktop/logs.py:32`). The Logs
tab — the only thing on this machine the operator can read — is empty of everything
else inside about seventeen minutes. That is the exact harm `_held_back` and
`_say_it_failed` were written to prevent, said in their own comments:

> a 500-line Logs tab in which nothing that explains the fault survives more than a
> couple of minutes - so the one diagnostic the operator has is destroyed by the
> fault it is meant to describe.

**What I would do.** Give `Go2rtcService` the same windowed give-up as
`ChildProcess._held_back`, and throttle `_reap`'s immediate-exit line the way
`_say_it_failed` throttles: spelled out the first three times, then rarely, with a
count.

---

## 5. `SetVideoEncoderConfiguration` is the one ONVIF call sent without checking the answer

`OnvifPtz._post` takes an `expect` argument precisely because a 200 from a camera is
not the thing happening (`vmd/ptz/onvif.py:186`):

> a device that will not do what it was asked ... answers with a Fault, which plenty
> of cameras send with a 200. The camera's own web server also answers 200 with a
> login page on any path it does not recognise. In every one of those cases the old
> code returned, `_do` reported `ok: True`, and the console told the operator the
> command had been sent while the head sat still.

`move`, `stop` and `home` all pass `expect`. `CameraEncoders._write` does not
(`vmd/ptz/encoder.py:196`):

```python
self.camera._post("/onvif/media_service", body)
```

So `fit_encoders_to_link` (`vmd/ptz/service.py:136`) reports what it *asked for*:

```python
encoders.cap_bitrate(config, target)
changed.append(f"{config.name or config.token}: {config.bitrate_kbps} -> {target} kb/s")
```

and the Settings tab prints "ch1: 8000 -> 3750 kb/s" whether or not the camera
accepted a single field. This is the control that stops a pan from starving the
link — the reason `vmd/ptz/encoder.py` exists at all. An operator who presses "Fit
the camera to the link", reads that line and walks away believes the link is capped.
It is the same shape as `Invalid credentials.` inside an HTTP 200.

**What I would do.** `expect="SetVideoEncoderConfigurationResponse"`, and afterwards
re-`read()` and report the bitrate the camera now says it has, not the one it was
asked for.

---

## 6. Zero-byte segments are never removed, and are re-scanned every five seconds

`_notice_empty_segments` (`vmd/record_main.py:615`) is a genuinely good addition —
it is what turns today's 24 empty files into a sentence. But nothing deletes them:

* `find_closed_segments`, `_index_final_segments` and `_adopt_orphans` all skip
  `st_size == 0`, so they never enter the index;
* retention only deletes what is in the index;
* `DiskWatcher._segment_files` skips them too, so they do not even show in the
  budget.

`held_back` bounds the rate at `RESTART_LIMIT` per `RESTART_WINDOW_SECONDS`, i.e. 5
files per 2 minutes ≈ **3,600 files a day** while the fault lasts, in the same
directory that `_notice_empty_segments` globs and stats in full on every pass. And
`self._empty_seen` (`vmd/record_main.py:463`) is a `set[str]` of every such path
ever seen, never pruned, in a process meant to run for months.

So a persistent ffmpeg fault gets slower and more expensive the longer it lasts,
and leaves a directory the operator cannot make sense of.

**What I would do.** Delete a zero-byte segment once it has been reported and is no
longer the newest file — it is provably not footage, and the count is what carries
the information. Failing that, cap `_empty_seen` (an LRU of a few hundred) and
report a rate rather than a running total.

---

## 7. Every recording pass walks each stream's whole directory three times, and the directory grows for months

Per stream, per five-second pass (`vmd/record_main.py:758`, `:633`):

* `segment_starts(dir)` — `glob("*.mp4")`, one `parse_segment_start` per file;
* `find_closed_segments(dir)` — `glob("*.mp4")` plus `path.stat()` per file;
* `_notice_empty_segments` — `glob("*.mp4")` plus `path.stat()` per file, sorted.

At the default 100 GB budget and 300 s segments that is ~1,360 files per stream, so
roughly 4,000 `stat()` calls per stream per pass — with two streams, about
**140 million stat calls a day**, growing with the archive, on the one filesystem
everything else in this codebase is careful about.

`DiskWatcher` measured exactly this work and chose differently
(`vmd/desktop/disk.py:44`): 13.8 ms with `os.scandir`, once every 30 s, "because it
is a filesystem call, and the filesystem is the thing that is broken in every case
this exists to report". The recorder does the same work six times as often with the
slower API, and it is the process that must not stall.

Not a correctness bug today. It becomes one on a drive that is slow, on a network
share, or when the disk is the thing that is failing — the pass takes longer than
the interval and the stall detector starts firing on a healthy camera.

**What I would do.** One `os.scandir` per directory per pass, its result shared by
all three consumers, exactly as `DiskWatcher._segment_files` does it.

---

## 8. Stream names are used unescaped as an RTSP path and as a directory name

The only validation is that the name is not blank (`vmd/settings.py:78`) and that
two streams do not share one case-folded name (`vmd/settings.py:195`). That name is
then used as:

* an RTSP URL path with no quoting — `vmd/streaming/go2rtc.py:812`
  ```python
  return f"rtsp://127.0.0.1:{self.rtsp_port}/{name}"
  ```
* a filesystem path — `vmd/record_main.py:487`, `output_dir=self.root / stream.name`;
* a go2rtc stream id, an events.db column, and `wall_view`.

An operator naming a view `Front gate` gets a URL with a space in it. `ch1/sub`
creates a nested directory that `_adopt_orphans` (which iterates one level of
subdirectories) will never reclaim. `con`, `aux`, `nul` are reserved on Windows and
`mkdir` fails. `..` writes outside the archive. In each case the visible symptom is
one stream that simply never records, and the console reports it the same way it
reports a camera that is switched off.

This is the single-camera assumption showing: with one operator who typed `ch1` and
`ch2` once, it has never mattered.

**What I would do.** Validate the name at the door — letters, digits, `-` and `_`,
with a sentence saying why — and percent-encode it in `local_rtsp_url` regardless.
Refusing at the Settings tab is far better than discovering it as a stream that does
not record.

---

## 9. `Go2rtcService._recent` is a deque written by the pump thread and iterated on the GUI thread

Appended from the log pump (`vmd/streaming/go2rtc.py:1029`):

```python
self._recent.append(text)
```

Iterated from the GUI thread in two places:

```python
last = next((line for line in reversed(self._recent) if line), "")   # :562, status()
logger.error("go2rtc exited immediately (%s): %s", code,
             " | ".join(self._recent) or "no output")                 # :614, _reap()
```

`deque` iteration raises `RuntimeError: deque mutated during iteration` if another
thread appends mid-iteration. Both call sites are on the *failure* path — the
process has just died and the pump thread is draining the last of the pipe, which is
exactly when they race.

The consequence is not a crash but a lie. `status()` is called from
`ConsoleServices.state()`, which is called from `ConsoleWindow.status_parts` inside a
bare `except Exception` (`vmd/desktop/window.py:699`) — so the recording chip, the
streaming chip and the detection chip are all replaced by "the services could not be
asked what they are doing", and `recording_now()` (`:769`) returns `False`, so the
dot stops pulsing. The console says it is not recording, on a tick where go2rtc died
and the recorder was fine.

**What I would do.** Guard the deque with a `threading.Lock` and snapshot under it
(`list(...)` alone has the same problem). Cheap, and it removes a class of
GUI-thread exception rather than one instance.

---

## 10. A wrong radio password makes the console attempt a full six-flow login every four seconds, for ever

`BackgroundValue` is created with `stale_after=CACHE_SECONDS` = 4.0
(`vmd/radio/service.py:16`), and `get()` starts a fresh read as soon as the last one
is 4 s old. `AirOsRadio._login` tries two schemes × three flows
(`vmd/radio/airos.py:524`), each flow up to two HTTP requests at `TIMEOUT = 6.0`.
Against a radio that answers but refuses, no flow raises `URLError`, so nothing
`break`s and the whole matrix is walked: up to ~70 s of login attempts, then
immediately again, for as long as the console is open.

The code knows what that costs — it says so itself in the 403 message
(`vmd/radio/airos.py:686`): airOS answers 403 "after too many tries". So a typo in
the radio password becomes a lockout, and the lockout then looks like a different
fault.

**What I would do.** Back off on repeated failure — 4 s, then 30 s, then a few
minutes — and stop retrying flows after one of them has produced a `LoginRefused`
with the radio's own words, because that is an answer and not an unknown.

---

## 11. Detection goes blind during and after every pan, and reports "detecting"

The detector is a separate process and is never told the camera moved. When it
does:

* `is_global_motion` (`vmd/detect/filters.py:111`) discards every blob in a frame
  where more than 35% is moving — correct, and it is every frame of a slew;
* MOG2's background model is of the old view, and `history=500`
  (`vmd/detect/motion.py:23`) is ~20 s at 25 fps before it has relearned the new
  one. `pipeline.reset()` exists and is only called from `_note_read_failure`
  (`vmd/detect/runner.py:499`), never from a PTZ move, because nothing tells it.

Throughout, `state()["opened"]` is true, frames are arriving, `fps` is healthy, the
picture is changing, so `stream_reason` says **"detecting on ch1"**
(`vmd/desktop/services.py:1219`). `frames_suppressed` is published in `state()`
(`vmd/detect/runner.py:316`) and no console surface reads it.

This is the exact moment the operator is most engaged: they pan because they saw
something.

**What I would do.** This one is hard to remove and easy to make visible: surface
`frames_suppressed` in the per-stream line — "ch1: not watching while the camera is
moving" — and, since the console owns both the steering and the detector's
lifetime, have it drop a note the detector can read so `reset()` runs when the head
stops.

---

## 12. `BackgroundValue` discards the last good reading when a read raises, and stamps it fresh

Its own docstring (`vmd/background.py:70`) promises the opposite:

> The previous value is kept and goes on ageing, so a caller can tell a reading that
> is old from one that is missing.

`_refresh` (`:163`) does not do that:

```python
except Exception:
    logger.exception("%s could not be read", self._name)
    value = None
with self._lock:
    self._value = value
    self._taken_at = self._clock()
```

The previous value is overwritten with `None` **and** marked as taken now. Two
consequences:

* `ChildProcess.running` reads `None` as "still there" by design
  (`vmd/desktop/services.py:421`) — safe direction — but `liveness_age()` now
  resets to zero on every failed read, so
  `LIVENESS_UNANSWERED_SECONDS` (`:1654`), the check that stops the console
  "inventing health" about an adopted recorder, can never fire when the reads are
  *failing* rather than *hanging*.
* The radio's panel loses the last figure it had rather than ageing it, which is the
  case `_signal_lines`' staleness rule was written for.

**What I would do.** On failure, keep `self._value` and leave `_taken_at` alone;
clear `_pending` only. Then `age` means what everything downstream believes it
means.

---

## 13. events.db and segments.db only shrink when retention deletes footage

`EventStore.delete_before` is called from exactly one place: `_reclaim_events`,
driven by segments that retention actually removed (`vmd/storage/retention.py:171`).
With `budget_enabled` false and `retention_days` None — both reachable from the
Settings tab — `plan.delete` is always empty, so:

* segments.db grows one row per segment for ever (~288 rows/stream/day at 300 s);
* events.db grows one row per confirmed track for ever, and a treeline with a
  swaying branch on a windy week produces a great many.

`PlaybackTab._reload` calls `index.all(stream)` and `refresh_streams` calls
`index.all()` — full-table reads that build a `Segment` object per row — on every
day change. After a year of budget-off recording that is a Playback tab that takes
seconds to answer, on the GUI thread.

Low harm because the disk fills first and that *is* reported. Worth one line in the
Settings tab: turning the budget off turns off the only thing that ever prunes the
two databases.

---

## 14. Smaller things, with what each would actually cost

* **`_pid_alive` has no timeout and matches by substring** (`vmd/desktop/services.py:1073`).
  `return str(pid) in result.stdout` — compare with `process_image`
  (`:256`/`vmd/streaming/go2rtc.py:341`), which parses the CSV *and* passes
  `timeout=15`. A `tasklist` that wedges wedges the `BackgroundValue` reader thread
  for ever (`close()` gives up after 2 s and abandons it). Two functions answering
  the same question two ways is the thing that becomes two opinions in a month.
* **The recorder's `status()` reaches nobody.** `held_back`, `stalled`,
  `stuck_deletions`, `empty_segments`, `stall_restarts` and `restarts` are all
  computed (`vmd/record_main.py:709`) and printed only by `--once`. The console
  derives everything from the folder, which is the right primary signal — but
  "ffmpeg is being held back on ch2" and "3 segments cannot be deleted" are things
  the folder cannot say, and there is no channel for them. The detector already has
  one: `detection.json`. A `recording.json` written on the same pattern would cost
  little and close the gap.
* **Playback's hour ticks ignore the DST fix.** `day_bounds` correctly resolves a
  23- or 25-hour day (`vmd/desktop/timeline.py:38`) and `coverage_bars` uses the real
  span — but the rules are drawn at `hour / 24.0 * width`
  (`vmd/desktop/playback.py:135`). On the two days a year the rest of that module
  was written for, every hour label is up to an hour out, on the tab whose whole job
  is turning a position into a time.
* **`recorder.out.log` and `recorder.err.log` are never rotated**
  (`scripts/recorder_service.ps1`), while `autostart.log` beside them is rotated at
  1 MB. In steady state the recorder is quiet, so this is slow — but the failure
  mode it appears in is a recorder logging something every few seconds, which is
  where an unbounded log file on the system drive is least welcome.
* **The Live tab keeps decoding panes for a tab nobody is looking at.**
  `hideEvent` stops the steering only (`vmd/desktop/live.py:1356`);
  `frame.isVisibleTo(self)` is still true when the whole tab is hidden, so switching
  to Settings leaves libVLC decoding both streams. `_apply_view`'s own comment says
  what that costs: "it is a dedicated machine with one job and no headroom to spare".

---

## What I would leave alone

* **The zero-byte / empty-segment reporting, the flapping counters, the claim files
  and their companions.** These are the parts that were rebuilt today and they are
  right. My complaints about them (§6, §4) are that the rule is not applied
  everywhere, not that the rule is wrong.
* **`stop_all()` not being called when the window closes.** Deliberate, documented,
  and correct: the recorder outliving the console is the first requirement.
* **The unread pipe held by a console that exits.** The recorder, the detector and
  go2rtc all write into a pipe whose reader dies with the console. Python's
  `logging` swallows the resulting `OSError` in `Handler.handleError`, and go2rtc is
  a Go binary that ignores a failed stdout write. It costs the output, which the
  code already says out loud in `_announce_adoption`. Not worth changing.
* **`Tracker._next_id` and `StreamDetector._frame_index` growing without bound.**
  Python ints. Nothing wraps.
* **The `is_live` TCP-connect check in `endpoint.py`.** On its own it is exactly the
  weak evidence today's lesson is about, but the console pairs it with
  `unadoptable()`, which asks the API what is actually being served. The recorder
  does not — that is §1, and the fix belongs there rather than here.
* **`BudgetedClassifier` spawning a thread per classification.** Serialised by
  `_busy`, daemon, one frame held. A model that wedges permanently leaves `_busy`
  set and every later call skipped — which is caught and reported by
  `NEVER_NAMED_AFTER`. That is the right answer.

---

## What I probed and found genuinely sound

So that silence here is not read as coverage.

* **Segment coverage arithmetic.** `_end_of` (`vmd/record_main.py:1008`), the
  overlap clamp, `next_segment_start`, `coverage_bars` and `seek_target`. I looked
  for off-by-one and double-coverage and found the case already reasoned through,
  including why clamping beats truncating.
* **Daylight saving in `day_bounds`.** Correct: it advances the calendar date and
  re-resolves through the local zone. Only the hour *ticks* were missed (§14).
* **`save_settings` and `_write_json_atomically`.** Temp file in the destination
  directory, `fsync`, `os.replace`, cleanup on any `BaseException`. Nothing here can
  leave a half-written settings.json or detection.json.
* **Password handling.** `redact` / `without_passwords` / `_without_secrets` /
  `secrets_of` all cover both the typed and the percent-encoded form, the regex in
  `vmd/desktop/logs.py:75` is length-bounded against catastrophic backtracking, and
  `_secrets` drops empty strings so `"".replace("", "****")` cannot happen. The
  report writer redacts on the way out rather than at each source.
* **`PtzCommands`.** The latest-value mailbox, the never-dropped stop, the bounded
  `close`, `_ensure_thread`, and the four places that call `stop_steering`
  (focus out, hide, close, pointer leave). I tried to find a path that leaves the
  head slewing with no key held and could not — with one caveat: a stop queued while
  `fit_encoders_to_link` holds `PtzService._lock` waits for the ONVIF round trips to
  finish. Bounded by `TIMEOUT = 2.0` per call and only reachable while the operator
  is pressing a Settings-tab button, so I have not ranked it, but it is the one
  place the mailbox's guarantee is not the whole guarantee.
* **`VlcVideoPane.release`.** Guarded, idempotent, called from `LiveTab.apply` on
  every rebuild. python-vlc frees nothing on collection and this is the thing that
  stops each Save leaking an instance.
* **The claim files.** `claim_recorder` (`O_CREAT|O_EXCL`, retry, clear-and-retry
  rather than overwrite), `running_recorder` (boot time, then image name, with ""
  meaning "could not tell" and reading as alive), `release_recorder` (only while it
  still names us), and `_is_ours` comparing process start time against the claim's
  `written_at`. I looked specifically for a path where a recycled PID is adopted or
  a live recorder's claim is deleted and did not find one.
* **`LogBuffer`.** Properly locked, monotonic sequence numbers assigned under the
  same lock as the append, and `LogsTab.refresh` uses them for an exact no-change
  test rather than a heuristic. This is the one shared-state object in the console
  that is unambiguously right, which is why §9 stands out.
* **`Supervisor`'s stability accounting.** `short_lived`, `settled`, `_started_once`
  and the "seeing it alive counts as having seen it start" rule. The counters answer
  the question that was actually being asked.
* **The offline guarantees.** `YOLO_OFFLINE` / `YOLO_AUTOINSTALL` /
  `YOLO_CONFIG_DIR` set in `vmd/__init__.py` before anything imports ultralytics;
  weights existence checked before `from ultralytics import YOLO` so nothing is
  fetched; `webrtc.listen` emptied *and* `ice_servers` emptied; `ProxyHandler({})`
  on every opener in both the radio and the PTZ; `uv run --offline --frozen
  --no-sync` in the launcher. I went looking for a path that touches the network and
  found each one already closed, deliberately, with the reason written down.
