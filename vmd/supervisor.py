"""Keeps services alive. One failing service must never affect another.

Restarting is not recovering. `start()` returning without raising proves that a
process was spawned, not that it did the job it was spawned for - and the two
are told apart by exactly one thing, which is whether it was still there a while
later. The recorder spent a whole day being restarted every five seconds because
ffmpeg refused to write a header for a codec MP4 cannot store: twenty-four
starts, twenty-four processes that died in milliseconds, twenty-four empty
files, and nothing anywhere that concluded anything from the pattern. So a start
that does not stick is counted as its own kind of failure, and a service that has
never had one stick says so.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Protocol

logger = logging.getLogger(__name__)

# How many starts in a row may fail to stick before the supervisor stops
# implying the service is being kept alive. Three, because one death is an
# accident and two is a camera that was rebooting; three identical deaths in a
# row is something that will not work however many more times it is tried.
FLAPPING_AFTER = 3


class Service(Protocol):
    """Anything the supervisor can keep alive.

    `running` is declared as a read-only property rather than a plain attribute:
    a plain attribute would require the implementer to allow assignment, which
    excludes SegmentRecorder, whose `running` is a computed property.
    """

    @property
    def running(self) -> bool: ...

    def start(self) -> None: ...
    def stop(self) -> None: ...


@dataclass
class Managed:
    name: str
    service: Service


class Supervisor:
    """Restarts any service that is not running, after a short delay."""

    def __init__(
        self,
        managed: list[Managed],
        clock: Callable[[], float] = time.monotonic,
        restart_delay: float = 2.0,
        stable_after: float = 60.0,
    ) -> None:
        self.managed = managed
        self.restarts: dict[str, int] = {entry.name: 0 for entry in managed}
        self.failures: dict[str, int] = {entry.name: 0 for entry in managed}
        # Starts that did not stick: the service was started, and by the time
        # anything looked again it was gone, having lasted less than
        # `stable_after`. This is the number that tells a restart that fixed
        # something from one that changed nothing, and it is the number that
        # was missing while the recorder was restarted twenty-four times.
        self.short_lived: dict[str, int] = {entry.name: 0 for entry in managed}
        # Has this service ever been up for `stable_after`? Not the same as
        # "is running": a process spawned two seconds ago is running and has
        # proved nothing yet.
        self.settled: dict[str, bool] = {entry.name: False for entry in managed}
        self._clock = clock
        self._restart_delay = restart_delay
        self._stable_after = stable_after
        self._up_since: dict[str, float] = {}
        self._next_attempt: dict[str, float] = {entry.name: 0.0 for entry in managed}
        self._started_once: set[str] = set()

    def tick(self) -> list[str]:
        """Check every service, start whatever is down. Returns the names started."""
        started: list[str] = []
        now = self._clock()
        for entry in self.managed:
            if entry.service.running:
                # Seeing it alive counts as having seen it start, whoever
                # started it. Most of these are started once directly - the
                # console brings the recorder, the detector and go2rtc up before
                # the first tick - and counting only the starts this object
                # performed meant the first death was recorded as a first start
                # instead of a restart. `restarts` then read zero after every
                # child had died and come back, which is the one number anyone
                # looks at to find out whether something is flapping.
                self._started_once.add(entry.name)
                started_at = self._up_since.get(entry.name)
                if started_at is None:
                    # Started by somebody else - the console brings the
                    # recorder, the detector and go2rtc up itself. All that is
                    # known is that it was alive at this moment, so that is
                    # what the run is timed from.
                    self._up_since[entry.name] = now
                    continue
                if now - started_at >= self._stable_after:
                    # It came back and it worked. That, and only that, is what
                    # clears the record: a start that has lasted this long is
                    # one the operator can believe in.
                    self.failures[entry.name] = 0
                    self.short_lived[entry.name] = 0
                    self.settled[entry.name] = True
                continue
            # It is down. If it was up when we last looked, that start has just
            # ended - and how long it lasted is the whole difference between a
            # service that recovered and one that has been dying identically
            # since this morning.
            # Is this service holding itself back? A recorder whose ffmpeg has
            # died before recording anything too many times in a row stops
            # trying, and says so once - see SegmentRecorder.held_back. Its
            # start() then returns without spawning anything and without
            # raising, which is exactly what this loop used to read as a
            # successful restart.
            #
            # The cost of not asking: every tick counted a restart AND, on the
            # next tick, a short-lived death, for a service that had not been
            # started at all. Three hundred ticks of a permanently broken stream
            # produced 299 restarts, 299 short-lived deaths and fifteen flapping
            # warnings, and the sentence in the log read "it has been started
            # 283 times and has never stayed up for 60 seconds" - about a thing
            # nobody had started once. Those are the numbers the console and the
            # operator read to decide whether something is flapping, and the
            # warnings flood the 500-line Logs ring that the hold-back exists to
            # protect.
            #
            # Asked with getattr because most services have no such notion, and
            # a service that has none is not held back.
            try:
                held = bool(getattr(entry.service, "held_back", False))
            except Exception:  # noqa: BLE001 - a service that cannot say is not held
                logger.exception("could not ask %s whether it is held back", entry.name)
                held = False

            started_at = self._up_since.pop(entry.name, None)
            if not held and started_at is not None and now - started_at < self._stable_after:
                self.short_lived[entry.name] += 1
                self._say_if_flapping(entry.name)
            if now < self._next_attempt[entry.name]:
                continue
            try:
                entry.service.start()
            except Exception:  # noqa: BLE001 - one bad service must not stop the others
                self.failures[entry.name] += 1
                # A permanently broken stream is retried every couple of seconds for
                # months. Logging a full traceback each time would write hundreds of
                # thousands of them and fill the disk this system exists to manage.
                if self.failures[entry.name] <= 2:
                    logger.exception("failed to start %s", entry.name)
                elif self.failures[entry.name] % 100 == 0:
                    logger.warning(
                        "%s has failed to start %d times",
                        entry.name,
                        self.failures[entry.name],
                    )
                self._next_attempt[entry.name] = now + self._restart_delay
                continue
            # Held back: start() returned without starting anything. Nothing was
            # started, so nothing is recorded as started - no run to time, no
            # name in the list this returns, no restart counted. The attempt is
            # still spaced out and start() is still called each time, because
            # held_back expires on its own as the old failures age out of its
            # window, and that call is how the service notices.
            if held:
                self._next_attempt[entry.name] = now + self._restart_delay
                continue
            self._up_since[entry.name] = now
            started.append(entry.name)
            self._next_attempt[entry.name] = now + self._restart_delay
            if entry.name in self._started_once:
                self.restarts[entry.name] += 1
            else:
                self._started_once.add(entry.name)
        return started

    # -- what any of it means ---------------------------------------------

    def flapping(self, name: str) -> bool:
        """True when this service's last few starts have all failed to stick."""
        return self.short_lived.get(name, 0) >= FLAPPING_AFTER

    def health(self) -> dict[str, dict]:
        """Per service, whether keeping it alive is actually working.

        `tick()` returns the names it started, which is what the console read as
        "recording" while twenty recorder processes died in forty seconds. This
        answers the question that was actually being asked.
        """
        report: dict[str, dict] = {}
        for entry in self.managed:
            name = entry.name
            try:
                running = bool(entry.service.running)
            except Exception:  # noqa: BLE001 - a service that cannot say is not running
                logger.exception("could not ask %s whether it is running", name)
                running = False
            report[name] = {
                "name": name,
                "running": running,
                "restarts": self.restarts[name],
                # Starts that raised before anything was spawned at all.
                "failures": self.failures[name],
                # Starts that spawned something which then died too soon to
                # count. The recorder's whole bad day lived in this number.
                "short_lived": self.short_lived[name],
                "settled": self.settled[name],
                "flapping": self.flapping(name),
                "reason": self._reason(name, running),
            }
        return report

    def _reason(self, name: str, running: bool) -> str:
        """A sentence for the operator, or empty when there is nothing to say."""
        if self.failures[name] >= FLAPPING_AFTER and not running:
            return (
                f"it could not be started at all on {self.failures[name]} attempts "
                f"in a row; the log above says why"
            )
        if not self.flapping(name):
            return ""
        if not self.settled[name]:
            return (
                f"it has been started {self.short_lived[name]} times and has never "
                f"stayed up for {self._stable_after:.0f} seconds; starting it again "
                f"is not fixing it"
            )
        return (
            f"it has been started {self.short_lived[name]} times since it last stayed "
            f"up for {self._stable_after:.0f} seconds; starting it again is not fixing it"
        )

    def _say_if_flapping(self, name: str) -> None:
        """Say it when the picture changes, not once every couple of seconds.

        This process runs for months. A warning per tick is the same as no
        warning: it buries the one line that matters in the Logs tab.
        """
        # The throttle is the arithmetic below and nothing else. There used to
        # be a `_flapping_said` alongside it, holding the count at which this
        # last spoke - which reads like the thing deciding when to speak again,
        # and was not: it was written here, reset when a service settled, and
        # never once read. Half-wired state next to a real rule is worse than
        # no state, because the next person maintains the wrong one.
        count = self.short_lived[name]
        if count < FLAPPING_AFTER:
            return
        if count != FLAPPING_AFTER and (count - FLAPPING_AFTER) % 20 != 0:
            return
        logger.warning("%s: %s", name, self._reason(name, running=False))

    def stop_all(self) -> None:
        for entry in self.managed:
            try:
                entry.service.stop()
            except Exception:  # noqa: BLE001 - shutdown must always complete
                logger.exception("failed to stop %s", entry.name)
