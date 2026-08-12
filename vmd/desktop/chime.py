"""A sound when something moves, because he is not always looking at the screen.

`DESIGN.md` has said since it was written that an arriving alarm makes a sound.
Nothing in the program made one. A review found the gap by grepping for it, and
the honest options were to delete the promise or to keep it.

Keeping it, and the reason is the whole point of the system. This console runs
24/7 on a laptop in a room, watching a perimeter 700 m away. The alarm strip is
red, it is wide, and it is completely silent - so an intrusion at 03:40 while he
is turned away, or making coffee, or asleep in a chair, is announced to an empty
chair and cleared by the next thing that moves. Every other part of this project
has been built on the principle that a state nobody can perceive is a state that
does not exist. A silent alarm is that, in the one place it matters most.

Four rules, and each of them is a way this could be made worse than silence:

* **Nothing is downloaded, ever.** The sound is Windows' own, from
  `C:\\Windows\\Media`, with the system beep behind it. This laptop has no
  network and a console that wanted a sound file from somewhere would be a
  console that does not work.
* **It never blocks.** `SND_ASYNC`, always. A sound played synchronously on the
  thread that draws the window is a window that stops repainting for the length
  of the sound - during an alarm, which is the worst moment this console has.
* **It cannot become noise.** One sound per alarm, and never two inside
  `QUIET_SECONDS`. Movement on a windy night is not one event, it is forty, and
  forty chimes in a minute is a console somebody turns the speakers off for -
  after which it is silent again, and now nobody knows.
* **It can be switched off**, and being off is a real state rather than a broken
  one. Somebody sleeping in the same room has a good reason, and taking the
  choice away is how the speakers get unplugged instead.

Windows-only by construction: `winsound` is in the standard library and this is
a Windows console. Everywhere else this is silent and says so, rather than
pulling in an audio stack for a machine that will never run it.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# How long the console stays quiet after a sound, whatever else arrives.
#
# Twelve seconds. Long enough that a gust through a treeline is one chime and
# not a rattle; short enough that a second, separate intrusion a quarter of a
# minute later is still announced. The alarm strip and the movement list are
# unaffected - they show every event, and this only decides how often the room
# is told.
QUIET_SECONDS = 12.0

# Windows' own sounds, in the order they are tried. All three ship with the OS,
# so this is a local file read and never a download.
#
# Chosen for what they sound like rather than for what they are called: this has
# to carry across a room and be recognisable at 03:40 without being the noise a
# machine makes when it is about to do something drastic.
CANDIDATES = (
    r"C:\Windows\Media\Windows Proximity Notification.wav",
    r"C:\Windows\Media\Windows Notify System Generic.wav",
    r"C:\Windows\Media\Windows Exclamation.wav",
)


_FIND_ONE = object()


class Chime:
    """The sound an alarm makes, and the rules about when it does not.

    Holds no Qt and starts no thread. `winsound` plays asynchronously through
    the OS, so there is nothing here to keep alive between sounds - which also
    means there is nothing here to leak on a console that runs for months.
    """

    def __init__(self, player=_FIND_ONE, clock=time.monotonic, enabled: bool = True) -> None:
        # A sentinel rather than None, because `player=None` has to mean "this
        # machine cannot make a sound" - it is a state the console really gets
        # into and has to be testable. Defaulting on None would have quietly
        # gone and found a player instead, which is the opposite.
        self._player = _windows_player() if player is _FIND_ONE else player
        self._clock = clock
        self._enabled = bool(enabled)
        self._last: float | None = None
        # Counted so the tests, and anybody reading a log, can tell "it played"
        # from "it decided not to". Those are different answers and this class
        # exists to get the difference right.
        self.played = 0
        self.held_back = 0

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def enabled(self) -> bool:
        return self._enabled

    def available(self) -> bool:
        """Whether there is anything here that can make a sound at all.

        False on any machine without `winsound`, and said out loud rather than
        pretended about: a console that reports an audible alarm it cannot
        produce is worse than one that admits it is silent.
        """
        return self._player is not None

    def alarm(self) -> bool:
        """Something moved. Returns whether a sound was actually made.

        The return value is the point of the method rather than a courtesy: the
        caller writes what happened into the log, and "an alarm arrived and the
        room was not told" is a thing somebody investigating a missed intrusion
        needs to be able to read.
        """
        if not self._enabled or self._player is None:
            return False
        now = self._clock()
        if self._last is not None and now - self._last < QUIET_SECONDS:
            self.held_back += 1
            return False
        try:
            self._player()
        except Exception:  # noqa: BLE001 - a sound may never cost the alarm
            logger.exception("the alarm sound could not be played")
            # Deliberately without stamping the clock. Nothing was said to the
            # room, so nothing has been said recently, and the next alarm is
            # still worth trying - a sound device that came back after somebody
            # plugged a headset in must not be silent for the quiet period it
            # never actually used.
            return False
        self._last = now
        self.played += 1
        return True


def _windows_player():
    """The thing that actually makes the noise, or None on a machine without one.

    Resolved once, at construction, because the alternative is finding out
    during an alarm - and `os.path.exists` on a path that is not there is a
    disk touch this would otherwise do every time something moved.
    """
    try:
        import winsound
    except ImportError:
        logger.info("no winsound on this machine, so movement will be silent")
        return None

    import os

    sound = next((path for path in CANDIDATES if os.path.exists(path)), None)

    def play() -> None:
        if sound is not None:
            # ASYNC because the window is drawing, NODEFAULT so that a file
            # which has gone missing since start-up is silence rather than the
            # Windows default ding, which sounds like an error and is not one.
            winsound.PlaySound(
                sound,
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        else:
            # Nothing in Media, which happens on stripped Windows images. The
            # system beep is always there and is better than nothing at all.
            winsound.MessageBeep(winsound.MB_ICONASTERISK)

    return play
