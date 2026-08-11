"""The two lenses, and how often it is fair to ask them where they are.

This sits between the zoom bars on the screen and the ONVIF client. It knows
three things the layers either side of it must not have to:

* **which profile token is which picture.** `vmd/ptz/onvif.py` can tell the
  lenses apart; this is what remembers the answer, so the console's "thermal"
  and "visible" reach the right glass without the widget knowing a token exists.
* **how to zoom a camera that cannot be told where to go.** Absolute zoom is
  optional in ONVIF and plenty of cameras refuse it. When it is there, a slider
  means "go here"; when it is not, the buttons mean "keep going while I hold
  you" and the slider is a readout. One decision, made once, from what the
  camera said about itself.
* **when NOT to ask.** This is the part that matters most here, and it is not
  obvious.

The link is the reason. This camera is at the far end of a Ubiquiti hop that was
measured at 88% of its airtime while carrying the video, with ONVIF replies
taking two seconds. A zoom readout polled on the console's two-second heartbeat
would be two more SOAP round trips every two seconds, for ever, on a link that
has nothing spare - to refresh a number that only changes when somebody touches
the zoom. That is the console making its own link worse in order to draw a
figure nobody was looking at.

So the position is asked for when there is a reason to believe it changed:

* once, when the camera is first found, so the sliders start somewhere true;
* after a zoom command, a few times while the lens is still travelling;
* and never on a schedule.

A lens somebody else moved - from the camera's own web page, say - goes stale on
this screen until the next zoom. That is a real cost and it is the right trade:
the slider is there so he can see where HIS zoom got to, and the alternative
spends the link he is trying to watch through.
"""

from __future__ import annotations

import logging
import time

from vmd.ptz.onvif import PtzError, match_profiles

logger = logging.getLogger(__name__)

# How long after a zoom command the lens is still worth asking about, and how
# often. A 30x lens crossing its whole travel takes a few seconds; asking every
# second and a half for six catches the end of that without turning a slider
# into a traffic source.
SETTLING_SECONDS = 6.0
SETTLING_EVERY = 1.5

# Speed for a held zoom button on a camera that cannot be sent to a position.
# Deliberately slow: the picture is 700 m away and the round trip on the last
# measurement was two seconds. A fast zoom on a two-second feedback loop
# overshoots every time, and the operator ends up hunting.
CREEP_SPEED = 0.35

# The reason a lens has no answer yet, before anybody has asked. Named because
# the screen has to tell it apart from every other reason there is no answer: it
# is the only one that is not a fault, it lasts a heartbeat or two after every
# start-up, and drawn as a fault it is one the operator sees each morning and
# learns to ignore.
NOT_ASKED = "the camera has not been asked yet"

# How long to leave a camera alone after it has failed to say what lenses it has.
#
# `poll` runs on the console's two-second heartbeat, and discovery is the one
# part of it that is not already rate-limited: a camera that answered is never
# asked again, but a camera that did NOT answer was asked on every single beat,
# for as long as the console was open. Against a wrong password that is three
# HTTP requests every two seconds against a device whose own firmware answers
# 403 "after too many tries"; against a camera that is switched off it is an
# eight-second timeout every two seconds, for months. Either way it is the
# console putting traffic on a link with nothing spare, on a schedule, to ask a
# question it has already been told the answer to - which is the one thing the
# docstring above says this file exists not to do.
#
# It is also the command sender's own thread. While it sits inside that call,
# the stop the operator owes the head when he lets go of an arrow key is in the
# mailbox waiting for it.
#
# Thirty seconds, which is `DiskWatcher`'s number for the same shape of
# question and about four times the ONVIF timeout, so the sender is idle
# between attempts rather than permanently inside one. The cost is that a camera
# which comes back takes up to half a minute to be noticed - and nothing is
# waiting on that but a slider, because steering does not go through here.
RETRY_AFTER_SECONDS = 30.0


