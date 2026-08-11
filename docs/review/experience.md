# Experience review — what is still hard to use

**Written 2026-08-11.**

---

## Since this was written — 2026-08-12

Added on top; **no finding below has been edited**. The measurements and the
reasoning are the value here, and several of these were closed by doing exactly
what the finding said, which cannot be checked if the finding is gone.

| Finding | Now | Commit |
|---|---|---|
| **1** — no sentence in the app takes him where the fix is | **The two that were the whole win are done.** The alarm strip has **Show me** beside Acknowledge, and double-clicking a row in the movement list does the same thing: it switches to Playback and opens that moment. Still open: the band chips are inert, and `NO_VIEWS_NOTE` is still a sentence rather than a button | `e5db594` |
| **2** — a radio that refuses the login is invisible in the band | **Closed.** A refusal reads as a refusal in the band, and the panel leads with a line he can act on rather than fourteen ending in a program to run | `952003b`, `615fddc` |
| **3** — Playback has no transport and no way to step between events | **Closed, and past what was asked for.** Play/pause, ±1 s, ±10 s, ±1 min, six speeds, all as buttons with words on them and keys as an addition, plus a calendar, three zoom levels on a pannable bar, both cameras on one timeline with the drift between them measured and printed, and a clip export | `dee471d`, `6cd9dab`, `a1119cd`, `e1f83a3` |
| **4** — a movement mark is under 1 px wide to click | **Closed.** The tolerance is at least as wide as what is drawn | `e19e18e` |
| **6** — lowering the budget deletes footage with no warning | **Half.** Save now says roughly how much of the oldest footage the new budget will delete and requires a second press. `Remove` on a camera view still has no undo | `6a73bc2` |
| **7** — words that name the machinery instead of the effect | **Mostly.** Four labels he read back as questions are renamed or explained on the form: `Name what moved` → **Try to say what it was**, `Sky line and ignored patches` → **Ignore parts of the picture**, `The camera` → **Check the camera**, and `Watch for movement` kept its name and got the sentence it was missing. `Use this view` and the auto/ffmpeg reader are off the screen entirely. Still open: `disk.py`'s *"Roughly 1 minutes left"* | `5d1946f`, `04bddaf`, `fcd32f2` |
| **8** — `Delete the selected patch` is clipped | **Closed.** It says `Delete patch`, and the four numbers beside it are two lines | `352fbc7`, `5d1946f` |
| **12** — remember the window size and position | **Closed**, on a screen that still exists | `a862b87` |
| **5**, **9**, **10**, **11** | **Open.** Save is still inside the scroll area, `Saved.` is still muted grey, and there is still not one `QShortcut` in the codebase (5); the Logs filter still does not say which one is on (9); the wall still does not even its panes (10); the steering panel still contradicts itself in bright ink (11) | — |

Also since: the Settings tab was cut down around the same complaint this review
opens with — the two camera views are side by side rather than stacked,
everything about detection folds away until the master switch is on, and Storage
grew a **Scan this PC** button that reads the drive and proposes a budget and an
age rule, with a slider that says what the budget means in days he can look back
(`5d1946f`). And there is a fullscreen mode and a zoom bar per lens, neither of
which this review covers.

The **"Do not do this"** list at the end still stands, with one entry that has
been argued against and should be read as amended rather than obeyed: **#8**,
*"do not add a second progress bar … to the Live column"*. The Link panel now has
two. The argument for them is that they did not *add* to that column — they
replaced fourteen sentences of it, and everything they replaced is still there
behind `Details` (`c476aee`). Whether that was the right call is a thing to look
at on the screen, not in this document. Nothing else on the list has been
contradicted, and it is still the most valuable page in this directory.

---

Every finding below was rendered and looked at, at 1920×1080, using an extended
copy of the console harness. Nothing here is inferred from reading alone. Where
a number appears (pixel widths, click tolerances) it was measured, not estimated.

---

## What it is like to use

