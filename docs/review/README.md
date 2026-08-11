# The backlog

Four reviews, written on 2026-08-11 after the system's first real deployment.
They are a **backlog, not a plan**. Nothing in them is required for the system to
work; everything in them is something a careful engineer noticed and wrote down
so it would not be lost.

Read the one that matches what you are about to touch. Each is self-contained,
and each now opens with a dated section saying which of its findings have since
been closed and by which commit. **No finding has been deleted or rewritten.** A
review whose fixed items quietly disappear cannot be audited, and "this was true
on 11 August and is not true now" is the sentence worth having.

| File | What it covers |
|---|---|
| [architecture.md](architecture.md) | Module boundaries, duplication, coupling, dead code, the threading model |
| [risks.md](risks.md) | Latent failures — things believed on weak evidence, months of uptime, the hand-set clock, resource lifetimes |
| [tests.md](tests.md) | What the suite is blind to, and which tests cannot fail |
| [experience.md](experience.md) | Using the console — first run, failure states, wording, the number of steps between an alarm and the footage |

## 2026-08-12 — a second wave, since the day itself

A day after these were written, most of the rest of the "worth doing" list was
done. Each document's own dated section has the detail and the commits; in
summary:

- **Architecture** — findings 1, 3 and 4 closed: the console no longer drags
  OpenCV in through `vmd/detect/__init__.py`, the heartbeat no longer touches
  the recordings folder, and Save no longer runs `taskkill` on the GUI thread.
  Finding 2 substantially addressed. Findings 5 and 7–11 untouched.
- **Risks** — §§1–6 and two of §14 closed, including the frozen recorder
  endpoint, the clock in both directions, the sqlite dead end, go2rtc's missing
  give-up rule, and the ONVIF write that was never checked. §§7–13 stand.
- **Tests** — 4.1, 4.3, 4.5, 4.7, 4.12 and 4.13 closed; the suite now has a
  `conftest.py`, a 30-second timeout on every test, and a guard that fails any
  test reaching off loopback. 4.2 (the detector→console alarm across a process
  boundary) is the one worth doing next and is still open.
- **Experience** — findings 1 (partly), 2, 3, 4, 7, 8 and 12 closed. Playback
  gained everything finding 3 asked for and more.

And a good deal that none of these four documents has ever been read against: a
fullscreen mode, a zoom bar per lens with the ONVIF profile matching behind it,
clip export, and an automatic bitrate loop that writes to the camera unattended.
Where a document reasons about "only while the operator is pressing a button",
check that assumption before believing it.

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
