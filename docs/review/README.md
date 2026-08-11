# The backlog

Four reviews, written on 2026-08-11 after the system's first real deployment.
They are a **backlog, not a plan**. Nothing in them is required for the system to
work; everything in them is something a careful engineer noticed and wrote down
so it would not be lost.

Read the one that matches what you are about to touch. Each is self-contained.

| File | What it covers |
|---|---|
| [architecture.md](architecture.md) | Module boundaries, duplication, coupling, dead code, the threading model |
| [risks.md](risks.md) | Latent failures — things believed on weak evidence, months of uptime, the hand-set clock, resource lifetimes |
| [tests.md](tests.md) | What the suite is blind to, and which tests cannot fail |
| [experience.md](experience.md) | Using the console — first run, failure states, wording, the number of steps between an alarm and the footage |

## What was already acted on

These four documents were written, and then the items that were **live harms
rather than improvements** were fixed the same day. So parts of each document
describe defects that no longer exist. Where that matters the document usually
says so; where it does not, check the code before believing it.

Fixed on the day, from these reviews:

- The recorder chose where to read from once, at start, and could never change
  its mind — so after every reboot it read the camera directly and crossed the
  radio link twice, for months, silently.
- A clock jump could delete the whole archive, or silently overwrite footage.
- One sqlite error stopped indexing *and* retention for the life of the process
  while the console still reported "recording", ending in a full disk on a
  machine that looked healthy.
- `go2rtc` had no give-up rule, so a broken one emptied the 500-line log buffer
  in about seventeen minutes — taking every other subsystem's careful reporting
  with it.
- The detector's fallback to the camera was sticky on success, so one restart
  put a second copy of a stream on the link permanently.
- Importing anything from `vmd.detect` dragged OpenCV into the console; on a
  machine without the detect extra that silently cost the movement list and
  every timeline mark.
- "Recording" could still be a lie: a backwards clock step made a dead recorder
  look alive, a 1-byte file counted as footage, and a console whose disk had
  never been polled reported healthy against a drive that did not exist.
- The password-redaction test could not fail, and a password containing a quote,
  a backslash or a non-ASCII character survived redaction into the report meant
  to be emailed.
- A radio that had refused the login looked identical to one still being checked.
- The movement marks on the timeline were 0.83 pixels wide to click.
- Lowering the disk budget deleted footage with no warning at all.
- Save froze the window for tens of seconds; three things read the recordings
  folder on the two-second heartbeat.
- Nothing stopped a test hanging, and 40% of every test run was spent waiting
  out TCP timeouts to a black-hole address.

## What is deliberately still here

The rest. In particular, `experience.md` ends with a **"do not do this"** list —
changes that would look like improvements and would make the console worse for a
non-technical operator watching a perimeter. That list is the most valuable page
in this directory and the easiest to ignore.

Two things worth pulling out, because they are cheap and were argued for twice:

- **The console barely navigates the operator anywhere.** Taking him from an
  alarm to the footage was the single largest saving found in any of the four
  reviews.
- **`_write_json_atomically` now exists in three places** (`vmd/settings.py`,
  `vmd/detect_main.py`, `vmd/record_main.py`). Each copy has its reason written
  down, and it still wants one home.

## One honest warning

Three of these four documents were written by agents that could not reach the
real camera, the real radio, or the deployment laptop. Where a document reasons
about the field it says so. Measurements taken on a development machine are
labelled as such. Believe the code and the hardware over the documents.