**Cold start.** He opens VMD. The window comes up at 1440×900 in the middle of
the screen — not maximised, not remembered from last time (`app.py:207`) — so
his first action every morning is a chore the app could have done. The band
across the top immediately shows one red box: **NOT recording**. It is true, and
it is the loudest thing on the screen, and there is nothing he can do about it,
because nothing is configured yet. Three quarters of the window is black with a
single line of small grey text in the middle: *"No pictures. Add a camera view
in Settings."* That sentence is the best thing on the first-run screen — it says
what to do — and it is drawn in the quietest ink on the palette in the middle of
a void. Meanwhile the right-hand column confidently predicts *"Roughly 48 hours
left before the oldest footage starts being deleted"* and *"Roughly 6 days left
before the drive is full"* about a system that has never recorded a frame.

He goes to Settings. Six boxes, every field blank, no order, nothing marked
required, no "start here". He does not know that **Find the right path** — the
one button on the page that would save him from typing an RTSP address — will
refuse to run until he has already typed an RTSP address
(`diagnose.py:163-164`). So the real first-run sequence is: press *Add a
stream*, invent a name, guess a URL, press *Find the right path*, wait up to a
minute, read a list of addresses out of a read-only black box, and **retype one
of them by hand** into the field above (`diagnose.py:197-200`). That is the
worst path in the application and it is the first one he walks.

Then he scrolls. The form is about 1600 px tall and the Save button is at the
bottom of it, past a 300 px troubleshooting panel he will use twice a year. If
he mistypes the budget, the answer is `storage.budget_gb: Input should be a
valid number, unable to parse string as a number` — a library's sentence with a
field path in front of it. If it works, the answer is the word **Saved.** in
muted grey, 13 px, at the bottom of a page he has scrolled to the end of.

**Running.** Once it is up, the Live tab is good. Two pictures side by side, a
segmented All / thermal / visible control that matches what he asked for, an
instrument label on each picture saying what it is doing, and a status band that
is honest and quiet when there is nothing to say. When something moves, it is
genuinely excellent: a red strip across the bottom of the pictures, a red
outline on the pane that saw it, and a row in the movement list. It reads across
a room.

**Something goes wrong.** This is where it stops being kind. Every failure
sentence in the app describes; almost none of them acts. *"failed — not coming
back on its own; check the address in Settings"* — and he must find Settings
himself. *"streaming: the streaming server stopped"* — and then what. *"go2rtc
is not installed — run install.bat"* names a program he has never heard of and a
file he cannot run. When three things fail at once the band becomes 1000 px of
red boxes with no ranking, and the one failure that is *silent* is the radio:
when the link refuses the login the band says `link -` in ordinary grey, no box,
no colour (`window.py:126-149`), while the panel one screen below prints a
fourteen-line paragraph ending *"Run spike/probe_radio.py against this radio and
send what it prints."* He has no terminal.

**Watching an event back.** The alarm fired at 14:46:50 on thermal. He has one
button: **Acknowledge**. Nothing in the console will take him to the moment.
Counting: read and memorise the time; click Playback; set the Stream combo to
thermal; find the right red mark among thirty on a bar covering 24 hours; click
it. Five steps, three of which can go wrong. And the last one usually does — on
a 1200 px bar one pixel is 72 seconds and the tolerance for hitting a movement
mark is 30 seconds, so the clickable window is **0.83 px** for a mark drawn 3 px
wide. He aims at what he can see and lands on plain time, gets footage from a
minute away, and is told nothing about the miss. Once he does land on it there
is no pause, no replay, no step back, no next-event. If he blinks, he re-aims at
a sub-pixel target.

---

## Findings, in the order I would fix them

Sizes: **S** ≈ under an hour, **M** ≈ an hour or two, **L** ≈ half a day.

### First hour — the three that change the most

**1. No sentence in the app takes him where the fix is. Make the important ones
buttons.** — L (but do it in slices; each slice is S)

