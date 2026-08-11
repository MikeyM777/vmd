# Console review — 12 August 2026

What the console looks like to the man who uses it, in the states he actually
meets it in. Every screen below was rendered off-screen with
`QWidget.grab().save()` under `QT_QPA_PLATFORM=windows`, at **1366×768** and
**1920×1080**, and looked at. Nothing here is inferred from reading the source;
where the source is quoted it is to say where a thing on the screen came from.

Screenshots live under

```
C:\Users\noams\AppData\Local\Temp\claude\C--Users-noams\aa7ba20c-a15d-44c6-b870-61c74dd28796\scratchpad\review\
```

and are named below without that prefix. `-1366x768` and `-1920x1080` are the
two sizes of each.

**States rendered:** first run (no settings, no camera, nothing recorded);
everything working; everything broken (no camera, no radio, no recordings, no
go2rtc); camera up / radio down; radio up / camera down; one view playing and
one failed; the link at 88 % airtime; an alarm up; fullscreen; Playback with a
day loaded, zoomed, and with a clip marked; Settings top, middle and bottom,
with a card folded and unfolded; Logs empty and full; the services unable to
answer.

---

## The five that matter most

### 1. A red box that says `recording` while nothing is being recorded

`20-broken-live-1366x768.png`, `20-broken-live-1920x1080.png`

**What he sees.** Four boxes across the top, all outlined in alarm red. They
read, left to right:

```
■ recording    ■ streaming: go2rtc is not installed - run install.bat    ■ detection    ■ link
```

Nothing is being recorded, nothing is being watched, and the radio is not
answering. Three of the four chips are drawn in the *healthy* vocabulary — the
name of the part and nothing else — inside a box that means "this has failed".
The words say one thing and the box says the opposite, and the words are what
gets read.

**Why it matters.** This is not a corner: it is the ordinary shape of a bad
morning on this deployment, because the streaming server being down takes
recording and detection with it. The band exists so the state of the machine can
be read from two metres. At two metres what is legible is the word, not the one
pixel of border and not the difference between `●` and `■`. "recording" in red
is the single most dangerous string this console can draw, because the fact it
misreports is the one the whole system exists to guarantee.

**Where.** `vmd/desktop/window.py:629-667` (`StatusBand.show_parts`) hands the
speaking chip its sentence and every other chip its *glance* word;
`vmd/desktop/window.py:1309-1344` (`ConsoleWindow.status_parts`) builds those
glance words as `"recording"`, `"streaming"`, `"detection"`, `"link"` regardless
of state. The one-sentence rule itself is right and the comment defending it is
right — four fault sentences do not fit across 1280 logical pixels.

**What I would do.** Give each part a *faulted* short word beside its healthy
one, and let a silenced chip show that: `NOT recording`, `no pictures`,
`no detection`, `no link`. Four strings, one extra element in the tuple, and the
band keeps its one line and its one sentence. `band.chips()` is already tested;
this is a change to what it returns in the fault states, not to its shape.

---

### 2. A camera that has stopped is invisible from two metres

`32-halfstream-live-1366x768.png`, `32-halfstream-live-1920x1080.png`

**What he sees.** The band is entirely quiet: `● recording  ● streaming  ●
detection  ● link`. The perimeter has two views and one of them has been a black
rectangle for however long. The only thing on the screen that says so is
`visible  -  failed`, eleven pixels high, in the top-left corner of the dead
picture (`vmd/desktop/live.py:1698-1716`, drawn at `SIZE_HEADING`).

**Why it matters.** The band reports *services* — the recorder process, the
streaming server, the detector, the radio — and never *views*. But what he is
paid to know is whether he can see the fence, and half of the fence has been
gone since some time he cannot name. Both black rectangles look the same at two
metres whether they are showing a dark field at night or showing nothing at all.
This is the clearest "looks fine but is a fault" on the screen.

**What I would do.** Two things, both small:

- A chip in the band when a configured view is not playing:
  `visible: no picture`. It is a fault of the machine, which is what the band is
  for, and it is the only fault of the machine that has no chip.
- Say it **in the empty picture**, not only above it. Every other empty area in
  this console says which of "nothing yet" and "this failed" it is; the video
  well is the one that does not. `no picture from visible` in the middle of the
  well, in the same muted ink `NO_VIEWS_NOTE` uses, costs nothing when the
  picture is there because it is covered by it.

