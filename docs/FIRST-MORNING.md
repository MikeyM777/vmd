# The first morning: running VMD against the real camera

Everything in this system has been tested against a synthetic camera on this
machine. **Nothing has been tested against the real camera or the real radio
link** — they are on your network, not this one. This page is the list of things
only you can check, in the order that finds problems fastest.

Work down it. Where something is wrong, the sentence to report back is written
next to it.

---

## Before you start

Open `VMD.exe`. It should open a window. If it does not, that is the first
finding and nothing below matters yet.

The window opens in about 15 seconds cold. That is libVLC rebuilding its plugin
index and it is normal. It is not hung. It opens where you left it last time,
on a screen that still exists — if you moved it, that is where it comes back.

**This laptop has no wifi and no internet, and nothing on it leaves it.** That
is a requirement of the deployment, not a preference: no part of this program
downloads anything, uploads anything, or expects a network beyond the radio link
to the camera. If anything on this list asks you to go and fetch something, it
is wrong and worth reporting.

---

## 1. Settings — type it in, save it, check it stuck

The tab is five boxes of settings, top to bottom — **Camera**, **Streams**,
**Movement detection**, **Storage**, **Radio** — and then **Check the camera**
at the bottom, which is tools rather than settings.

Camera address, username, password. Then one stream to begin with — press
**Add a stream** and give it a name and the RTSP address your camera uses
(`/ch2` on the FLIR). Each view is a card, and the second one you add sits
beside the first rather than under it, because they are two heads on one camera
and you will be setting them up against each other. There is no tick saying
whether to use a view. **Every view on the list is used** — shown, recorded, and
watched if you ask for that. The way to stop using one is **Remove**.

Press **Save**. It should say `Saved.`

Then open `settings.json` in Notepad and confirm it contains exactly what you
typed. If a field is missing or different, that is a bug and worth reporting
before anything else — settings that do not persist make every later result
meaningless.

**Passwords are shown, not masked.** That is deliberate and was asked for.

While you are here, under **Storage**: press **Look at this drive and suggest a size**. It reads the
drive the recordings folder is on — how big, how much is free, how much VMD is
already using — and fills in **How much space VMD may use (GB)** and an age rule
that fit it, with a report you can read line by line. Those are suggestions and
stay yours to change; the slider beside that box says what the number means in
the only unit anybody has an instinct for, which is how far back you can look.
**Nothing is written until you press Save**, and lowering it below what is
already on the disk warns you what it will delete and makes you press Save
twice.

---

## 2. Live — does a picture arrive, and does it stay

Watch the pane. On this machine, against a local source, the first frame arrives
in about **1 second**. Over your radio link it will be slower; note roughly how
long.

Then leave it alone for **ten minutes** and watch.

This is the single most important measurement of the morning. The whole desktop
rewrite exists because the browser console stuttered every second and dropped
streams where VLC held the same feed without trouble. A twelve-minute soak here
was flawless — but that was over loopback, which cannot reproduce a saturated
radio link. **Only your camera can answer this.**

Report: how long to first frame, and whether the picture dropped or stuttered in
ten minutes.

---

## 3. Steering — including the thing that used to break it

Arrow keys. Then two at once — it should move diagonally. Release one, it should
keep going on the other.

You can also **drag near an edge of the picture** to slew. There is no longer
any widget laid over the video — the pictures' own mouse events are read
instead. That change is why the video is a picture rather than a black rectangle
with `playing` written beside it, and it costs one thing worth knowing: the
splitter handle between two pictures, and the name plate above each one, are not
part of any picture, so a drag begun on those does not steer. A few pixels of
dead ground between the panes.

Now the important one: **steer continuously for a full minute**, then leave it
still for five.

The picture must not drop. On the old console, panning was what killed the
stream, and my own recovery timers made it worse. Every timer that did that has
been removed.

Then check the hazard case: hold an arrow key, and while still holding it,
switch to the Settings tab. **The camera must stop.** It used to keep slewing
until it hit its mechanical stop.

