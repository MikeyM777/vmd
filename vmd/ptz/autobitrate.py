"""Keeping the camera's bitrate inside what the radio link is actually carrying.

The owner asked for this in one sentence: "can you do something automatic that
always looking at the numbers and matching the resolution and MBPS according to
the airOS numbers?" This is that loop, minus the resolution - see below.

**Airtime is the thing that is full, not throughput.** A wireless link runs out
of TIME on the medium, not out of bits per second, and the two do not look alike
from here. On his NanoStation the radio reported `polling.use` at 88% while the
same link's 10.7 Mb/s against the airMAX capacity estimate read as 13% used and
looked perfectly healthy. At that 88% a PTZ command took 2.02 s to answer,
because it was queued behind video. Queueing delay grows as 1/(1-p) in the
utilisation p: at 50% a packet waits twice its own transmission time, at 70%
three times, at 88% eight times. That curve, not a bandwidth figure, is why
steering the head became unusable, and it is what this loop controls.

**Signal is a second opinion and never the first.** It predicts where the
airtime is going - a link losing signal drops to a lower modulation, and the
same bitrate then costs far more airtime than it did - but it is not itself a
measurement of how full the link is. So it can veto spending more; it cannot
order a cut on its own, and it is deliberately read as a TREND rather than
against an absolute number. That matters more here than it looks: the owner is
testing with the antennas close together at -66 dBm, and at the real 15 km range
his radio's own expectation is `dl_signal_expect: -80`. A rule written against
an absolute figure would be tuned to a bench link and would pin the picture at
the floor for ever on the link this console actually exists for.

**Resolution is deliberately not touched.** He mentioned it, and it is the wrong
lever: 4K at 4 Mb/s is still 4K, whereas a resolution change alters what every
downstream consumer is decoding - go2rtc, the recorder's segments, the
detector's ignore masks and horizon, all of which are in frame pixels. Far more
disruption for less benefit. This loop has no vocabulary for it: the only thing
it can ask the camera for is a number of kilobits.

**Nothing here talks to anything.** `poll` is fed a reading that has already
been taken and returns immediately; the ONVIF write, which takes seconds across
this link, is handed to an executor. See `vmd/desktop/watch.py`, whose rules
these are and whose seam this is the same seam as.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from vmd.radio.panel import AIRTIME_BUSY_PERCENT, STALE_AFTER_SECONDS
from vmd.settings import Settings

# The operator reads this in the Logs tab and has no terminal. Its own name,
# beside "recorder", "detector" and "go2rtc", because what it is about is the
# link rather than any one child process.
logger = logging.getLogger("link")

# ---------------------------------------------------------------- the thresholds
#
# Above this share of the airtime, sustained, the picture comes down. It is
# `vmd/radio/panel.py`'s own figure, imported rather than chosen again, so that
# the panel reading BUSY and the loop deciding to act are the same event as far
# as the operator is concerned. Two numbers for one idea is two opinions within
# a month, and the operator would be the one holding both of them.
#
# It is also where the queueing curve turns: at 60% a packet waits about 1.5
# times its own transmission time, at 70% three times, and by 88% - measured on
# his link - eight, which is the 2.02 s PTZ command.

# And below this share, sustained, there is room to spend more. The gap between
# the two is not a comfort margin, it is the anti-oscillation condition: one
# step up multiplies the video bitrate by UP_FACTOR, and on a link whose traffic
# is nearly all video that multiplies the airtime by about as much. So the calm
# threshold has to satisfy CALM * UP_FACTOR < BUSY, or the loop raises, finds
# itself busy, cuts, finds itself calm, and blips the picture round that circle
# for ever. 45 x 1.15 = 51.75, comfortably under 60.
AIRTIME_CALM_PERCENT = 45.0

# How long a reading has to hold before it is acted on. A pan floods the link
# for a second or two, a keyframe for a fraction of one, and a vehicle crossing
# the scene for a few - so ten seconds of continuously busy airtime is longer
# than any transient this camera produces and is a real change in what the link
# will carry. It is deliberately short: falling behind costs the picture NOW.
BUSY_FOR_SECONDS = 10.0

# Going the other way is speculative - nothing has measured that the link will
# take more, the loop is guessing that it will - and being wrong costs a blip
# now and congestion afterwards. Six times the busy window, so the loop retreats
# six times faster than it advances and a marginal link settles low instead of
# hunting. A minute is also longer than the ordinary lulls: traffic gone, scene
# still, the camera between events.
CALM_FOR_SECONDS = 60.0

# How far one step moves. Down hard, up gently, for the same reason the windows
# are asymmetric: the cost of being too high is paid continuously and the cost
# of being too low is paid once.
DOWN_FACTOR = 0.7
UP_FACTOR = 1.15

# The least time between two writes. Every one of them interrupts the stream -
# go2rtc reconnects and the operator sees a blip - so this is a hard limit on
# how often the picture may be disturbed, independent of what the link is doing.
# Worst case a collapsing link walks from a 5 Mb/s ceiling to a 1 Mb/s floor in
# five steps and two and a half minutes, which is fast enough; a recovering one
# climbs back over about a quarter of an hour, which is slow enough that nobody
# watching notices it happening.
MIN_SECONDS_BETWEEN_DOWN = 30.0
MIN_SECONDS_BETWEEN_UP = 180.0

# How much signal may fall across the calm window before a rise is refused.
# About one modulation step: a link shedding that much while still looking quiet
# is a link on its way down, and the calm is about to stop being true. Read as a
# difference and never as a level, so it means the same thing at -66 dBm on the
# bench and at -80 dBm at 15 km.
SIGNAL_FALLING_DB = 6.0

# The fewest readings a window may be decided on, however long it spans. Without
# it, two readings taken ten seconds apart across a gap in which the radio was
# unreachable would count as "busy for ten seconds".
MIN_SAMPLES = 3


@dataclass(frozen=True)
class LoopState:
    """What the loop is doing, for anything that has to report it.

    `running` is about the loop, not about the link: False means it is not
    watching at all - switched off, or with no radio reading to watch - and
    `reason` says which in words the operator can act on.
    """

    running: bool
    reason: str
    target_kbps: int | None
    below_floor: bool
    changes: int
    refused: int


@dataclass(frozen=True)
class _Sample:
    at: float
    airtime: float
    signal: float | None


class BitrateLoop:
    """One camera, held at the highest bitrate the link is carrying comfortably.

    `apply` is handed a whole-link budget in kb/s and is expected to do whatever
    that means at the camera - which is `PtzService.fit_encoders_to_link`,
    sharing the budget between the streams and verifying what landed. It is
    called on the executor's thread and may take seconds; it must never be
    called on the thread that draws the window.
    """

    def __init__(
        self,
        settings: Settings,
        apply: Callable[[int], dict],
        clock: Callable[[], float] = time.monotonic,
        executor: Callable[[Callable[[], None]], None] | None = None,
        share: Callable[[Settings], int] | None = None,
    ) -> None:
        # How many consoles are on this radio, asked of the settings rather than
        # assumed to be one. See `my_ceiling` for what it is for and
        # `vmd/settings.py:consoles_on_this_radio` for how it is answered. A
        # loop built without one is alone on its link, which is what this class
        # assumed for its whole life and is still true of a single camera.
        self._share = share or (lambda _settings: 1)
        self._apply = apply
        self._clock = clock
        self._executor = executor or _daemon_thread
        self._lock = threading.Lock()
        self._samples: list[_Sample] = []
        self._in_flight = False
        self._closing = False
        self._last_change_at: float | None = None
        self._changes = 0
        self._refused = 0
        self._below_floor = False
        # What the loop has last told the camera to fit into. Seeded at the
        # ceiling because that is what the operator's ceiling means and what the
        # "Fit the camera to the link" button does by hand: the loop only ever
        # tracks what it has commanded, never what it has guessed.
        self._settings = settings
        self._target = self.my_ceiling()
        self._running = False
        self._reason = "the link has not been read yet"
        # The last thing said about not running, so that a console sitting in
        # one state for months says it once rather than every two seconds. The
        # rule `vmd/desktop/watch.py` uses for the same reason.
        self._said = ""

    # ------------------------------------------------------------------ settings

    def my_ceiling(self) -> int:
        """The most this camera may use, which is the link's ceiling divided.

        "The FLIR sends 2.5 Mbps and multiply it by 2 because there are 2
        cameras." The ceiling on the Settings tab is how much of the LINK the
        video may use, and there are two consoles on that link now. Each of them
        reads the same airtime - airtime is a property of the medium, not of a
        stream, so it already counts the other camera - and each of them holds
        this same figure. Two consoles each spending the whole link is twice the
        link, and both of them then see it full and turn their own camera down,
        for ever, on a link that was never the problem.

        Never below the floor. A ceiling divided until it is under the floor is
        two instructions that cannot both be obeyed, and the floor is the one
        that means "less than this is not worth showing".

        Read every time rather than held: `apply_settings` is where a second
        camera being set up arrives, and this console will not be restarted for
        it.
        """
        bitrate = self._settings.bitrate
        try:
            sharing = max(1, int(self._share(self._settings)))
        except Exception:  # noqa: BLE001 - a share is not worth the loop
            logger.exception("could not work out how many cameras share the link")
            sharing = 1
        return max(bitrate.floor_kbps, bitrate.ceiling_kbps // sharing)

    def apply_settings(self, settings: Settings) -> None:
        """Take a saved change. Switching the loop on or off is one of these."""
        with self._lock:
            self._settings = settings
            bitrate = settings.bitrate
            self._target = max(bitrate.floor_kbps, min(self.my_ceiling(), self._target))
            # The window the loop was reasoning over described a different set
            # of instructions. Keeping it would have the first decision after a
            # save made on evidence gathered under the old ones.
            self._samples = []
            self._below_floor = False
            self._said = ""

    def close(self) -> None:
        """Stop dispatching. Nothing to join: the executor owns its own threads."""
        with self._lock:
            self._closing = True

    # --------------------------------------------------------------- the reading

    def state(self) -> LoopState:
        with self._lock:
            return LoopState(
                running=self._running,
                reason=self._reason,
                target_kbps=self._target,
                below_floor=self._below_floor,
                changes=self._changes,
                refused=self._refused,
            )

    def poll(self, link: dict) -> None:
        """One heartbeat. Never waits, never raises, usually does nothing.

        `link` is `RadioService.status()` - a cached reading with its age on it,
        taken on the radio's own thread. This is called on the thread that draws
        the window, so everything it does has to be arithmetic.
        """
        try:
            self._poll(link or {})
        except Exception:  # noqa: BLE001 - the heartbeat outlives this loop
            logger.exception("the automatic picture setting could not be worked out")

    def _poll(self, link: dict) -> None:
        if self._settings.bitrate.mode != "auto":
            self._stand_down(
                "the picture is set by hand, so the link is not being followed",
                quiet=True,
            )
            return

        why = _unusable(link)
        if why:
            self._stand_down(why)
            return

        now = self._clock()
        with self._lock:
            self._running = True
            self._reason = "following the link"
            self._said = ""
            self._samples.append(
                _Sample(
                    at=now,
                    airtime=float(link["airtime_percent"]),
                    signal=_number(link.get("signal_dbm")),
                )
            )
            oldest = now - max(BUSY_FOR_SECONDS, CALM_FOR_SECONDS)
            self._samples = [sample for sample in self._samples if sample.at >= oldest]
            if self._in_flight or self._closing:
                # A write is on the wire. The link is being disturbed by it, and
                # deciding on that would be deciding on our own noise.
                return
            decision = self._decide(now)
            if decision is None:
                return
            wanted, sentence = decision
            self._in_flight = True
            self._last_change_at = now
            # The samples described the link at the old bitrate. Keeping them
            # would have the next decision made partly on evidence about a
            # camera that no longer exists, which is how a loop oscillates.
            self._samples = []

        self._executor(lambda: self._write(wanted, sentence))

    # -------------------------------------------------------------- the decision

    def _decide(self, now: float) -> tuple[int, str] | None:
        """What to ask for, and the sentence saying why. None means leave it."""
        bitrate = self._settings.bitrate
        busy = self._window(now, BUSY_FOR_SECONDS)
        if busy and all(sample.airtime > AIRTIME_BUSY_PERCENT for sample in busy):
            return self._down(now, busy, bitrate)
        calm = self._window(now, CALM_FOR_SECONDS)
        if calm and all(sample.airtime < AIRTIME_CALM_PERCENT for sample in calm):
            return self._up(now, calm, bitrate)
        return None

    def _window(self, now: float, seconds: float) -> list[_Sample]:
        """The readings covering the last `seconds`, or nothing if they do not.

        Both halves matter. A window that is not full yet is not evidence that
        anything held, and a window filled by two readings either side of a gap
        is not evidence that anything held either.
        """
        inside = [sample for sample in self._samples if sample.at >= now - seconds]
        if len(inside) < MIN_SAMPLES:
            return []
        if now - inside[0].at < seconds:
            return []
        return inside

    def _down(self, now: float, window: list[_Sample], bitrate) -> tuple[int, str] | None:
        wanted = int(self._target * DOWN_FACTOR)
        if wanted < bitrate.floor_kbps:
            wanted = bitrate.floor_kbps
            self._say_below_floor(window, bitrate)
        if wanted >= self._target:
            return None
        if not self._allowed(now, MIN_SECONDS_BETWEEN_DOWN):
            return None
        return wanted, (
            f"The picture has been turned down to {wanted} kb/s: the radio link "
            f"has been {_busy(window):.0f}% busy for the last "
            f"{_held(window, now):.0f} seconds, which is what makes the camera "
            f"slow to answer and the picture stutter."
        )

    def _up(self, now: float, window: list[_Sample], bitrate) -> tuple[int, str] | None:
        fell = _fell_by(window)
        if fell is not None and fell >= SIGNAL_FALLING_DB:
            return None
        wanted = min(self.my_ceiling(), int(self._target * UP_FACTOR))
        if wanted <= self._target:
            return None
        if not self._allowed(now, MIN_SECONDS_BETWEEN_UP):
            return None
        self._below_floor = False
        return wanted, (
            f"The picture has been turned up to {wanted} kb/s: the radio link "
            f"has been quiet - {_busy(window):.0f}% busy - for the last "
            f"{_held(window, now):.0f} seconds, so it has room for a better picture."
        )

    def _allowed(self, now: float, gap: float) -> bool:
        """Has enough time passed since the last write to disturb the picture again?"""
        if self._last_change_at is None:
            return True
        return now - self._last_change_at >= gap

    def _say_below_floor(self, window: list[_Sample], bitrate) -> None:
        """The state that means "this link cannot do this job".

        The floor is a floor. Below it the picture is not worth having, so the
        loop stops rather than going under it - and then it has to SAY so, once,
        because a picture that stutters with nothing said about it looks exactly
        like a console that is broken.
        """
        if self._below_floor:
            return
        self._below_floor = True
        logger.warning(
            "The radio link cannot carry even %d kb/s, which is the lowest "
            "picture you have allowed: it has been %.0f%% busy with the camera "
            "already at that setting. The picture will stutter and the camera "
            "will be slow to answer until the link improves. Nothing here can "
            "fix that - it is the link, not the camera.",
            bitrate.floor_kbps,
            _busy(window),
        )

    # ----------------------------------------------------------------- the write

    def _write(self, wanted: int, sentence: str) -> None:
        """The slow half, on the executor's thread. Never raises into it."""
        try:
            result = self._apply(wanted)
        except Exception as exc:  # noqa: BLE001 - the console outlives the camera
            logger.exception("the camera would not take a new bitrate")
            result = {"ok": False, "error": str(exc)}
        with self._lock:
            self._in_flight = False
            if not result.get("ok"):
                # The camera did not take it, so the loop's idea of where the
                # camera is has not changed. Believing otherwise would have the
                # next decision compounded on a write that never happened -
                # which is how this project has twice ended up reasoning about a
                # camera setting nobody had checked.
                logger.warning(
                    "The picture could not be changed to %d kb/s: %s. It will be "
                    "tried again.",
                    wanted,
                    result.get("error") or "the camera did not say why",
                )
                return
            refused = result.get("refused") or []
            if refused:
                # And the loop's idea of where the camera is does not move,
                # which is the other half of "the request was not believed" and
                # the half that was not true. Moved, the next busy window took
                # 70% of a bitrate the camera never had, and the one after that
                # 70% of that - walking the loop down to the floor while the
                # camera sat where it started, and then climbing back from a
                # number that was fiction. It is also what makes the retry
                # right: `apply_budget` skips a stream already at its target, so
                # asking for the same budget again asks only the stream that
                # refused.
                self._refused += 1
                logger.warning(
                    "The camera was asked for %d kb/s and did not keep it on %s. "
                    "What it reports now is what it is doing; the request was not "
                    "believed.",
                    wanted,
                    ", ".join(str(name) for name in refused),
                )
                return
            self._target = wanted
            self._changes += 1
        logger.info("%s", sentence)

    # ------------------------------------------------------------- standing down

    def _stand_down(self, why: str, quiet: bool = False) -> None:
        """Do nothing at all, and say why - once, not once per heartbeat."""
        with self._lock:
            self._running = False
            self._reason = why
            self._samples = []
            fresh = why != self._said
            self._said = why
        if fresh and not quiet:
            logger.info(
                "The picture is not being matched to the radio link: %s.", why
            )