---

### 3. "Nothing has moved yet." while nothing is watching

`20-broken-live-1366x768.png` (right column), `vmd/desktop/live.py:206`, `:1026`

**What he sees.** Detection has stopped — the band says so, in a red box, in the
truncated form of finding 1 — and the Recent movement panel says, in the middle
of itself:

> Nothing has moved yet.

**Why it matters.** That sentence is reassurance, and it is the wrong one of the
two things an empty list can mean. `DESIGN.md` states the rule for exactly this
case: *"each says which of 'nothing has happened' and 'this failed to load' it
is … on the movement list the difference is whether anything has crossed the
perimeter."* The panel is currently the only empty state in the console that
gets its own rule backwards. An operator glancing at a quiet movement list while
the detector is dead is being told the perimeter is clear by a system that is
not looking at it.

**What I would do.** The Live tab already receives the detection state on every
heartbeat through the same services object the band reads. When detection is
enabled and not running, the empty note becomes *"Nothing is being watched right
now, so nothing would be listed here."* When detection is switched off
altogether, *"No view is being watched. Tick a view in Settings."* Same widget,
same place, one branch.

---

### 4. `DESIGN.md` promises the alarm makes a sound. It does not.

`DESIGN.md:214` — *"An arriving alarm changes state instantly — outline, strip,
sound."* `PRODUCT.md:97` — *"The only motion that matters is an alarm arriving."*

There is no sound anywhere in this program. `grep -rni "sound\|QSound\|winsound\|
beep\|QAudio" vmd/` returns one hit and it is the word "sounds" inside a comment
(`vmd/desktop/services.py:935`).

**Why it matters.** The alarm itself is excellent to look at — see *What is
good* below — and everything it does is visual. The console runs 24/7 on a
laptop watched by one man who is also doing other things. An alarm that only
exists on a screen is an alarm that is missed for as long as he is not looking
at the screen, and this system's stated purpose is *"know immediately that
something is moving"*.

**What I would do.** Decide which document is wrong and make them agree. If the
sound is wanted, it is one `QSoundEffect` on a bundled `.wav` fired from
`_raise_alarm` (`vmd/desktop/live.py:1111`), off by default is *not* the right
answer here, and it must not repeat — one short sound per event, never a loop,
because a loop is a thing that gets muted. If it is not wanted, delete the word
from `DESIGN.md` rather than leaving a promise nobody has checked.

*Marked: I cannot judge from here whether the deployment laptop has usable
speakers. That is a real-machine question.*

---

### 5. The first sixty seconds are still the hardest path in the application

`01-firstrun-live-*.png`, `03-firstrun-settings-*.png`, `02-firstrun-playback-*.png`

He opens VMD on a laptop that has never run it.

- The band is honest: one red box, `NOT recording`. Good.
- Three quarters of the window is black with one line of the **quietest ink on
  the palette**, centred in the void: *"No pictures. Add a camera view in
  Settings."* It is the right sentence and it is the smallest, dimmest thing on
  the screen. It is still not a button — `vmd/desktop/live.py:117`, `:724`; the
  only `setCurrentIndex` on the tab bar in the whole of `vmd/desktop/` is the
  alarm's `Show me` (`window.py:1092`) and fullscreen restoring its page.
- In Settings the fields are blank with nothing marked required and no order to
  work in. The one button that would save him typing an RTSP address —
  **Find the right path** — still refuses to run until he has already typed one:
  `vmd/streaming/diagnose.py:162-165` returns *"No stream is configured, so
  there is no address to work from."* It has the camera address, the username
  and the password. Refusing on first run is refusing at the only moment it is
  genuinely needed.
- When it does find addresses they are printed into a read-only box
  (`diagnose.py:200`) and he retypes one by hand.
- **Save** is at the bottom of a form about 1700 px tall, below a 270 px
  troubleshooting panel he will use twice a year (`73-settings-bottom-*.png`).
  There is no `Ctrl+S`; there is not one `QShortcut` or `setShortcut` in the
  whole codebase.
- Success is the word `Saved.` in `PALETTE["muted"]`, at the opposite end of the
  row from the button he just pressed (`75-settings-save-refused-1366x768.png`,
  `settings_tab.py:1773-1783`).

