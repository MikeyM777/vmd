"""A slow question, asked on a worker, answered from what the worker left behind.

This is `DiskWatcher`'s mechanism with the folder taken out of it, because the
rule `vmd/desktop/disk.py` opens with is not about the disk:

> Every question here touches the filesystem, and the filesystem is exactly what
> is broken in the cases that matter - a disconnected drive can leave a stat call
> blocked for many seconds. So none of it runs on the GUI thread and none of it
> runs on the two-second heartbeat.

Three questions in this console are the same shape, against the same folder: how
full it is, what the detector last published into it, and what has moved. Two of
those were being asked on the GUI thread on the two-second heartbeat, so the
dead-drive case `disk.py` exists to report was also the case in which the
console froze every two seconds - which is the one moment it may not.

The rules, and they are the rules `vmd/background.py` states for the same
intent:

* **the caller never waits.** `poll()` starts a reading if one is due and
  returns; `value` is whatever the last one left behind.
* **one reading at a time.** A `stat` blocked on a drive that is not there must
  not queue one worker per heartbeat behind it.
* **the answer carries its age**, so nothing downstream can present a reading
  taken four minutes ago as the state of the world now.
* **the worker is a daemon and is never joined.** These outlive windows on
  purpose, and a closing console may not wait on a call that is blocked.

Not a QObject and not tied to a thread pool, so it can be built and driven by
`ConsoleServices`, which has no window and no event loop, and driven
synchronously by a test that must never wait on a thread - which is what the
`executor` seam is for.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Watched(Generic[T]):
    """One question, asked at most every `every` seconds, always on a worker."""

    def __init__(
        self,
        read: Callable[[], T],
        every: float,
        executor: Callable[[Callable[[], None]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        name: str = "reading",
    ) -> None:
        self._read = read
        self._every = every
        self._name = name
        self._executor = executor or _daemon_thread(name)
        self._clock = clock
        self._lock = threading.Lock()
        self._value: T | None = None
        self._answered_at: float | None = None
        self._last_started: float | None = None
        self._in_flight = False
        # The last failure that was written to the log. This runs for months on
        # a machine whose only diagnostic is a 500-line ring, and a question
        # that fails every time it is asked - a database that will not open, a
        # drive that has gone - would evict everything explaining the fault it
        # is a symptom of. Said when it starts, and again when it changes.
        self._said = ""

    @property
    def value(self) -> T | None:
        """What the last reading left behind, or None if there has not been one."""
        with self._lock:
            return self._value

    def age(self) -> float | None:
        """How long ago that answer arrived, or None if none ever has.

        None is a different thing from a value of None, and the distinction is
        the point: a console that draws the same thing for "nobody has managed
        to ask yet" and "the answer is nothing" has said the same thing about
        two different situations, and one of them is fine.
        """
        with self._lock:
            if self._answered_at is None:
                return None
            return max(0.0, self._clock() - self._answered_at)

    def again(self) -> None:
        """Ask again at once. The question is about something else now.

        What is already known is kept until the new answer arrives, because
        dropping it would replace a reading that is merely out of date with no
        reading at all, and the panels draw those differently.
        """
        with self._lock:
            self._last_started = None

    def forget(self) -> None:
        """Drop what is known and ask again at once.

        For when the last answer was about something else - the recordings
        folder moved, so the detector's report at the old path is a file nobody
        writes any more. Different from `again`, which keeps the old answer
        because it is still an answer to the same question, only an old one.
        """
        with self._lock:
            self._value = None
            self._answered_at = None
            self._last_started = None

    def poll(self) -> None:
        """Called from the heartbeat. Starts a reading if one is due."""
        with self._lock:
            if self._in_flight:
                return
            now = self._clock()
            if self._last_started is not None and now - self._last_started < self._every:
                return
            self._last_started = now
            self._in_flight = True

        def work() -> None:
            value: T | None = None
            problem: BaseException | None = None
            try:
                value = self._read()
            except Exception as exc:  # noqa: BLE001 - an unanswered question, not a crash
                problem = exc
            said = "" if problem is None else f"{type(problem).__name__}: {problem}"
            with self._lock:
                self._value = value
                self._answered_at = self._clock()
                self._in_flight = False
                fresh = said != self._said
                self._said = said
            if problem is not None and fresh:
                logger.error("%s could not be read: %s", self._name, said, exc_info=problem)

        self._executor(work)


def _daemon_thread(name: str) -> Callable[[Callable[[], None]], None]:
    def start(work: Callable[[], None]) -> None:
        threading.Thread(target=work, name=f"vmd-{name}", daemon=True).start()

    return start