*Where:* everywhere. There is not one `setCurrentIndex` on the tab bar anywhere
in `vmd/desktop/` — the console never navigates the operator. Concretely:
`live.py:167` (`"check the address in Settings"`), `live.py:114` (`"Add a
camera view in Settings"`), `window.py:346-369` (band chips), `live.py:723-725`
(the alarm strip's only button is Acknowledge), `live.py:729-783` (the movement
table has no click handler).

*Why it costs him:* he is one person with no second machine. A sentence that
names a destination and does not go there is a sentence that assumes someone is
standing behind him. The gap between "I know something is wrong" and "I am
doing something about it" is entirely made of clicks he has to invent.

*What I would do,* in this order — the first two are the whole win:

- **Alarm strip: add `Show me` beside `Acknowledge`.** It switches to Playback,
  selects that stream and that day, and calls the existing
  `PlaybackTab.click_at`/`_play_at` path with the event. All the machinery
  exists — `_play_at(when, event=...)` already handles the five-second lead and
  the "no longer on disk" case. Five steps become one. `live.py:691-727` plus a
  signal the window forwards.
- **Movement list: double-click a row does the same thing.** Same call, same
  code. `live.py:729-783`.
- **Band chips: clicking a chip that is showing a fault opens the tab that fixes
  it** (recording/streaming → Logs, detection → Settings, link → Settings). Give
  faulted chips a pointing-hand cursor so it is discoverable; leave quiet chips
  inert so nothing invites a pointless click. `window.py:152-266`.
- Make `NO_VIEWS_NOTE` a real button — *"Add a camera view"* — that opens
  Settings with a new stream row already added and the name field focused.
  `live.py:114`, `live.py:552-556`.

*Do not* turn every sentence into a button. These four are the ones he needs
under pressure; the rest can stay words.

---

**2. A radio that refuses the login is invisible in the band, and the sentence
that explains it is unusable.** — M

*Where:* `window.py:126-149` (`_link_state`) and `window.py:775-794`
(`_link_words`); the sentence is built at `radio/airos.py:678-688`, carried by
`radio/service.py:107`, drawn by `radio/panel.py:109-116`.

*Rendered, verified:* with the radio answering 403, `band.chips()` returns
`['recording', 'streaming', 'detection', 'link -']` — the link chip is `muted`,
which means no box, no colour, the same drawing a chip gets while it is still
being checked. `_link_state` falls through to `"muted"` whenever `signal_dbm` is
not a number, and a hard authentication refusal is exactly that case. So the one
failure at the far end of a 700 m link is the one the band will not report.

Below it, the Link panel prints, in muted grey, fourteen wrapped lines:

> The radio answered HTTP 403 (Forbidden) to the login at
> http://192.168.1.20/login.cgi. It is reachable and it refused the request,
> which need not mean the password is wrong: airOS also answers 403 to a login
> sent without the session cookie from its own login page, to one that does not
> look like it came from that page, and after too many tries. All login flows
> were tried. **Run spike/probe_radio.py against this radio and send what it
> prints.**

*Why it costs him:* the band is what he glances at; the panel is what he reads.
Today the glance says nothing is wrong and the read tells him to open a terminal
he does not have. Both halves are backwards.

*What I would do:*

- In `_link_state`, add a branch before the `signal is not a number → muted`
  fallthrough: if `connected` is false and `checking` is false, the state is
  `alarm`. In `_link_words`, `link -` becomes the short version of the reason
  (e.g. `link: the radio would not accept the password`).
- Split the radio's sentences into **one line he can act on** plus the technical
  detail. The panel shows the first; the detail goes to the Logs tab, which is
  where technical detail belongs and where it is already reachable. Suggested
  first lines, one per case:
  - 401 → *"The radio would not accept the username or password. Check them in
    Settings → Radio."*
  - 403 → *"The radio is reachable but refused the login. Check the password in
    Settings → Radio; if it is right, wait a minute and it will try again."*
  - unreachable → *"No answer from the radio at 192.168.1.20. Check it is
    powered and that the address is right."*