Everything in that list was in `experience.md` five months of commits ago and is
still true. Individually each is small; together they are the first hour of the
only person who will ever set this up.

---

## Defects

Things that are wrong, not things that could be nicer. Screenshot names are
relative to the review folder above.

| # | What | Where | Evidence |
|---|---|---|---|
| D1 | A silenced faulted chip shows the healthy word: a red box reading `recording` while nothing is recorded | `window.py:629-667`, `window.py:1309-1344` | `20-broken-live-1366x768.png` |
| D2 | A view that has failed is reported nowhere except an 11 px label on the dead picture | `live.py:1698-1716` | `32-halfstream-live-1366x768.png` |
| D3 | "Nothing has moved yet." is shown while detection is stopped | `live.py:206`, `live.py:1026` | `20-broken-live-1366x768.png` |
| D4 | The band and the Link panel disagree about the same radio, on the same screen, at the same moment. At 88 % airtime with a healthy signal the panel says **`■ FULL`** in alarm red — *"Nothing else fits - the picture can stutter or drop during a pan."* — while the band four inches above shows a **quiet green `link` chip**, no box, healthy vocabulary. `_link_state` reads `signal_dbm` and nothing else, so airtime cannot reach it. With a weaker signal it is still wrong the other way: the band goes amber and quotes `-71.0 dBm`, a figure about the wrong quantity. `radio/panel.py:129-138` states the rule this breaks: *"the two views may never disagree about the same radio"* | `window.py:255-299`, `window.py:1386-1410` | `41-linkfull-good-signal-live-1366x768.png` (green chip over a red `FULL`), `40-linkfull-live-1920x1080.png` |
| D5 | Pressing **Show me** while in fullscreen leaves the console on the Playback tab with no status band, no tab bar and no visible way back | `window.py:1062-1096` changes the tab and never leaves fullscreen | `52-fullscreen-then-show-me-1366x768.png` |
| D6 | Pressing **Ignore parts of the picture** on a stream card draws text over text — "How touchy:" lands on top of the last line of the note above it, and two more lines collide below | `settings_tab.py:368`, `:395`, `:508-509` | `80-settings-ignore-parts-open-1366x768.png`, same at 1920 |
| D7 | The Logs filter gives no sign which of `All` / `Warnings and errors` is on. The two renders are pixel-identical | `logs.py:179-184` — plain `QPushButton`s, not checkable, not grouped, unstyled | `14-logs-with-lines-1366x768.png` vs `15-logs-warnings-filter-1366x768.png` |
| D8 | A refused save named the field by its Python attribute path — `storage.retention_days: Input should be a valid integer, unable to parse string as an integer` | `settings_tab.py:_first_problem` | `77-settings-bad-travel-1366x768.png` — **fixed today, see below** |
| D9 | "Roughly 1 minutes left before the drive is full" | `disk.py:_duration` | **fixed today, see below** |
| D10 | Timeline zoom lands on an empty window with no way to see where the footage is: with no playhead set, **1 hour** centres on the middle of the day. On a console that recorded 00:00–01:35 it jumps to 11:30–12:25 and draws an empty bar, while the line beneath still says "1h 25m recorded" | `playback.py:921-931` | `60-playback-day-1366x768.png` then `61-playback-hour-1366x768.png` |
| D11 | ~~A test on master is failing right now.~~ **Fixed while this was being written, by `5693933`.** `tests/test_desktop_window.py::test_live_and_playback_read_the_same_movement` was red for about twenty minutes: after any heartbeat, `show_day` for a camera the catalogue had no segments for left the Camera selector **empty** and dropped every movement mark — `show_day` added the missing name, then `_reload` called `refresh_streams` again and cleared it. Introduced by `545bf10` (01:45 today), closed by `5693933`. Kept here because the failure is instructive: a first-morning console has an empty catalogue, so this was the state he would have met | `playback.py:830-845`, `playback.py:855-865`, `playback.py:775-799` | reproduced before the fix; see *How to reproduce D11* |
| D12 | On Playback the same date is drawn twice, 40 px apart — once in the day-picker button and once as the heading below it | `playback.py:641-656`, `:668-686` | `60-playback-day-1366x768.png` |
| D13 | Two whole help paragraphs are printed once per camera view, side by side and word for word identical. The commit that removed one such pair (`fcd32f2`) left these two | `settings_tab.py:368` (`CLASSIFY_HELP`), `settings_tab.py:395` (`REGIONS_HELP`) | `78b-settings-stream-card-watched-1366x768.png` |
| D14 | The Link panel says the same thing three times when opened: the headline note *"Nothing else fits - the picture can stutter or drop during a pan."*, then *"Airtime: 88% used - the link is full"*, then *"Nothing else will fit on it. A picture that stutters, falls behind, or drops during a pan is this…"* | `radio/panel.py` (`link_summary` note and `_traffic_lines`) | `42-link-details-open-1366x768.png` |
| D15 | At 1366×768 the Recent movement table is cut through the middle of a row of text rather than at a row boundary — a half-height line of glyphs that reads as a rendering fault | side column, `live.py:979-1037` | `30-radiodown-live-1366x768.png` (bottom right) |
| D16 | The zoom readout is a bare number: `42%`, with no noun anywhere on the control. The word "zoom" appears only in the *failure* caption (`zoom not reported`), so the control names itself only when it is not working | `zoombar.py:216-223` | `10-working-live-1920x1080.png` |

