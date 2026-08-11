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
- **S — `test_live_and_playback_read_the_same_movement` is failing on master.**
  After any heartbeat, `show_day` for a camera the catalogue has no segments for
  leaves the Camera selector empty and drops every movement mark: `show_day` adds
  the name, then `_reload` calls `refresh_streams` again and clears it.
  Introduced by `545bf10`. `playback.py:830-845`, `:855-865`, `:775-799`.
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