- The word ban in `tests/test_desktop_settings_tab.py:156` and
  `test_desktop_picker.py:401` covers Settings and the picker only. Extend the
  same test to the Live tab's side column and the Playback status line, and add
  `HTTP`, `cookie`, `.py`, `.bat`, `go2rtc`, `rtsp` (outside the address field)
  and `segments` to the list. That is what stops this coming back.

---

**3. Playback has no transport and no way to step between events.** — M

*Where:* `playback.py:206-250`. The controls row is `Day`, `Stream`, and
nothing else. There is no play, pause, replay, or step.

*Why it costs him:* re-watching the same ten seconds is the single most common
thing anyone does with security footage, and here it costs a fresh sub-pixel
click on the day bar (see finding 4). He cannot pause on the frame that matters.

*What I would do* — a one-row strip under the picture, four controls, no more:

```
  ⟵ Previous movement    ⟲ Again (10 s back)    ⏸ Pause    Next movement ⟶
```

`Again` re-seeks to `playhead_time − 10 s` through the existing `_play_at`.
`Previous/Next movement` walks `self.event_marks`, which is already built and
sorted (`playback.py:354-377`), and calls `_play_at(event.started, event=event)`
— the same path the click takes, including the five-second lead and the
"no longer on disk" answer. Bind them to Left / Space / Right so the keyboard
works too. Pause needs one method on `VideoPane`; if that is more than an hour,
ship the other three first — they are the ones he needs.

---

### The rest of the half day

**4. A movement mark is drawn 3 px wide and is under 1 px wide to click.** — S

*Where:* `playback.py:79-80` (`MARK_WIDTH = 3`, `MARK_TOLERANCE_SECONDS = 30`),
used at `playback.py:379-392`.

*Measured:*

| bar width | 1 px is | mark drawn | actually clickable |
|---|---|---|---|
| 1200 px | 72 s | 3 px | **0.83 px** |
| 1900 px | 45 s | 3 px | 1.32 px |
| 2540 px | 34 s | 3 px | 1.76 px |

*Why it costs him:* he aims at the red line he can see, misses by one pixel,
and is silently given plain time — footage from up to a minute away, with a
status line that reads as though it worked. The comment above the constant
explains, correctly, why the tolerance stopped being measured in pixels. The
correction went one step too far: the tolerance must be a duration *and* at
least as wide as what is drawn.

*What I would do:* `tolerance = max(MARK_TOLERANCE_SECONDS, seconds_per_pixel *
(MARK_WIDTH + 1))`. `click_at` already receives `width`, so the arithmetic is
local. One line, one test.

---

**5. Nothing tells him a save was noticed, and Save is below the fold.** — S

*Where:* `settings_tab.py:838-847` and `settings_tab.py:1143-1147`. The form is
~1600 px tall at 1920×1080; the Save button is at the very bottom, after the
troubleshooting panel. Success is the word `Saved.` rendered in `PALETTE["muted"]`
at 13 px.

*Why it costs him:* he presses a button at the bottom of a long page and the
only acknowledgement is a grey word next to his hand, in the colour the palette
reserves for "this does not matter". He is the person who has to be sure the
camera password he just corrected actually took. The failure sentence beside it
is drawn in `warn` amber — so failure is louder than success, which is right —
but success is quieter than nothing.

*What I would do:*
- Keep the Save button where it is, but **pin the message-plus-button row to the
  bottom of the tab** (outside the scroll area) so it is always reachable. The
  scroll area already exists at `settings_tab.py:647-654`; moving the `ending`
  layout out of `column` and into `outer` is a small change.
- Draw `Saved.` in `PALETTE["ok"]` with the `●` glyph, and hold it for a few
  seconds before fading to nothing — a state that never clears is furniture.
- Add `Ctrl+S`. There is not one `QShortcut` or `setShortcut` in the whole
  codebase.

---

**6. `Remove` deletes a camera view with no confirmation and no undo; lowering
the budget deletes footage with no warning at all.** — S each

*Where:* `settings_tab.py:231` (`Remove`), `settings_tab.py:783-785` (`Budget
(GB)`), enforced by `storage/retention.py:57-64`.

