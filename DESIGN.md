# Design

The visual system for the VMD console. Chosen from three candidates by looking at them
side by side: **Console** — graphite with visible panel structure and amber instrument
accents. It reads as equipment rather than as software, and holds up in daylight.

Strategy: **restrained**. Tinted neutrals carry the whole surface; colour appears only to
encode state. There is no decorative colour anywhere in this system.

## Theme

Dark, and deliberately so — but not for atmosphere. The operator looks at night video for
long stretches, and a bright surround makes dark footage read worse and causes glare
against the screen. The chrome is dark so the footage is legible.

The counterweight is that this screen sits in **office daylight**, not a dark control room.
So the darkness is mid-graphite rather than black, and every contrast ratio is checked
against a daylight threshold rather than a dim-room one. Nothing in this system relies on
the room being dark.

## Color

OKLCH throughout. Neutrals are tinted 0.006–0.010 chroma toward blue (hue 265) — the same
cool cast as the video wells, so chrome and footage feel like one instrument rather than a
website wrapped around a video.

### Surfaces

| Token | Value | Use |
|---|---|---|
| `--bg` | `oklch(0.23 0.006 265)` | Page field behind panels |
| `--surface` | `oklch(0.28 0.007 265)` | Panels, bars, the side column |
| `--raised` | `oklch(0.33 0.008 265)` | Controls, selected states, chips |
| `--well` | `oklch(0.12 0.005 265)` | Video areas only. Nothing else is this dark |

### Lines

| Token | Value | Use |
|---|---|---|
| `--line` | `oklch(0.40 0.010 265)` | Default borders, row rules |
| `--line-strong` | `oklch(0.52 0.012 265)` | Boundaries that must not be missed, e.g. between two video panes |

### Text

| Token | Value | Contrast on `--surface` | Use |
|---|---|---|---|
| `--ink` | `oklch(0.97 0.003 265)` | 13.4:1 | Values, headings, anything read at a glance |
| `--muted` | `oklch(0.78 0.010 265)` | 7.3:1 | Labels, units, secondary text |

`--muted` is deliberately far brighter than a typical secondary grey. Light grey secondary
text is the single most common legibility failure in dark interfaces, and this screen is
read under glare. 7.3:1 is the floor, not a target.

### State

Colour never carries meaning alone. Every state also has a glyph and a word.

| Token | Value | Meaning | Glyph |
|---|---|---|---|
| `--ok` | `oklch(0.80 0.15 150)` | Healthy, recording, linked | `●` |
| `--warn` | `oklch(0.84 0.14 75)` | Degraded, approaching a limit | `▲` |
| `--alarm` | `oklch(0.68 0.21 27)` | Movement detected; link lost | filled bar + word |
| `--accent` | `oklch(0.82 0.13 82)` | Amber. Interactive emphasis, focus, active control | — |

**Amber discipline.** Amber is what makes this direction feel like an instrument, and it is
also the fastest way to make it look like a film prop. It is allowed on: focus rings,
active control states, slider tracks. It is **not** allowed on: text, borders at rest,
icons, backgrounds, or anything decorative. If amber is spreading, the design is drifting.

## Typography

System faces only. The machine is offline and cannot load fonts — and this is not a
compromise: system UI faces are what real instrument software uses, and a monospace numeric
face is correct for telemetry regardless.

```css
--ui:   system-ui, "Segoe UI", Roboto, sans-serif;
--mono: ui-monospace, "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace;
```

One family in multiple weights, plus monospace for data. No paired display face.

| Role | Size | Weight | Face |
|---|---|---|---|
| System state band | 16px | 600 | UI |
| Site / camera name, tab labels | 14px | 600 | UI |
| Body / labels | 13px | 400 | UI |
| Notes and captions | 12px | 400 | UI |
| Section heading, table headers | 11px | 600 | UI |
| **All numerics** | 11–13px | 400–600 | **Mono, `tabular-nums`** |
| Video overlay | 11px | 600 | Mono |

The sizes live in `vmd/desktop/style.py` as `SIZE_BAND`, `SIZE_TITLE`,
`SIZE_BODY`, `SIZE_SMALL` and `SIZE_HEADING`, and nothing types a number at the
point of use. They are logical pixels, and the case that sets them is Windows
display scaling: a 1920×1080 laptop panel at 150% reports 1280×720 logical
pixels to Qt, so one logical pixel is 1.5 real ones on the console's own screen
and `SIZE_BAND` lands at 24 real pixels — which is what an operator two metres
back has to read.

**The section heading is not tracked or uppercased in the Qt console.** Qt
stylesheets support neither `letter-spacing` nor `text-transform`, and the only
ways to get them — uppercasing the strings, or a `QFont` on the group box, which
every child then inherits — either change what the code says or leak into the
body text. The heading is 11px/600 in `--muted` against 13px/400 body, which
restores the hierarchy without either.

