# Product

## Register

product

## Users

**Trained operators, rotating.** The owner built the system. Others take shifts on it, and
**every one of them is briefed on the software before using it.** Nobody meets this screen
cold.

That permits real density: abbreviations, compact numerics, keyboard shortcuts, and status
encoded in a glance rather than spelled out in sentences. The design does not need to teach
— it needs to be fast to read for someone who already knows what the fields mean.

What training does **not** solve is physics. Glare, viewing distance and colour-blindness
are unaffected by a briefing, so contrast and non-colour-coded status remain hard
requirements.

The screen lives on a **normal desk under office or daylight conditions**, not in a
darkened control room. Anything designed for a dark room — low-contrast greys, thin type,
dim status colours — becomes unreadable here. Contrast has to survive a window.

Their job: know immediately that something is moving in a monitored area roughly 700 m
away, see enough to judge whether it matters, and be able to find it again later.

## Product Purpose

A surveillance console for one multi-spectral PTZ camera watching a distant perimeter. It
shows live video, records continuously, raises an alarm when something moves, and lets the
operator look back through what was recorded.

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

2. **Dense, but legible under glare.** Operators are briefed, so abbreviations and compact
   numerics are fair game — density is a feature, not a risk. But the screen sits in
   daylight, so contrast is non-negotiable and status must never depend on colour alone.
   Fast to read for someone who knows the system; still readable when the sun is out.

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
- **Reduced motion respected.** The only motion that matters is an alarm arriving; under
  `prefers-reduced-motion` it becomes an instant state change rather than a flash.
- **Keyboard reachable.** The owner wants speed; steering, layout switching and
  acknowledging an alarm are all reachable without a mouse.