*Why it costs him:* `Remove` sits in the row he is editing, immediately right of
the reader combo, and takes the stream's sky line and every ignored patch with
it. That is minutes of careful work on a picture, gone to one misplaced click,
recoverable only by not pressing Save — which he will not know.

The budget is worse and quieter. Typing `10` where `100` was meant means
retention deletes ~90 GB of footage on the next pass, permanently, with no
question asked and no line anywhere saying it is about to happen. This is the
only irreversible destructive action in the interface and it looks like a text
field.

*What I would do:*
- `Remove` → *"Remove"* stays, but on click the row collapses to a single line —
  *"thermal removed. **Undo**"* — until Save. No dialog; an undo he can see for
  as long as it matters.
- On Save, if the new budget is lower than the old one **and** the recordings
  folder already holds more than the new budget, put one sentence in the message
  line before writing: *"This will delete about 90 GB of the oldest footage.
  Save again to go ahead."* Second press writes. No modal — a modal on a console
  is a thing that can be dismissed by a stray keypress; a two-press confirm
  cannot.

---

**7. Words that name the machinery instead of the effect.** — S (each is a
string change)

Rendered and confirmed:

| Where | Says | Should say |
|---|---|---|
| `settings_tab.py:1384-1390` | `storage.budget_gb: Input should be a valid number, unable to parse string as a number` | *"Budget must be a number of gigabytes, like 100."* Map the pydantic path to the field's own on-screen label; fall back to the label alone. |
| `streaming/go2rtc.py:558` → band | `go2rtc is not installed - run install.bat` | *"Part of VMD is missing, so there are no pictures. Reinstall VMD."* He cannot run a batch file. |
| `playback.py:347-350` | `2026-08-11: 135 segments, 11h 15m recorded.` | *"11h 15m recorded on 2026-08-11."* He does not have a word for a segment and does not need one. |
| `settings_tab.py:763-773` | `Must travel at least (dots)` | This asks for a number he cannot see anywhere. Either move it behind the picture (where dots are visible) or delete the field — its own tooltip says *"Leave it empty"*, which is a field admitting it should not be on the page. |
| `settings_tab.py:694` and `:798` | two boxes, **Camera** and **The camera** | Rename the second **If the picture will not come up** — it is a troubleshooting panel, and saying so also explains why it is there. |
| `disk.py:347-352` | *"Roughly 1 minutes left"* | `1 minute`. Plural bug, visible in the state that matters most. |

The banned-word test (`test_desktop_settings_tab.py:156`) is doing real work and
the Settings tab is clean because of it. The leaks are all in the surfaces the
test does not cover — see finding 2.

---

**8. `Delete the selected patch` is clipped to `elete the selected patc`.** — S

*Where:* `settings_tab.py:392`, in the stream row's details panel.

*Measured at 1600 px* (the form column is capped at `FORM_MAX_WIDTH`, so this
does not improve on a bigger screen): the button is given 157 px for text
needing 147 px plus padding, and Qt clips both ends. Both `Add this patch`
(110/84) and the two adjacent buttons fit; only this one does not.

*What I would do:* `Delete patch`, or give the row a stretch that lets it size to
its hint. The code comment at `settings_tab.py:288-297` describes fixing exactly
this failure one row above; it just did not reach this row.

---

**9. The Logs tab does not say which filter is on.** — S

*Where:* `logs.py:178-188`. `All` and `Warnings and errors` are plain
`QPushButton`s — not checkable, not in a group, no styling. Nothing on screen
distinguishes them.

*Why it costs him:* Logs is where he goes when something is already wrong. He
presses *Warnings and errors*, sees three lines, and cannot tell whether that is
all the trouble there is or all the trouble that passed the filter. Half an hour
later he cannot remember which he left it on.

*What I would do:* reuse `ViewChooser._draw` from `live.py:340-357` — same
segmented look, same amber underline the tab bar uses. The vocabulary for "this
is where you are" already exists in this codebase and is good; this control just
does not speak it.