**Every number is monospace with tabular figures.** Timestamps, bitrates, pixel counts and
disk figures change constantly; proportional digits make them jitter, which reads as
instability on a screen someone is monitoring.

Uppercase tracked labels are used **only** for section headings in the side column and for
sensor tags on video. They are instrument labelling, not decorative eyebrows — if they
start appearing above every block, that is the drift to catch.

## Geometry & Spacing

- **Radius: 0.** Square corners throughout. This is the strongest single signal separating
  "equipment" from "web app", and it costs nothing.
- Panel edges get `inset 0 1px 0 oklch(1 0 0 / 0.05)` — a one-pixel top highlight that
  reads as a physical bevel without becoming a gradient.
- Spacing scale: 2 / 4 / 6 / 9 / 12 / 18 / 22px. Tight by web standards, correct for a
  briefed operator reading dense telemetry. Named in `style.py` as `SPACE_HAIR`
  … `SPACE_WIDE`. The rhythm is **wide between groups, tight inside them**: what
  separates two settings is the panel they are on, not the distance between them.
- Side column: 0.22 of the window, floored at 330px and capped at 420. A
  fixed width is the same paragraph wrapped to four lines on a 1366 laptop panel
  and on a 4K screen with a third of the width wasted beside it. The floor is
  what the movement list needs before its columns start eliding values. Video
  takes everything else.
- Forms stop at 980px (`FORM_MAX_WIDTH`) and are centred. A thirteen-character
  address field stretched across a 4K panel puts the label at one end of the
  screen and the box it belongs to at the other.

## Components

**Status band** — the first thing on the screen, above the tabs, because it is
true of the machine rather than of whichever page is open. One chip per part of
the system's health: recording, streaming, detection, link. Read at 16px from two
metres, which is the whole reason it exists — it was eleven pixels of grey in a
footer, the least prominent thing on screen and the most important.

**One line, and 16px of it.** What earns the band its place is the type size, not
the padding: this is a screen whose purpose is showing video, and every pixel the
band takes it takes from the pictures. Chips are as wide as what they are saying
and the room to their right is left empty.

**Status chip** — glyph + words. Healthy is the *name of the part* and nothing
else — `recording`, `streaming`, `detection`, `link` — drawn in `--ink` on no
panel, with no border, and only the glyph in green: `streaming: streaming` is the
same news every four seconds, and four green sentences across the top is a wall
of colour that says nothing. Anything that is not healthy — a fault, or a reading
nobody could take — carries the whole sentence, on `--surface`, inside a border
in the state colour. So the one chip worth reading is the only one drawn as a
box, and it is seen from across the room. The border is there when quiet too, in
`transparent`: the reflow this band accepts is a sentence getting longer, which
is meaning, not an outline appearing, which is decoration. The figure behind a
healthy reading — `-63 dBm` — is one tab away in the Live tab's link panel, with
what it means beside it.

**Recording dot** — a circle in `--alarm` that pulses at 900 ms while footage is
reaching the disk, and a still bar in the same colour when it is not. What
separates the two is the movement, not the colour, so "not recording" cannot be
mistaken for a glance that landed on the dim beat. It dims rather than going out,
because a dot that vanishes is indistinguishable from no dot at all for as long
as it is away. It follows whether anything was WRITTEN, never whether a process
is alive.

**Data row** — label left in `--muted`, value right in mono `--ink`, separated by a
one-pixel rule. The workhorse of the side column. Not a card.

**Headline word** — one word at 16px/600 in the state colour, with the state
glyph beside it and one short line under it. Used at the top of the Link panel:
`GOOD` / `FAIR` / `BUSY` / `FULL` / `WEAK` / `NO LINK`. It exists because the
panel had grown to fourteen true sentences, and a paragraph is not something the
person in front of this screen will read to find out whether the picture is
about to break up. Colour never carries it alone — the word *is* the state.

**Meter** — a bar 8px tall with its name at 11px on the left, the reading in
mono on the right, and **the thresholds marked as hairlines on the track**. The
marks are the point: `-66 dBm` means nothing without the scale it sits on, and a
mark where the reading changes meaning says "past it" with a shape rather than
with a colour. Painted rather than assembled out of widgets — three pieces of
text and a rectangle do not need four `QLabel`s on a panel redrawn every two
seconds. See Motion for the fill.

**Behind `Details`** — anything the panel knows and does not need to say. A
disclosure that starts shut and stays shut across restarts. Nothing is deleted
to make a panel shorter: the sentences behind it are produced whether it is open
or not, and they are decided by the same thresholds as the word above them, so
the two views can never disagree about the same reading.