### How to reproduce D11 (against `545bf10`, before `5693933`)

```python
from tests.test_desktop_window import FakeServices, FakePtz, FakeRadio, write_settings, beating
# window built with events_path, one event written for 2026-08-11
beating(window, lambda: len(window.live.recent_rows()) == 1)   # any heartbeat at all
window.playback.show_day(2026, 8, 11, stream="thermal")
window.playback.stream_names()   # [] — it was ['thermal'] before the heartbeat
window.playback.event_marks      # []
```

Without the heartbeat the same call left the selector holding `thermal` and one
mark on the bar. It was another agent's commit in another agent's file, so it
was reported rather than fixed here, and that agent closed it the same hour. As
of this document the whole suite is green.

---

## Words he would not understand, or would misread

Rendered and confirmed on screen. This is the category he complained about by
name, so it is listed in full rather than summarised.

| Says | Where | Why it is wrong | Should say |
|---|---|---|---|
| **Must travel at least (dots)** | `settings_tab.py:1078`, `03-firstrun-settings-1366x768.png` | Asks for a number in a unit he cannot see anywhere, about a thing he has no word for. Its placeholder points at "the touchiness setting" — which is called **How touchy:** and lives *inside* a camera card, folded away until **Watch for movement** is ticked. Its own tooltip says *"Leave it empty."* | Delete the field. A control whose tooltip tells you not to use it, referring to a control that is not on the same screen, is not a setting — it is a developer's escape hatch. If it must stay, move it beside **How touchy:** on the card and call it *"Ignore movement smaller than"* |
| **streaming: go2rtc is not installed - run install.bat** | band, from `streaming/go2rtc.py`; `20-broken-live-*.png` | Names a program he has never heard of and a file he cannot run. He has no terminal | *"Part of VMD is missing, so there are no pictures. Reinstall VMD."* — unchanged from the last review, still unfixed |
| **the services could not be asked what they are doing** | `window.py:1291-1297`; `23-services-mute-live-1366x768.png` | "services" is a word from the source. And the sentence ends without saying what to do | *"VMD cannot see its own recorder and detector. Restart VMD."* |
| **ptz zoom_poll failed unexpectedly…** | Logs tab; `14-logs-with-lines-1366x768.png` | A Python function name, in the tab he opens when something is already wrong | Say what could not be done: *"the camera would not say where its zoom is"* — which is the phrasing used two lines below it, by the same subsystem |
| **Budget (GB)** | `settings_tab.py:1146`; `71-settings-mid-1366x768.png` | "Budget" is a money word. He is being asked how much disk VMD may use | *"How much space VMD may use (GB)"* |
| **Scan this PC** | `settings_tab.py` storage box; `71-settings-mid-1366x768.png` | Reads as a virus scan or a search for cameras. It looks at the drive and suggests a size | *"Look at this drive and suggest a size"* |
| **It never goes below the lowest picture you allow** | Radio box; `73-settings-bottom-1366x768.png` | Refers to a "lowest picture you allow" setting that is not on this screen, or on any screen — it is `bitrate.floor_kbps` | Either show the floor, or say the number: *"It never asks for less than 1 Mb/s."* |
| **Fit the camera to the link** | `settings_tab.py` camera tools; `73-settings-bottom-1366x768.png` | Reads as an alignment or a mounting instruction | *"Turn the picture down to what the link can carry"* — which is what the Radio checkbox two boxes up already says in plain words |
| **Find the right path** | camera tools | "path" is the RTSP path. To him a path is a track | *"Find the camera's address"* |
| **Save it… / Clear** | Playback transport, far right; `60-playback-day-1366x768.png` | Save *what*. Clear *what*. Both are about the marked range and neither says so | *"Save the marked clip" / "Unmark"* |
| **Watch for movement** (per card) vs **Watch for movement at all** (the box below) | `settings_tab.py:317`, `:1030` | Three controls with almost the same name on one screen, two of them identical; `78b-settings-stream-card-watched-1366x768.png` | The master switch reads *"Watch for movement on any view"*; the per-card one reads *"Watch this view"* |
| **Acknowledge** | alarm strip; `50-alarm-live-1366x768.png` | A formal word next to a plain one (**Show me**) | *"Seen it"* |
| **Follow** | Logs, top right | Follow what | *"Jump to newest"* |
| **the status line at the bottom** | `docs/FIRST-MORNING.md:361` | There is no status line at the bottom; it has been a band at the top for a while. The doc has drifted | *"The band across the top"* |

