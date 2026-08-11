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

## 9. The radio

Enter the Ubiquiti address, username and password. The status line at the bottom
should show a signal strength instead of `link -`.

This has never been exercised against a real airOS device.

---

## What I could not test, and you should not assume works

- **The real camera** — no thermal head, no visible head, no real ONVIF, no 4K.
- **The radio link failure the rewrite exists to fix.** The clean ten-minute run
  here was over loopback.
- **The airOS radio**, at all.
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