---

**10. Three views are not the same size, and there is no way to even them.** — S

*Measured:* with three streams all playing, the panes come out 446 / 446 / 383
px. With mixed states the split was far worse (roughly 200 / 350 / 740). The
splitter is populated by `addWidget` in `live.py:999` and never given sizes, so
the widths fall out of size hints and insertion order.

*Why it costs him:* he did not ask for the third camera to be smaller, and he
cannot tell whether he dragged it there. It reads as something being broken.

*What I would do:* after `_apply_view(force=True)`, call
`self._wall.setSizes([1] * count)` so equal is the starting point. His own drags
still stick for the session. Optionally a double-click on a splitter handle
resets to equal — Qt gives that for free with `setChildrenCollapsible(False)`
plus a handler.

---

**11. The steering panel contradicts itself when the camera does not answer.** — S

*Rendered:* after one arrow key with an unresponsive camera, the panel reads

```
  pan −0.50  tilt +0.00  zoom +0.00
  the camera did not answer
```

The first line is the console's own request, in the bright ink reserved for
readings; the second is the truth, in amber, underneath. He reads the top line
first because it is brighter and bigger.

*What I would do:* when `UNANSWERED_NOTE` is showing, dim the velocity line to
`muted` — it is a request, not a reading, and should stop looking like one.
`live.py:1262-1279`, three lines.

---

**12. Small kindnesses, in descending value.** — S each

- **Remember the window size and position, and open maximised the first time.**
  `app.py:207` calls `window.show()`. On a dedicated console that never sleeps,
  making him maximise the window every morning is the first thing he does and
  the first thing that says "unfinished".
- **A `Browse…` button beside the Storage folder** (`settings_tab.py:783`).
  `QFileDialog` is already imported and used for *Save a report*. Today the
  field shows the *middle* of a long path, right-truncated, and he must type it.
- **Feed `Find the right path` back into the field.** When it finds working
  addresses, offer them as buttons — *"Use rtsp://…/ch2 (0.9 Mb/s)"* — that
  write straight into the stream row. `settings_tab.py:1157-1162`,
  `diagnose.py:193-200`. This is the biggest single reduction in typing in the
  app.
- **Make `Find the right path` work with no stream configured.**
  `diagnose.py:163-164` refuses without one. It has the camera address, the
  username and the password — everything it needs. Refusing on first run is
  refusing at the only moment it is genuinely needed.
- **A one-line legend under the Playback bar:** `green = recorded · red =
  something moved · amber = where you are`. Three colours carry the whole tab
  and none of them is named anywhere.
- **Show the time under the pointer on the Playback bar** as he moves across it.
  One pixel is over a minute; he is aiming blind.
- **Draw movement marks so they stop at the hour rules** rather than over them
  (`playback.py:150-154` paints `0..height`). On a busy day the red bars cross
  the hour numerals and he cannot read the times off the bar he is aiming at.