---

## Improvements

Ordered by what they buy him, not by cost.

1. **Take him somewhere when a chip is red.** The alarm → Playback jump landed and
   it is the best change in the console. The same idea is missing from the band:
   clicking a faulted `streaming` or `recording` chip should open Logs, a faulted
   `link` or `detection` chip should open Settings, and quiet chips should stay
   inert so nothing invites a pointless click. `window.py:629-667`.
2. **Make `No pictures. Add a camera view in Settings.` a button** that opens
   Settings with a stream card already added and the name field focused.
   `live.py:117`, `:724`.
3. **Make `Find the right path` work with nothing configured** and offer what it
   finds as buttons that write into the field. `diagnose.py:162-165`, `:200`.
   This is the largest single reduction in typing in the application and it is
   in the hour he needs it most.
4. **Pin Save (and its message) to the bottom of the Settings tab**, outside the
   scroll area, and add `Ctrl+S`. Draw `Saved.` in `--ok` with the `●` glyph and
   let it fade after a few seconds. `settings_tab.py` scroll area and `ending`
   row.
5. **Give the Playback picture an empty state.** On first run it is a black
   rectangle 1350×450 with nothing in it and the only explanation is eleven
   pixels of muted mono in the bottom-left corner of the window
   (`02-firstrun-playback-1366x768.png`). Everywhere else in this console an
   empty area says what it is.
6. **Disable the Playback transport until there is something to play.** With no
   recordings, `Play`, the six skip buttons, `Mark start`, `Mark end` and `Clear`
   are all live and all do nothing. `Save it…` is correctly disabled, which
   proves the pattern is available. This is the exact failure the zoom bar's own
   comment describes: *"a live-looking slider whose buttons quietly go nowhere
   is the shape of failure this whole readout exists to remove."*
7. **A legend under the Playback bar** — `green = recorded · red = something
   moved · amber = where you are`. Three colours carry the whole tab and none of
   them is named anywhere.
8. **Name the two unlabelled arrows** beside the timeline zoom buttons
   (`◀` `▶`, `playback.py:711-...`). They pan the window; nothing says so
   without hovering.
9. **A `Browse…` button beside the Storage folder.** `QFileDialog` is already
   imported and used by *Save a report*. Today it is a long path in a box, shown
   from its middle, typed by hand.
10. **Give the Storage panel the treatment the Link panel got.** They sit one
    above the other in the same column; one is two bars, a word and a
    disclosure, and the other is four sentences (`10-working-live-1920x1080.png`).
    The link panel is the model and the operator asked for exactly this.
11. **The two storage countdowns read as two warnings and only one is.** *"About
    21 hours left before the oldest footage starts being deleted"* is retention
    working as designed; *"About 10 days left before the drive is full"* is a
    problem. Identical phrasing, identical colour.
12. **`idle` is the largest thing in the side column** — brighter and bigger than
    the link headline beside it, on the panel that matters least of the four
    (`10-working-live-1366x768.png`). It should be the quietest thing there when
    nothing is moving.