**Zoom bar** — under each picture, in that picture's own frame: `−`, a slider,
`+`, and a mono caption on the right. One per lens, because the camera is two
sensors on a shared gimbal and a single zoom control is a command going to
whichever lens the camera happened to list first. Two rules it must keep. The
readout draws **what the camera reported, or nothing** — never a percentage
counted from how long a button was held, because that number is right until the
first command that does not arrive and looks right for ever afterwards. And
"nobody has asked yet" (`checking the lens`) is a different caption from "the
camera says it has none" (`zoom not reported`), set to the same width so the
slider does not change length as the camera answers; a warning the operator
meets every morning and that clears itself is a warning he learns to ignore.

**Video pane** — `--well` background, sensor tag top-left, telemetry readout bottom-left,
both on a translucent black plate. Panes are separated by `--line-strong`, never by a gap
alone, since both wells are near-black.

**Alarm strip** — a full-width bar **below** the video, never over it, plus a red outline on
the video itself. An alarm is when the picture matters most; covering it with the notice
about it is self-defeating.

**Segmented control** — flush buttons in a bordered group, active state on `--raised` with
weight 600 and a 2px `--accent` bar along its bottom edge, which is the same mark
the active tab carries. Used for choosing which view fills the video wall:
everything side by side, or one of them alone. The buttons are built from the
streams that exist, never from a fixed pair, because a camera names its views
whatever it likes and offering one that is not configured is offering a black
rectangle. Every button refuses focus: the tab is what steers the camera, and a
button that took the keyboard would leave the next arrow key going nowhere.

**Fullscreen** — a mode, not a window. It hides the status band, the tab bar and
the Live tab's side column, and asks the window it is already in to fill the
screen. Nothing is reparented and nothing is rebuilt, and that is the design
rather than a shortcut: the panes hand libVLC an HWND, and a picture moved into
a window of its own leaves libVLC drawing into a surface that belongs somewhere
else — a black rectangle with a frame counter still counting beside it. What the
mode keeps is the view chooser, the splitter share between the pictures, the
zoom bars, and the steering. The way out is a button that names its own key —
`Leave fullscreen  (Esc)` — in the same row as the chooser, plus `Esc` and `F11`.
An operator who cannot find his way out at three in the morning is a fault, not
a preference; he has no second machine.

**Empty state** — nothing on this console is a black rectangle with nothing in
it. A list with no rows, a report box nothing has written to, a wall with no
views configured: each says which of "nothing has happened" and "this failed to
load" it is, in one muted sentence where the content would be. The operator
cannot tell those two apart by looking at a hole, and on the movement list the
difference is whether anything has crossed the perimeter.

Cards are not part of this system. The side column is rows and groups; the main area is
video. Nothing is a card.

**Settings note — passwords are shown, not masked.** Every credential field renders as
plain text. This is deliberate. The machine is offline, physically controlled, and reachable
only from `127.0.0.1`, so masking defends against nothing here — while a wrong camera
password means no video and no recording, and a masked field makes that typo invisible
precisely when it matters. The threat this interface actually faces is a mistyped
credential, not a shoulder-surfer. If the console is ever exposed beyond the local machine,
this decision must be revisited along with everything else about its access model.

## Motion

Minimal and functional. The only thing worth animating is a state change, and
there are exactly three things in this console that move.

- Transitions: `0.16s cubic-bezier(0.22, 1, 0.36, 1)` on hover and active states only.
- **The recording dot**, at 900 ms. Documented under Components: what separates
  recording from not recording is the movement, not the colour.
- **A meter's fill travels**, 420 ms on `OutCubic` (`vmd/radio/meter.py`). This
  is the one place motion carries meaning rather than feedback: a figure that
  changes by being redrawn is indistinguishable from a figure that was always
  that, and the operator has already been taught once not to believe a screen
  that looks calm. A bar that slides says "this changed", and says it in the
  direction it changed. `OutCubic` and never a bounce — a bar that overshoots
  has, for a moment, shown a reading the radio never gave. The cost is bounded
  on purpose: it runs only when the value actually moves, it stops when it
  arrives, it repaints one 28px widget, and a change under half a percent is
  taken rather than travelled to, because the radio jitters between readings and
  a bar that never settles is noise dressed as information.
- No entrance animations, no staggered reveals, no scroll effects. Nothing in an
  operations console should ever move because it just appeared.
- An arriving alarm changes state instantly — outline, strip, sound. It does not fade in.
- `prefers-reduced-motion` collapses all transitions to ~0. The three above are
  Qt animations rather than CSS transitions and are not covered by it; if that
  setting is ever honoured here, the recording dot and the meter fill must both
  become instant state changes rather than stopping — a dot that stops pulsing
  reads as a recorder that stopped.

## Anti-patterns for this system

Rejected explicitly, since "tactical" invites all of them: neon on black, glowing borders,
scanlines or CRT effects, hexagonal frames, animated radar sweeps, targeting reticles,
stencil or military display faces, camouflage, chevrons, gradient text, glass blur, rounded
cards on grey, pastel charts, and any number on screen that no one makes a decision from.
