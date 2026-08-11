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
- Side column: a fifth of the window, floored at 330px and capped at 420. A
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

Minimal and functional. The only thing worth animating is a state change.

- Transitions: `0.16s cubic-bezier(0.22, 1, 0.36, 1)` on hover and active states only.
- No entrance animations, no staggered reveals, no scroll effects. Nothing in an
  operations console should ever move because it just appeared.
- An arriving alarm changes state instantly — outline, strip, sound. It does not fade in.
- `prefers-reduced-motion` collapses all transitions to ~0. Because motion here is only
  ever hover feedback, nothing is lost.

## Anti-patterns for this system

Rejected explicitly, since "tactical" invites all of them: neon on black, glowing borders,
scanlines or CRT effects, hexagonal frames, animated radar sweeps, targeting reticles,
stencil or military display faces, camouflage, chevrons, gradient text, glass blur, rounded
cards on grey, pastel charts, and any number on screen that no one makes a decision from.