The camera is given **8 seconds** to answer a command. That is not generosity:
at 88% airtime the measured reply took 2.02 seconds, and the console logs the
slowest answer it has had so the number can be chosen from evidence rather than
guessed a third time.

---

## 4. The zoom — one slider per lens

Under each picture there is a zoom bar of its own: minus, a slider, plus, and a
readout of where the lens is.

This matters because the camera is **one gimbal carrying two lenses**, thermal
and visible, and until now every ONVIF command went to whichever media profile
the camera happened to list first. The console now matches the profiles by name
(thermal, ir, visible, optical) and, when the camera does not say, by video
source.

What to check:

- Drag the **thermal** bar. The thermal picture should zoom and the visible one
  should not. Then the other way round. **If dragging one zooms the other, that
  is the finding of the morning** — report which bar moved which picture.
- If your camera really has one lens behind both pictures, the console says so
  in a muted line under the pictures, and both bars move the same glass. That
  sentence appearing is the camera's answer, not a fault.
- Pan with the arrow keys while dragging a zoom slider. Both must happen. They
  have separate queues so that a pan and a zoom cannot discard each other; a
  stop still jumps ahead of everything.

**The readout is what the camera reported, or nothing.** It is never counted
from how long a button was held. So `zoom not reported` means the camera was
asked and said it has no position to give, and `checking the lens` means nobody
has asked yet — which is what you will see for a heartbeat or two at every
start-up. If a bar says `zoom not reported` for ever on a camera that does zoom,
report it.

---

## 5. Fullscreen — the pictures and nothing else

Press **F11**, or the button in the same row as the All/thermal/visible chooser.
The status band, the tab bar and the whole right-hand column go away and the
pictures fill the screen.

Check three things:

- **The picture is still there.** Not black, not frozen. Nothing is reparented
  going in or coming out, and this is exactly the failure that would show.
- **Steering still works** — arrow keys and edge drags both.
- **You can get out**: `Esc`, `F11`, or the button, which says
  `Leave fullscreen  (Esc)` so it is not something to remember.

The view chooser and the split between the two pictures survive the round trip,
so if you have set the thermal narrower than the visible it stays that way.

---

## 6. The second stream

Enable the visible camera as well. Both should play.

If your camera is still sending 4K on that stream, expect trouble — the whole
link is worth about 12 Mb/s (see §11), and that is the link, not the app. Two
things address it, and underneath they are the same operation:

- **Turn the picture down to what the link can carry**, in the **Check the
  camera** box, reads what
  the camera will accept and caps the encoder bitrate over ONVIF, by hand, when
  you press it. It now reads the value back afterwards and tells you when the
  camera answered yes and changed nothing.
- **Turn the picture down by itself when the link gets busy**, in the **Radio**
  box, is a loop that does the same thing on its own. **It is on by default.**
  It watches the radio's airtime, asks the camera for less above 60% and for
  more again after a minute below 45%, never goes under the floor you allow, and
  writes every change it makes into the **Logs** tab. It never touches
  resolution — 4K at 4 Mb/s is still 4K.

Each change interrupts the stream for a moment, so the picture blips. That is
the loop working, not a fault. If you would rather the camera were left exactly
as set, untick it; it stops on the next heartbeat and nothing needs restarting.

Watch the Logs tab for a few minutes here. Lines from `link` are this loop
explaining itself.

---

## 7. Recording, and the thing that outranks everything

Let it record for a few minutes. Then **close the console window**.

Recording must continue. Check it:

```powershell
Get-Process python | Where-Object { $_.CommandLine -match 'record_main' }
```

Then look in the recordings folder and confirm a file is still growing.

Reopen `VMD.exe`. It should adopt the recorder that is already running rather
than starting a second one — the same process ID, and no gap in the footage.

This has been verified here against a synthetic camera. It is the system's
oldest requirement and worth confirming on the real one.

**Why it never worked before, so you know what you are confirming.** Your camera
offers `pcm_mulaw` audio, MP4 cannot store it, and ffmpeg died before the first
frame — every time, for as long as this existed, while the console said
"recording". The command now takes the video stream and nothing else. If there
are `.mp4` files in the recordings folder that keep growing, that is fixed.

---

