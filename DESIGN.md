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
| Site / camera name | 14px | 650 | UI |
| Section heading | 11px, `0.06em` tracking, uppercase | 600 | UI |
| Body / labels | 12.5px | 400 | UI |
| **All numerics** | 11.5–13px | 400–600 | **Mono, `tabular-nums`** |
| Video overlay | 11–11.5px | 400 | Mono |

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
  briefed operator reading dense telemetry.
- Side column: fixed 292px. Video takes everything else.

## Components

**Status chip** — bordered, `--raised`, glyph + label + monospace value. The border tints
toward the state colour when not healthy; the glyph carries the state; the word names it.

**Data row** — label left in `--muted`, value right in mono `--ink`, separated by a
one-pixel rule. The workhorse of the side column. Not a card.

**Video pane** — `--well` background, sensor tag top-left, telemetry readout bottom-left,
both on a translucent black plate. Panes are separated by `--line-strong`, never by a gap
alone, since both wells are near-black.

**Alarm strip** — a full-width bar **below** the video, never over it, plus a red outline on
the video itself. An alarm is when the picture matters most; covering it with the notice
about it is self-defeating.

**Segmented control** — flush buttons in a bordered group, active state on `--raised` with
weight 600. Used for layout and overlay switching.

Cards are not part of this system. The side column is rows and groups; the main area is
video. Nothing is a card.

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