13. **The steering box invites keys that go nowhere** when the camera is
    unreachable: it says `idle` and lists the arrow keys with no note that
    nothing will answer (`20-broken-live-1366x768.png`). The camera's own
    unavailability is already known — `PtzService.status()` returns it.
14. **Show the time under the pointer on the Playback bar.** At whole-day zoom
    one pixel is over a minute and he is aiming blind.
15. **`Save it…` is offered over a marked range containing no footage at all**
    (`62-playback-marked-1366x768.png` — the marked block sits entirely in the
    part of the day that has not happened yet).
16. **In fullscreen there is no health at all.** The band is the thing that
    would tell him recording had stopped, and fullscreen is the mode he is most
    likely to leave running. A single faulted chip should survive into
    fullscreen; the three quiet ones should not.
17. **The address field accepts anything** that does not look like a URL scheme —
    `192.168.1.64/ch2` and `not-a-url` both save without complaint (deliberate,
    so a local file can be read: `settings.py:99-127`). The cost is that the
    commonest typo in the application produces no feedback until a black
    rectangle appears. A note beside the field — *"this does not look like an
    rtsp:// address"* — would not have to refuse the save.

---

## What I checked and found fine

This is not a short list, and it should not be read as padding — most of the
console is in good shape and the parts that are good are good for stated
reasons.

- **The alarm.** `50-alarm-live-1366x768.png`. A full-width red strip *below*
  the pictures, a red outline on the pane that saw it, a glyph as well as a
  colour, and — new since the last review — **Show me** beside **Acknowledge**,
  which takes him to the footage in one press. It reads across a room and it
  does not cover the thing it is about. It survives into fullscreen
  (`51-fullscreen-live-1366x768.png`).
- **The Link panel.** `10-working-live-*.png`, `40-linkfull-live-1920x1080.png`,
  `42-link-details-open-1366x768.png`. One word (`GOOD` / `FULL` / `NO LINK`),
  one short line saying what to do, two bars with threshold hairlines, and a
  `▸ Details` disclosure holding the sentences. At 88 % airtime it says `FULL`
  and *"Nothing else fits - the picture can stutter or drop during a pan."*
  This is a direct and successful answer to *"much less text in the link tab …
  make it visual"*, and it is the best-designed panel in the console.
- **The status band's restraint** when things are well. Four quiet chips, no
  boxes, no wall of green, one line high. Correct, and hard-won.
- **The recording dot.** Pulses while footage is arriving, a still bar when it
  is not; the two differ by movement, not only by colour.
- **The empty states**, apart from D3 and the Playback picture: *"Nothing has
  been recorded yet."*, *"Nothing has been logged yet."*, *"Press one of the
  buttons above and what the camera says appears here."*, *"No pictures. Add a
  camera view in Settings."* Someone went through these deliberately.
- **The window remembers its size and position** (`window.py:131`, `:894`,
  `:1440`). Fixed since the last review.
- **The movement-mark click target.** `MARK_CLICK_PIXELS` now floors the
  tolerance at the width actually drawn (`playback.py:169-171`, `:1043`). Fixed.
- **The Playback transport exists**: back a minute / ten seconds / a second,
  Play, the same three forward, and a speed selector. The zoom buttons carry the
  amber active mark the design reserves for an active control.
- **Lowering the budget warns before it deletes** and needs a second Save
  (`settings_tab.py:1675`).
- **The Settings tab is genuinely smaller.** Six controls per camera view are
  folded behind **Watch for movement**, the reader combo is gone from the screen
  but not from the file, and the paragraph that used to be printed once per card
  is now printed once. All four of his named complaints — *"Use this view"*,
  *"auto vs ffmpeg"*, *"Name what moved"*, *"Skyline and ignore…"* — have been
  answered by removing or renaming the control rather than by adding a tooltip.
- **Validation of the things that break the machine.** A recordings folder on a
  drive that does not exist, a folder that is really a file, an `http://` stream
  address: all refused in words, and a refused save writes nothing.
- **Passwords shown, not masked** — deliberate, documented, and right for this
  machine.
- **Nothing refuses to open.** A tab that cannot be built becomes a label saying
  why and the other three still work.
