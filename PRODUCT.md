# Product

## Register

product

## Users

**One person, and he is not an engineer.** This was written as "trained operators,
rotating, every one of them briefed before using it". That is not what happened. One man
owns this system, uses it, and is the only person who ever will. He has **no terminal, no
second machine, and nobody standing behind him.** He is a fast, daily user of the screens
he watches and a first-time reader of every control he has to set.

The two halves of that pull in opposite directions and both are real:

- **What he reads all day may be dense.** The status band, the video panes, the link and
  storage panels, the movement list — abbreviations, compact numerics, keyboard steering,
  status encoded in a glance. He is in front of these for months; they should be fast, not
  chatty.
- **What he has to decide must explain itself on the page.** He read four of the Settings
  tab's own labels back as questions — *"'Name what moved' — what is that?"*, *"what is the
  difference between auto and ffmpeg?"* — and one as an answer: *"'Use this view' is
  useless, of course use that view, if it's added."* Every one of those was a label we
  wrote. **A control he cannot parse is not a control.** A tooltip is not the fix; a
  control whose name only makes sense on hover has the wrong name.

Anything that only a terminal, a second screen, or a phone call to the author can resolve
is a dead end, not a fallback. Sentences that name a destination must go there.

What none of this solves is physics. Glare, viewing distance and colour-blindness are
unaffected by familiarity, so contrast and non-colour-coded status remain hard
requirements.

The screen lives on a **normal desk under office or daylight conditions**, not in a
darkened control room. Anything designed for a dark room — low-contrast greys, thin type,
dim status colours — becomes unreadable here. Contrast has to survive a window.

Their job: know immediately that something is moving in a monitored area roughly 700 m
away, see enough to judge whether it matters, and be able to find it again later.

## Product Purpose

A surveillance console for a multi-spectral PTZ camera watching a distant perimeter. It
shows live video, records continuously, raises an alarm when something moves, and lets the
operator look back through what was recorded.

One console watches one camera. A site with more than one camera runs more than one
console — `cameras.bat` sets each one up with its own settings, its own recordings and its
own screen, out of the same installed folder. Everything below describes a single console,
because that is what an operator sits in front of.

The system deliberately does not care *what* moved. A person, a dog, a vehicle — all are
worth knowing about. What it must not do is cry wolf at wind in trees, rain, or birds.

Success is an operator who trusts it: every alarm is worth looking at, and nothing real
gets past it.

## Brand Personality

**Instrument, not interface.** It should feel like equipment — something built to be
depended on, not something designed to impress. Three words: *precise, sober, legible.*

The tone of every label and message is plain and factual. It states what is true, states
what it does not know, and never dresses either up. "LINK LOST — last frame 14 s ago" is
the voice. "Oops! Something went wrong" is not.

## Anti-references

The brief is "military tactical", and that phrase pulls hard toward exactly the wrong
things. Specifically avoid:

- **Cyberpunk HUD cosplay** — neon cyan on black, glowing borders, scanlines, CRT flicker,
  hexagonal frames, animated radar sweeps, targeting reticles that aren't targeting
  anything. This is film-prop language, and it makes a real tool look like a toy.
- **Call-of-Duty chrome** — stencil fonts, camouflage, chevrons, aggressive angular cuts.
- **Generic SaaS dashboard** — rounded cards on a light grey field, pastel donut charts,
  a metric tile row across the top, an illustrated empty state.
- **Decorative density** — data that is on screen to look busy rather than to be read.
  If a number has no decision attached to it, it should not be there.

The real references are the boring ones: professional VMS software, aviation and marine
instruments, broadcast equipment. Things whose seriousness comes from restraint.

## Design Principles

1. **The video is the product.** Every pixel of chrome competes with the thing the
   operator actually needs to see. Surround the video with the quietest surface that still
   reads in daylight, and put nothing beside it that does not earn its place.

2. **Dense where it is watched, plain where it is set.** On the screens he reads all day,
   abbreviations and compact numerics are fair game — density is a feature there. On the
   screens where he makes a decision, every control says what it does on the page, in
   words he used himself. The screen sits in daylight either way, so contrast is
   non-negotiable and status must never depend on colour alone.

3. **Never imply certainty the system does not have.** If the link is down, say so and say
   how long. If the detector cannot classify a 13-pixel blob, say "movement", not "person".
   Confidence is displayed, never implied.

4. **Nothing decorative.** No element exists to look technical. Every number answers a
   question someone actually asks; every colour encodes a state; every animation reflects a
   real change. Anything else is removed.

5. **Failure is a first-class state.** Link loss, disk pressure, a stalled stream and a
   dead camera are normal, expected conditions on this deployment, not errors. They get
   designed presentation, not a red toast.

## Accessibility & Inclusion

- **WCAG 2.2 AA minimum**, and higher for status text: this is read under daylight glare
  and sometimes from a distance. Body text ≥ 4.5:1; status and alarm text treated as
  critical and pushed well past it.
- **Never colour alone.** Every state carries a shape, an icon, or a word as well as a
  colour. Red/green status pairs are the most common colour-blindness failure and this
  interface is full of them.
- **Reduced motion — not honoured, and here is why.** `prefers-reduced-motion` is a CSS
  media query and this is a Qt application; nothing reads it. What was written here as a
  promise is instead a constraint on what may move at all: an arriving alarm is an instant
  state change and never a flash, and the only three things in the console that animate
  are the recording dot, a meter's fill, and hover feedback (see `DESIGN.md` → Motion).
  If the setting is ever honoured, the dot and the fill must become instant rather than
  still — a dot that stops pulsing reads as a recorder that stopped.
- **Keyboard reachable.** The owner wants speed; steering, layout switching and
  acknowledging an alarm are all reachable without a mouse.