def _unusable(link: dict) -> str:
    """Why this reading cannot be acted on, or "" if it can.

    No radio reading means no action. A radio that is unreachable, that was
    never set up, that does not report airtime, or whose last answer is too old
    to describe the link now, all come out here - and the loop then does nothing
    whatsoever. Guessing at a link you cannot see is worse than leaving the
    camera alone, because the camera at least has a setting somebody chose.
    """
    if not link:
        return "the radio has not been read"
    if not link.get("connected"):
        reason = str(link.get("reason") or "").strip()
        if link.get("checking"):
            return "the radio has not answered yet"
        return reason or "the radio cannot be read"
    if _number(link.get("airtime_percent")) is None:
        return "this radio does not report how busy the link is"
    age = _number(link.get("age_seconds"))
    if age is None:
        return "the radio's reading has no age, so how current it is cannot be told"
    if age >= STALE_AFTER_SECONDS:
        return f"the radio's last reading is {age:.0f} seconds old, which is too old to act on"
    return ""


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _busy(window: list[_Sample]) -> float:
    """How busy the link has been across the window, on average.

    The average rather than the newest reading, because what is being reported
    is the thing that held for the whole window - which is what was acted on.
    """
    return sum(sample.airtime for sample in window) / len(window)


def _held(window: list[_Sample], now: float) -> float:
    return max(0.0, now - window[0].at)


def _fell_by(window: list[_Sample]) -> float | None:
    """How much signal has been lost across the window, or None if unknown.

    A difference and never a level. See the module docstring: an absolute
    threshold would be a threshold tuned to a bench link.
    """
    known = [sample.signal for sample in window if sample.signal is not None]
    if len(known) < 2:
        return None
    return known[0] - known[-1]


def _daemon_thread(work: Callable[[], None]) -> None:
    """One ONVIF write, off whatever thread asked for it.

    The same seam and the same rule as `vmd/desktop/watch.py`: a daemon, never
    joined, because these outlive a closing window and a console that is closing
    may not wait on a camera at the far end of a radio link.
    """
    threading.Thread(target=work, name="vmd-link-bitrate", daemon=True).start()