## 8. Playback

Pick a day. The day comes off a **calendar** with the day before and the day
after beside it, and the days that have something recorded on them are drawn in
the same colour recorded time is drawn in everywhere else.

Click inside the coloured coverage — the recording should open at that time.
Click a gap — it should say how far back the archive goes, how far forward, and
where the hole is, and leave the picture alone. It will not tell you *why* the
gap is there, because nothing records which retention rule reclaimed which hour
and a guess dressed as a finding is worse than silence.

Confirm the coverage drawn matches what is actually on the disk.

Then the things that were missing entirely:

- **The transport row** under the picture: back a minute, back ten seconds, back
  a second, Play/Pause, and the same three forward, plus a speed from quarter to
  eight times. Space, Left and Right do the same, but nothing here is
  keyboard-only.
- **The bar zooms** — whole day, an hour, ten minutes — and pans. At whole-day
  zoom one pixel is over a minute, which is not a thing anybody can aim at.
- **Both cameras on one timeline.** Two players opened at the same wall-clock
  moment, given the same pause, speed and skip. Nothing locks them frame to
  frame and libVLC offers no way to, so **the difference between them is
  measured and printed** beside the time. Check that number against what you can
  see. A moment only one camera recorded takes the other picture away and says
  which one.
- **Save a clip.** Mark start, mark end, **Save the marked clip**, and choose
  where it goes. Somewhere outside the recordings folder — everything in there
  is on a clock, because retention deletes the oldest footage to stay inside the
  space VMD is allowed, so the
  clip of the thing that mattered is already counting down if you leave it
  there. Nothing is re-encoded, so an hour is a disk-to-disk copy and
  what you keep is bit for bit what the camera sent. The cost is that the cut
  lands on a keyframe, so the clip may begin a moment before your mark — in your
  favour, and it says so. A range crossing a gap gives you a shorter clip and a
  sentence saying by how much.

**Open a saved clip in whatever you normally use.** A clip that cannot be played
back is the worst failure this feature has, and it is the one you would find out
about six months late.

---

## 9. Detection

Turn it on for one stream — the tick on that camera's card in Settings, which
says the view's own name: **Watch thermal for movement**. It is off per view on
purpose: aimed at a treeline with no ignored
patches set, it will alarm all day, and an operator who learns to ignore the
alarm strip is worse off than one who never had it. Ticking it unfolds the rest
of that view's settings; leaving it unticked keeps the card down to a name, an
address and one tick.

Under the cards, the **Movement detection** box has the master switch —
**Watch for movement on any view** — which is on. With it off nothing is watched
whatever the cards say, and recording carries on either way.

Then press **Ignore parts of the picture** on that card. The panel it opens
starts with **Show me the picture and let me draw on it**, which grabs a real
frame from your camera and lets you click the sky line and drag rectangles over
anything that moves on its own — a tree, a flag, a road. That is the only
reliable answer to a swaying branch. If the camera cannot be reached, the boxes
underneath are the same settings typed instead of drawn, and nothing you already
had is changed.

Then walk in front of the camera, or have someone do it. An event should appear
in **Recent movement** and the pane should outline. Press **Show me** on the
alarm strip, or double-click the row: it should take you to that moment in
Playback rather than telling you where to look for it.

**A blank confidence column means the thing could not be named, not that nothing
was there.** At 700 m a person is about 13 pixels on the thermal sensor. The
classifier never decides whether to raise an alarm — it only labels what it can.

---

## 10. Things worth breaking on purpose

Each of these should produce a plain sentence you could act on, not a stack
trace, a blank pane, or silence:

- Type the wrong password and Save.
- Point the camera address at something unreachable.
- Point the recordings folder at a drive that does not exist.
- Enter a stream path that is not there.

If any of them shows you a Python traceback or just does nothing, report it —
that class of bug is the reason this list exists.

---

## 11. The radio — the most valuable half hour of the morning

The link is the bottleneck of this whole system. Every bandwidth problem we have
had — the 20–40 second latency, the streams dropping during pans, the stuttering
— was a link problem.