- **Fullscreen is clean** (`51-fullscreen-live-1366x768.png`): pictures, the view
  chooser, the zoom bars, the alarm strip, and `Leave fullscreen  (Esc)` which
  names its own way out.
- **1366×768 is survivable.** Nothing is clipped off the right-hand edge, the
  form column stays at its 980 px cap and centres, and the band stays one line.
  The side column does overflow (D15) but it scrolls.

---

## Status of `docs/review/experience.md`

The previous review of the same product. Every finding, judged against what is
on the screen today.

`experience.md` gained its own *"Since this was written"* table a few hours
before this review was written (`e7384f7`), from the commits. This section was
arrived at from the screen instead, and the two agree everywhere except one
place, which is worth saying: that table calls finding **7**'s
*"Roughly 1 minutes left"* open — it was, and it is fixed below — and it does
not mention that findings **10** and **6a** are still open for the reasons given
here rather than for want of a commit.

### Fixed

| # | Finding | Evidence |
|---|---|---|
| 1a | Alarm strip: `Show me` beside `Acknowledge` | `50-alarm-live-1366x768.png`; `live.py:930-978` |
| 1b | Double-clicking a movement row does the same | `live.py:1130-1133` |
| 2 | A radio that refuses the login is a fault in the band, and the panel's sentence is short and actionable | `30-radiodown-live-1366x768.png`; `window.py:255-299`, `link_trouble` |
| 3 | Playback has a transport | `60-playback-day-1366x768.png` |
| 4 | Movement marks are clickable | `playback.py:169-171`, `:1043` |
| 6b | Lowering the budget warns and needs a second Save | `settings_tab.py:1675` |
| 7 (row 3) | `135 segments` is gone; the line reads *"1h 25m recorded on thermal on 12 August 2026."* | `60-playback-day-1366x768.png` |
| 7 (row 5) | **The camera** is now **Check the camera** | `73-settings-bottom-1366x768.png` |
| 7 (row 6) | `Roughly 1 minutes` | fixed today — see below |
| 8 | `Delete the selected patch` is now `Delete patch` at the place that clipped it | `settings_tab.py:498` |
| 12a | The window remembers its size and position | `window.py:894` |
| 12j | Storage no longer predicts from nothing: on first run it says *"No idea how long…"* rather than *"Roughly 6 days left"* | `01-firstrun-live-1366x768.png` |

### Still open

| # | Finding | Note |
|---|---|---|
| 1c | Band chips do not navigate | the only `setCurrentIndex` on the tab bar is the alarm's |
| 1d | `No pictures. Add a camera view in Settings.` is not a button | `live.py:117` |
| 5 | Save is below the fold; `Saved.` is muted grey; no `Ctrl+S` | `73-settings-bottom-1366x768.png`; no `QShortcut` anywhere |
| 6a | `Remove` deletes a view with no confirmation and no undo | `settings_tab.py:303` |
| 7 (row 1) | pydantic sentences with attribute paths | **fixed today** — see below |
| 7 (row 2) | `go2rtc is not installed - run install.bat` | `20-broken-live-1366x768.png` |
| 7 (row 4) | `Must travel at least (dots)` | `03-firstrun-settings-1366x768.png` |
| 9 | The Logs filter does not say which is on | `14-` vs `15-`, pixel-identical. `playback.py:697` even names this as *"the same fault the Logs tab's filters have"* while fixing it for itself |
| 10 | The wall does not even its panes | There is no `setSizes`, `setStretchFactor` or `setChildrenCollapsible` anywhere in `live.py`; the widths still fall out of size hints and insertion order. My own three-view render came out 335 / 337 / 331 (`16-three-views-live-1366x768.png`) and that is an **artefact of the harness** — three identical stand-in widgets have identical hints. The last review's mixed-state measurement, roughly 200 / 350 / 740, is the case that matters and nothing has changed to prevent it |
| 11 | Steering contradicts itself when the camera does not answer | I could not reach the state that shows both lines at once without a camera; the *invitation* problem is improvement 13 |
| 12b | `Browse…` beside the Storage folder | — |
| 12c | Feed `Find the right path` back into the field | `diagnose.py:200` |
| 12d | `Find the right path` with no stream configured | `diagnose.py:162-165` |
| 12e | A legend under the Playback bar | — |
| 12f | The time under the pointer on the Playback bar | — |
| 12g | Marks stopping at the hour rules | `playback.py:334-336` still paints `0..room` |
| 12i | Hold the alarm strip's count | `live.py:1111-1116` still overwrites |