class Lenses:
    """Per-picture zoom, over one camera and one shared gimbal.

    Built around any object with the `OnvifPtz` shape. Nothing here starts a
    thread or touches Qt: it is called from whatever already owns the camera's
    worker, because two threads posting SOAP at one camera over a link this busy
    is how commands start timing out.
    """

    def __init__(self, camera, streams: list[str], clock=time.monotonic) -> None:
        self._camera = camera
        self._streams = list(streams)
        self._clock = clock
        self._tokens: dict[str, str] = {}
        self._positions: dict[str, float | None] = {}
        # When each lens was last commanded. Not when it was last read: the
        # question this answers is "might it still be moving", and a read tells
        # you nothing about that.
        self._commanded: dict[str, float] = {}
        self._read_at: dict[str, float] = {}
        self._absolute = False
        self._found = False
        # When discovery was last attempted and failed. See RETRY_AFTER_SECONDS:
        # a camera that cannot answer must not be asked on every heartbeat, and
        # this is the only thing that remembers it was asked at all - `reason`
        # says what went wrong, never when.
        self._asked_at: float | None = None
        self.reason = NOT_ASKED

    # ----------------------------------------------------------- discovery

    def find(self) -> bool:
        """Ask the camera what it has, and remember which lens is which.

        Safe to call repeatedly, and cheap. A camera that answered is never
        asked again - it does not grow a third lens - and a camera that did not
        is left alone for RETRY_AFTER_SECONDS rather than asked on every beat of
        the console's heartbeat.
        """
        if self._found:
            return True
        now = self._clock()
        if self._asked_at is not None and now - self._asked_at < RETRY_AFTER_SECONDS:
            # Already refused, recently. `reason` still holds the camera's own
            # words for it, so nothing on screen changes; what changes is that
            # the link is not spent asking again.
            return False
        self._asked_at = now
        try:
            profiles = self._camera.profiles()
        except PtzError as exc:
            self.reason = str(exc)
            return False
        if not profiles:
            self.reason = "the camera listed no media profiles, so there is no lens to zoom"
            return False

        self._tokens = match_profiles(self._streams, profiles)
        self._absolute = bool(getattr(self._camera.capability, "absolute_zoom", False))
        self._found = True
        self.reason = "ready"
        if self.shared():
            # Worth a log line and worth saying on screen: two zoom controls
            # that move one lens is confusing until somebody knows why.
            logger.info(
                "%s: one lens behind %d pictures, so both zoom controls move the same glass",
                getattr(self._camera, "host", "the camera"),
                len(self._streams),
            )
        return True

    def token(self, stream: str) -> str | None:
        """The profile token for one picture, or None if it could not be placed."""
        return self._tokens.get(stream)

    def absolute(self) -> bool:
        """Whether a lens can be sent to a zoom rather than only nudged."""
        return self._absolute

    def shared(self) -> bool:
        """Whether every picture is behind the same lens."""
        return bool(self._tokens) and len(set(self._tokens.values())) == 1

    # ------------------------------------------------------------- moving

    def go_to(self, stream: str, where: float) -> dict:
        """Send one lens to a zoom, 0.0 wide to 1.0 tele."""
        token = self._for(stream)
        if token is None:
            return {"ok": False, "reason": self.reason}
        if not self._absolute:
            # Asked to go somewhere by a camera that only knows how to keep
            # going. Refused rather than approximated: creeping for a guessed
            # length of time and calling it 70% is the invented figure this
            # whole control was built to avoid.
            return {
                "ok": False,
                "reason": "this camera cannot be told where to zoom, only to keep zooming",
            }
        try:
            self._camera.zoom_to(where, profile=token)
        except PtzError as exc:
            return {"ok": False, "reason": str(exc)}
        self._commanded[stream] = self._clock()
        return {"ok": True, "reason": ""}

    def creep(self, stream: str, speed: float) -> dict:
        """Keep one lens zooming, or stop it when the speed is zero."""
        token = self._for(stream)
        if token is None:
            return {"ok": False, "reason": self.reason}
        try:
            if speed:
                self._camera.move(0.0, 0.0, _sign(speed) * CREEP_SPEED, profile=token)
            else:
                # Only the zoom. The gimbal is shared, and letting go of a zoom
                # button must not halt a pan the operator is still holding.
                self._camera.stop(profile=token, pan_tilt=False, zoom=True)
        except PtzError as exc:
            return {"ok": False, "reason": str(exc)}
        self._commanded[stream] = self._clock()
        return {"ok": True, "reason": ""}

    # ------------------------------------------------------------ reading

    def due(self, stream: str) -> bool:
        """Whether it is worth spending a SOAP call on this lens right now.

        The whole policy, in one place: once at the start, then while a lens is
        still travelling after a command, then never. See the module docstring
        for why "never" is the right answer on this link.
        """
        if self._for(stream) is None:
            return False
        now = self._clock()
        read = self._read_at.get(stream)
        if read is None:
            return True
        commanded = self._commanded.get(stream)
        if commanded is None or now - commanded > SETTLING_SECONDS:
            return False
        return now - read >= SETTLING_EVERY

    def read(self, stream: str) -> float | None:
        """Ask the camera where one lens is. Costs a SOAP call; check `due` first."""
        token = self._for(stream)
        if token is None:
            return None
        self._read_at[stream] = self._clock()
        try:
            answer = self._camera.position(profile=token)
        except PtzError as exc:
            logger.debug("could not read the zoom of %s: %s", stream, exc)
            return self._positions.get(stream)
        zoom = (answer or {}).get("zoom")
        # A camera that answered without a zoom has said it does not report one.
        # That is drawn as "not reported" rather than as a position, which is
        # the whole reason the widget accepts None.
        self._positions[stream] = None if zoom is None else max(0.0, min(1.0, float(zoom)))
        return self._positions[stream]

    def position(self, stream: str) -> float | None:
        """The last position read, without asking the camera anything.

        This is what the screen calls on its heartbeat. It never talks to the
        camera, which is what makes it safe to call as often as the window
        redraws.
        """
        return self._positions.get(stream)

    def poll(self) -> None:
        """Read whichever lenses are worth reading. One heartbeat's worth of work."""
        if not self.find():
            return
        for stream in self._streams:
            if self.due(stream):
                self.read(stream)

    def _for(self, stream: str) -> str | None:
        if not self._found and not self.find():
            return None
        return self._tokens.get(stream)


def _sign(value: float) -> float:
    return 1.0 if value > 0 else -1.0