It is last on this page because it is a separate half hour with a terminal in
it, not because it is least important. **Do it before you spend a long time on
anything above §6** — most of what looks like a fault in §2, §3 or §6 turns out
to be this.

### First, ask the radio directly

Open PowerShell in the VMD folder and run:

```powershell
uv run --offline --frozen --no-sync python spike\probe_radio.py 10.0.0.9 --user ubnt
```

Use your radio's address and its username. **It will ask for the password —
type it at the prompt.** There is deliberately no way to put the password on the
command line: PowerShell keeps every command you type, in plain text, for ever.

It prints three blocks:

1. **What the radio sent** — the raw answer from `status.cgi`, formatted so you
   can read it. Your password is blanked out of it.
2. **What the console makes of it** — every figure the console wants, either
   with its value or the word `UNKNOWN` and the exact field names it looked for.
3. **The names your radio actually uses** — its own field names, next to ours.

A good result ends with a line like:

```
The console's link panel will show -63 dBm as its headline.
```

and every figure in block 2 has a value. That means the console will read your
radio properly and there is nothing to do. (The panel's headline is a word now,
not the figure — see below. The probe's sentence is one version behind on that
wording and nothing else.)

A bad result ends with:

```
The console's link panel will show dashes for the signal, because the
signal is not where vmd/radio/airos.py looks for it.
```

**If that happens, copy the whole output of the command and send it to me.** It
contains everything needed — your radio's own field names beside the ones we
guessed — and the fix is a one-line change, not an investigation. There are no
passwords in it.

If it will not connect at all it says so in one sentence: the wrong password,
nothing answering at that address, or a login page coming back instead of an
answer. Send that sentence.

### Then, look at the console

Enter the same address, username and password in **Settings** and press Save.

The band across the top shows the signal at a glance. The **Link** panel
in the Live tab's right-hand column is now three layers, and which one you are
in is your choice:

- **One word**, big, at the top: `GOOD`, `FAIR`, `BUSY`, `FULL`, `WEAK` or
  `NO LINK`, with one short line under it saying what to do about it. A weak
  signal beats a full link for the word, because the signal is the one with
  something to do about it on the roof. Before the radio has answered it says
  `CHECKING`; with no radio configured, `NOT SET UP`.
- **Two bars** — **Signal** and **Link in use** — each with the thresholds
  marked as hairlines on the track. `-66 dBm` means nothing without the scale it
  sits on; a bar with a mark at the point where the reading changes meaning says
  "past it" to somebody who has never heard of a dBm. The fill travels rather
  than jumps, so a change is something you can see happen.
- **`▸ Details`**, which opens every sentence the panel used to show without
  being asked: signal at both ends of the hop, how far above the noise it is,
  the airtime split between in and out, what it is carrying, link quality, and
  which radio it is. It stays shut between restarts. Nothing was deleted to make
  the panel shorter, and the same thresholds decide the word and the sentences,
  so the two cannot disagree about one radio.

There is no distance on the panel. Your radio reports two of them — `0` in one
field and `1` in another, on a path that is really 15 km — so neither is in
metres, nothing here can say what they are, and a distance nobody can justify is
left off rather than printed.

The panel should go from `CHECKING` to real figures within a few seconds. If it
stays on `NO LINK` or says the radio reported no figures, that is the probe's
job — go back and run it.

### What the numbers mean here

**Signal.** This is the one to watch.

| Reading | What it means for us |
|---|---|
| −65 dBm or stronger | Healthy. The link has room for the video and room to spare. `GOOD`. |
| −65 to −80 dBm | Works, but there is no margin left. Rain, or a mast that has moved a little, will take it below. Worth getting someone onto the alignment before winter. `FAIR`. |
| Weaker than −80 dBm | Marginal. This is where the picture starts breaking up. `WEAK`. |

These come from what airOS radios generally do — a noise floor around −90 to
−96 dBm — and not from measurements of your link. Yours reads −66 dBm at this
end and −63 dBm at the far end, which is the amber band: it works, and it has
little left over. They are set one step pessimistic on purpose: being told a
working link is marginal costs a phone call, being told a marginal link is fine
costs the picture on the day it matters.