### Come back / newly true

- **D14** — the Link panel's `Details` now says the airtime verdict three times.
  The panel that was shortened has grown a duplicate inside its own disclosure.
- **D13** — two paragraphs are printed once per card again. `fcd32f2` removed one
  such pair; `CLASSIFY_HELP` and `REGIONS_HELP` are still per-card.
- **D6** — text drawn over text in the Settings tab. This is the failure mode the
  `StoragePanel` comment (`disk.py:481-500`) documents and defends against; the
  stream card does not do the same thing.

### Its *"do not do this"* list

Checked against everything proposed above. Nothing here contradicts it. In
particular: no signal figure is proposed for the healthy `link` chip, no green
band, no masked passwords, no modal alarm, no auto-switch of the wall, no
confidence percentage on the strip, no tooltips as the fix for a bad label, no
second graph in the Live column, no confirmation on `Acknowledge`, and no change
to the four tabs.

One item of it needs re-reading in the light of D4: *"Do not put the signal
figure in the healthy `link` chip"* is right, and the band is currently doing
something worse — putting the signal figure in the **faulted** chip, where it is
the wrong reading entirely.

---

## Fixed in this pass

Two, both small, both with a failing test written first and a mutation check
afterwards.

**`Roughly 1 minutes left`** — `vmd/desktop/disk.py`, `_duration` / new
`_plural`. The plural was wrong exactly once, in the state the panel exists for:
the last minute before a drive fills, and the last hour before that (`1 hours`).
Test: `tests/test_desktop_disk.py::test_the_last_hour_of_footage_is_not_reported_as_1_minutes`,
which asserts through `storage_lines` rather than the private helper. Mutation:
forcing the `s` back on fails the test.

**A refused save named a Python attribute** — `vmd/desktop/settings_tab.py`,
`_first_problem`, plus `FIELD_LABELS` and `WANTED_NUMBER`. Typing `two weeks`
into **Delete older than (days)** answered
`storage.retention_days: Input should be a valid integer, unable to parse string
as an integer`; it now answers
`Delete older than (days): "two weeks" is not a whole number.` The field is named
as the screen names it, which matters on a form he has to scroll: the old message
could not tell him *where* to look. `Value error, ` — pydantic announcing its own
machinery — is stripped from the sentences this codebase wrote for him. Test:
`tests/test_desktop_settings_tab.py::test_a_number_that_will_not_parse_is_refused_by_the_name_on_the_form`.
Mutations: removing the label lookup fails it; removing the plain wording fails
it.

Everything else in this document is written down rather than changed.

---

## What I could not judge from here

- **Whether the laptop can make a sound at all** (finding 4).
- **Anything about the real camera**: how long the first frame takes over the
  radio link, whether the two lenses zoom independently, whether the picture
  survives a minute of panning. The panes in every screenshot are dark
  rectangles because there is no camera on this machine.
- **The real radio.** The 88 % airtime state was rendered from his own measured
  figures fed to `RadioService.status()`, not read from a radio.
- **Colour under daylight glare on the deployment screen.** Contrast ratios are
  stated in `DESIGN.md` and the palette honours them; whether `--muted` at 11 px
  survives the window behind that desk is a thing only that desk can answer.
- **Whether the alarm reads from two metres in the room it is in.** It reads from
  two metres in a screenshot, which is not the same claim.
- **Timing and motion**: the recording dot's pulse, the link panel's travelling
  bar fill, and the `readings arriving` mark are all animations. A still frame
  cannot show that they move, only that they are drawn — and the travelling fill
  is why the two bars look empty in `41-linkfull-good-signal-live-1366x768.png`
  while the figures beside them read −63 dBm and 88 %: the fill had not caught up
  in the three heartbeats before the grab. The code for each is present and
  reasoned; whether the fill settles fast enough to be read at a glance is a
  question for the running console.
- **How the video wall divides itself between real panes.** Every pane in these
  screenshots is a stand-in widget with no size hint of its own, so the splitter
  gives them equal room. A real `VlcVideoPane` does not, and that is the whole of
  `experience.md` finding 10 — see above.
