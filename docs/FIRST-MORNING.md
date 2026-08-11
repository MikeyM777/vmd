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
index and it is normal. It is not hung.

---

## 1. Settings — type it in, save it, check it stuck

Camera address, username, password. One stream to begin with — the thermal, at
whatever path your camera uses (`/ch2` on the FLIR).

Press **Save**. It should say `Saved.`

Then open `settings.json` in Notepad and confirm it contains exactly what you
typed. If a field is missing or different, that is a bug and worth reporting
before anything else — settings that do not persist make every later result
meaningless.

**Passwords are shown, not masked.** That is deliberate and was asked for.

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

Now the important one: **steer continuously for a full minute**, then leave it
still for five.

The picture must not drop. On the old console, panning was what killed the
stream, and my own recovery timers made it worse. Every timer that did that has
been removed.

Then check the hazard case: hold an arrow key, and while still holding it,
switch to the Settings tab. **The camera must stop.** It used to keep slewing
until it hit its mechanical stop.

---

## 4. The second stream

Enable the visible camera as well. Both should play.

If your camera is still sending 4K on that stream, expect trouble on a 5 Mb/s
link — that is the link, not the app. The Settings tab has **Fit the camera to
the link**, which reads and caps the encoder bitrate over ONVIF.

---

## 5. Recording, and the thing that outranks everything

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

---

## 6. Playback

Pick today. Click inside the coloured coverage — the recording should open at
that time. Click a gap — it should tell you there is no recording there and
leave the picture alone.

Confirm the coverage drawn matches what is actually on the disk.

---

## 7. Detection

Turn it on for one stream — **Watch for movement** on the stream's second line
in Settings. It is off by default on purpose: aimed at a treeline with no
ignored patches set, it will alarm all day, and an operator who learns to ignore
the alarm strip is worse off than one who never had it.

Press **the picker button** on that stream row. It grabs a real frame from your
camera and lets you click the sky line and drag rectangles over anything that
moves on its own — a tree, a flag, a road. That is the only reliable answer to
a swaying branch.

Then walk in front of the camera, or have someone do it. An event should appear
in **Recent movement** and the pane should outline.

**A blank confidence column means the thing could not be named, not that nothing
was there.** At 700 m a person is about 13 pixels on the thermal sensor. The
classifier never decides whether to raise an alarm — it only labels what it can.

---

## 8. Things worth breaking on purpose

Each of these should produce a plain sentence you could act on, not a stack
trace, a blank pane, or silence:

- Type the wrong password and Save.
- Point the camera address at something unreachable.
- Point the recordings folder at a drive that does not exist.
- Enter a stream path that is not there.

If any of them shows you a Python traceback or just does nothing, report it —
that class of bug is the reason this list exists.

---

## 9. The radio — the most valuable half hour of the morning

The link is the bottleneck of this whole system. Every bandwidth problem we have
had — the 20–40 second latency, the streams dropping during pans, the stuttering
— was a link problem. Until this morning nothing has ever read the radio, so
everything the console says about it was written from general knowledge of airOS
and not from your device.

Do this part before you spend a long time on anything else.

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
radio properly and there is nothing to do.

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

The status line at the bottom shows the signal at a glance. The **Link** panel
in the Live tab's right-hand column shows the rest: signal, how far above the
noise it is, what is going through the link against what the link will carry,
link quality, distance, and which radio it is.

The panel should go from `Checking the radio...` to real figures within a few
seconds. If it stays on dashes or says the radio reported no signal strength,
that is the probe's job — go back and run it.

### What the numbers mean here

**Signal.** This is the one to watch.

| Reading | What it means for us |
|---|---|
| −65 dBm or stronger | Healthy. The link has room for the video and room to spare. |
| −65 to −80 dBm | Works, but there is no margin left. Rain, or a mast that has moved a little, will take it below. Worth getting someone onto the alignment before winter. |
| Weaker than −80 dBm | Marginal. This is where the picture starts breaking up. |

These come from what airOS radios generally do — a noise floor around −90 to
−96 dBm — and not from measurements of your link, because nobody has measured
your link yet. They are set one step pessimistic on purpose: being told a
working link is marginal costs a phone call, being told a marginal link is fine
costs the picture on the day it matters.

**Coming in / going out.** This is the line that explains the video. Your link
carries about 5 Mb/s. If "coming in" is close to the number beside it, the link
is full, and a picture that stutters, falls behind, or drops during a pan is the
link and not the camera or the console. The fix for that is the camera's
bitrate — **Fit the camera to the link** in Settings, or the second stream
turned off — not anything in this program.

Note both figures the first time you look, next to what the radio's own web
interface says. The console reads the capacity in kb/s and the older rate fields
in Mb/s; that is what airOS is understood to do and it has not been confirmed on
a real radio. If the console's numbers and the radio's own page disagree, tell
me — that is a five-minute fix too.

**Link quality.** Shown as a percentage. Below about 80% the link is spending
its time retrying rather than carrying data.

### What is still unproven

The parser has now been written twice against documentation and never once
against a device. The probe is the thing that settles it, and running it once is
what turns everything on this page from "should" into "does".

---

## What I could not test, and you should not assume works

- **The real camera** — no thermal head, no visible head, no real ONVIF, no 4K.
- **The radio link failure the rewrite exists to fix.** The clean ten-minute run
  here was over loopback.
- **The airOS radio**, at all. Section 9 is how that stops being true: run
  `spike/probe_radio.py` once and send me what it prints.
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