- **The empty patch list in the picker** (`picker.py:791`) is a black rectangle.
  Everywhere else in this codebase an empty list says so in words — *"Nothing is
  being ignored yet."* — and this one should too. Same for `Take the sky line
  off` (`picker.py:788`), which is enabled while there is no sky line.
- **Hold the alarm strip's count.** `_raise_alarm` (`live.py:847-851`) overwrites
  the text, so three events in a minute look like one. *"Movement on thermal at
  14:46:50 (3 in the last two minutes)"* costs one integer.
- **Storage predictions before anything has been recorded.** On first run the
  panel promises *"Roughly 6 days left before the drive is full"* from an
  estimated rate with no footage behind it. While `used_bytes == 0`, say
  *"Nothing recorded yet."* and keep the panel's credibility for the day it
  matters.

---

## Do not do this

Every reviewer proposes more information. Most of it is noise on a screen
somebody stares at for months.

1. **Do not put the signal figure in the healthy `link` chip.** It is the same
   news every four seconds. The chip is a glance; the panel one tab away already
   carries the number with its meaning beside it. The current split is right.
2. **Do not colour the whole band green when everything is fine.** The comment
   at `window.py:241-245` is correct and hard-won: four green sentences is a
   wall of colour that says nothing, and it destroys the one red one.
3. **Do not mask the password fields.** `settings_tab.py:948-950` explains why —
   an offline, physically-controlled machine whose real failure is a typo nobody
   can see. Masking would look like security and cost him an hour.
4. **Do not make the alarm modal, or add a dialog to it.** A modal over the
   pictures is a modal over the pictures at the exact moment they matter, and it
   can be dismissed by a stray keypress from someone steering the camera.
5. **Do not auto-switch the wall to the stream that alarmed.** He may be
   deliberately watching the other one. Outlining the pane is the right amount of
   pull. Offer the switch on the strip; do not take it.
6. **Do not add a confidence percentage to the alarm strip.** The movement is
   confirmed; the naming is a bonus. A number there invites him to disbelieve a
   real event because it scored 41%.
7. **Do not add tooltips as the fix for an unclear label.** The tooltips in
   `settings_tab.py` are good and are already doing more work than tooltips
   should. A control whose name only makes sense on hover is a control with the
   wrong name.
8. **Do not add a second progress bar, throughput graph, or history sparkline to
   the Live column.** It is already the busiest part of the screen and it is
   beside the pictures.
9. **Do not add a confirmation to `Acknowledge`.** It is reversible in the only
   way that matters — the event is still in the list and still on the timeline.
10. **Do not overlay the steering keys on the picture.** They belong in the
    column, read once. If anything, they should get *quieter* over time, not
    more prominent.
11. **Do not restructure the four tabs.** The structure is right and it is what
    he asked for. Everything above is a change inside it.

---

## What is genuinely good

Worth saying plainly, because most of this document is complaints.

- **The alarm is excellent.** Red strip below the pictures — not over them, not
  pushing them down — plus a red outline on the pane that saw it, plus a row in
  the list. The glyph is there as well as the colour. It reads from across a
  room and it does not cover the thing it is about.
- **The status band is the right idea, right size, right restraint.** Quiet
  chips carry only their own name; the one with a fault takes the room and the
  box. `● recording` at 16 px is exactly what an operator two metres back needs.
- **The recording dot.** A pulsing circle that dims rather than disappears, and
  a still square when it is not recording, so the two states differ by movement
  and not only by colour. The reasoning in `window.py:79-93` is the best comment
  in the codebase and the result is correct on screen.
- **The frame picker is the model for the rest of the app.** One instruction at
  the top, a big picture, direct manipulation, a primary *Use what I drew*, and
  when the camera cannot be reached: *"There is no picture, so the boxes in the
  settings are the way to set these. Nothing you had is changed."* That sentence
  is the standard every other failure state should be held to.
- **The storage panel is honest and useful.** Two limits kept apart, thresholds
  that actually change the colour, and — uniquely in this app — a failure line
  that ends with what to do: *"…so lower the budget or free space on this drive."*
- **The empty states.** *"Nothing has moved yet."*, *"Nothing has been recorded
  yet."*, *"Nothing has been logged yet."*, *"Press one of the buttons above and
  what the camera says appears here."* Someone went through and made sure no
  black rectangle is ever ambiguous. That discipline is why finding 9's empty
  patch list stands out as an exception rather than a pattern.
- **The instrument labels on the panes.** `thermal — playing` in green,
  `visible — late – no new pictures` in amber, on the picture they describe
  rather than in a list across the room. Right place, right vocabulary.
- **`late` is reported and not acted on.** A console that leaves a late stream
  alone, on purpose, with the reason written down — that is a system that has
  been in the field.
- **The form column stops growing.** `FORM_MAX_WIDTH` is why Settings is
  readable at 3840 px instead of putting each label at the opposite end of the
  screen from its field.
- **Nothing refuses to open.** A tab that will not build becomes a label saying
  why, and the other three still work. On a machine with no terminal that is not
  a nicety, it is the difference between a fixable installation and a brick.