**Airtime — the `Link in use` bar.** This is the line that explains the video,
and it is the one to look at before any other. A wireless link runs out of *time
on the air*, not of megabits: how many megabits a given slice of airtime carries
depends on the modulation rate, which falls as the signal does. Your radio
reported **88%** while carrying 10.7 Mb/s.

| Reading | What it means |
|---|---|
| Below 60% | Room to spare. Another stream would fit. `GOOD`. |
| 60–80% | Little room left. No headroom for a burst — a key frame, or a pan — and latency builds from here. `BUSY`. |
| 80% and up | Full. A picture that stutters, falls behind, or drops during a pan is this, not the camera and not the console. `FULL`. |

At 88% the panel also says what that arithmetic implies: 10.9 Mb/s costing 88%
of the air means the whole link is worth about 12 Mb/s. That is the number to
hold against any question about a second stream or a 4K one — not the 194 Mb/s
the radio estimates. The fix is the camera's bitrate — the automatic loop in
§6, **Turn the picture down to what the link can carry**, or the second stream
turned off — not
anything in this program.

Those are the same two thresholds the automatic bitrate loop acts on. They are
imported from the panel rather than chosen again, so the panel reading `BUSY`
and the loop deciding to turn the picture down are one event.

**The capacity figures are an estimate.** They are behind `Details`, drawn grey,
and say so. Your radio claims 194 Mb/s in and 227 Mb/s out; that is a modulation
rate worked out from the signal, not a ceiling this link has ever reached, and
printing "13% of capacity" beside 88% airtime is how a full link came to look
healthy. The units are settled now — the capacity and the throughput are both in
kb/s, confirmed against your own radio rather than assumed.

**Link quality.** Behind `Details`, shown as a percentage. Below about 80% the
link is spending its time retrying rather than carrying data. Yours reports 100%
both ways.

### What is no longer unproven

The parser had been written twice against documentation and never once against a
device. It has now met yours, and the probe is what did it: the signal of a
station is not where the code was looking, there is no CCQ figure on your
firmware, and the airtime — the reading that mattered most — was not being read
at all. All three are fixed, and your radio's own answer is kept in the tests so
they cannot quietly stop being true.

---

## What I could not test, and you should not assume works

- **The real camera** — no thermal head, no visible head, no real ONVIF, no 4K.
  Everything in §4 in particular is written against a camera that answers ONVIF
  the way the specification says it does.
- **Two lenses on one gimbal.** The profile matching has been tested against
  cameras built out of what real ones send, and never against yours. It is the
  first thing on this page that can be wrong quietly: sending the thermal zoom
  to the visible lens raises nothing and logs nothing.
- **The automatic bitrate loop against a real camera and a real link.** Its
  arithmetic is tested; that a write lands, that the picture recovers, and that
  it settles rather than hunts are all things only your link can show.
- **A clip saved from real footage.** Proved here against segments this machine
  recorded, decoded frame by frame. Never against footage your camera produced.
- **The radio link failure the rewrite exists to fix.** The clean ten-minute run
  here was over loopback.
- **The airOS radio** — no longer "at all". The probe has now read yours and the
  parser is built on what it sent: signal, far-end signal, noise, airtime,
  throughput, link quality and uptime all come off your own answer, and that
  answer is kept in the tests. What is still unread is the radio under a FAULT —
  a link that has actually dropped, or a far end that has lost power.
- **The two-machine offline install.** The mechanism is proved on one machine;
  the USB handoff to a second is not. Do it with someone who can read a screen,
  not on the day the camera goes up.
- **`VMD.exe` itself in anger** — every test run here was `python -m vmd.desktop`.

---

## One question only you can answer

The deleted `mockup/console.html` is still in this public repository's history
(commits `fa2f4a0` and `a1d89ab`). It carries a radio login and a camera login
that were written into the mockup as examples.

They look like invented placeholders. **If either is a password you actually
use, it is public and must be changed.** Look at those two commits, tell me,
and I will rewrite the history as well.

They are not repeated here on purpose: writing them into a second file that is
also public would make the problem worse rather than smaller.
