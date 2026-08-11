"""Reading something slow without stopping the thread that draws the window.

Three things this console has to know are answered by something that can take
seconds: the radio, which needs a login before it will say anything about the
link; whether a process adopted from an earlier console is still there, which on
Windows means shelling out to `tasklist`; and the camera. All three were asked
on the Qt thread, on a timer, and all three take longest at precisely the moment
they matter - when the network is down. While any of them blocked, nothing
repainted, the supervisor did not tick, and the alarm strip could not appear.

So they are asked here instead. The rules are the same for every one of them:

* the caller never waits - `get` returns what is known now and starts a fresh
  read if what is known has gone stale;
* one read at a time - a second caller does not queue another worker behind the
  first, which is what turns a slow answer into an unbounded pile of threads;
* the answer carries its age, so nothing downstream can present a reading taken
  four minutes ago as the state of the world now;
* the thread is a daemon and every wait on it is bounded, because these outlive
  windows and must never hold a closing console open. Nothing here uses
  `concurrent.futures`: its atexit hook joins worker threads at interpreter
  exit, which is how a console that had already closed its window went on to
  hang.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

# How long a closing caller waits for a reader to finish. The thread is a
# daemon, so abandoning it costs nothing past this point.
CLOSE_SECONDS = 2.0

T = TypeVar("T")


@dataclass(frozen=True)
class Reading(Generic[T]):
    """A value, how old it is, and whether another one is on its way.

    `age` is None when nothing has ever been read successfully - which is a
    different thing from a value of None, and the distinction is the whole
    point. A console that shows nothing for "we have not managed to ask yet"
    and nothing for "the answer is nothing" has told its operator the same
    thing about two different situations, and one of them is fine.
    """

    value: T | None
    age: float | None
    pending: bool

    @property
    def known(self) -> bool:
        return self.age is not None

    def older_than(self, limit: float) -> bool:
        """Older than `limit` seconds, or never taken at all."""
        return self.age is None or self.age > limit


class BackgroundValue(Generic[T]):
    """One slow question, asked on a thread of its own and never on yours.

    `read` must be cheap to call wrongly: it is called from a worker, its result
    is simply stored, and an exception from it is caught and treated as "the
    question could not be answered this time" rather than as an answer. The
    previous value is kept and goes on ageing, so a caller can tell a reading
    that is old from one that is missing.
    """

    def __init__(
        self,
        read: Callable[[], T],
        stale_after: float,
        name: str = "reading",
        seed: T | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read = read
        self._stale_after = stale_after
        self._name = name
        self._clock = clock
        self._lock = threading.Lock()
        self._value: T | None = seed
        # A seed is something the caller has just seen for itself - the console
        # checks a PID once, synchronously, at the moment it adopts a child -
        # and it counts as a reading taken now rather than as nothing.
        self._taken_at: float | None = clock() if seed is not None else None
        self._pending = False
        self._wake = threading.Event()
        self._closing = threading.Event()
        # Never taken while `_lock` is held: starting a thread must not happen
        # while the reader might be waiting for that same lock.
        self._threading = threading.Lock()
        self._thread: threading.Thread | None = None

    def get(self) -> Reading[T]:
        """What is known now, and a fresh read started if it has gone stale."""
        wake = False
        with self._lock:
            age = None if self._taken_at is None else max(0.0, self._clock() - self._taken_at)
            if (age is None or age >= self._stale_after) and not self._pending:
                if not self._closing.is_set():
                    self._pending = True
                    wake = True
            reading = Reading(value=self._value, age=age, pending=self._pending)
        if wake:
            self._ensure_thread()
            self._wake.set()
        return reading

    def invalidate(self) -> None:
        """Forget what was read: the question is about something else now."""
        with self._lock:
            self._value = None
            self._taken_at = None

    def close(self, timeout: float = CLOSE_SECONDS) -> bool:
        """Let the reader go. Bounded, always, and never raises."""
        self._closing.set()
        self._wake.set()
        with self._threading:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        if thread.is_alive():
            logger.warning("%s is still being read; letting the reader go", self._name)
            return False
        return True

    # ------------------------------------------------------------- the reader

    def _ensure_thread(self) -> None:
        with self._threading:
            if self._closing.is_set():
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name=f"{self._name}-reader", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                wanted = self._pending
            if wanted:
                self._refresh()
                continue
            if self._closing.is_set():
                return
            self._wake.wait()
            self._wake.clear()

    def _refresh(self) -> None:
        try:
            value = self._read()
        except Exception:  # noqa: BLE001 - an unanswerable question, not a failure
            logger.exception("%s could not be read", self._name)
            # Nothing is written, and that is the whole of the class docstring's
            # promise: "the previous value is kept and goes on ageing, so a
            # caller can tell a reading that is old from one that is missing".
            #
            # It used to store None and stamp it as taken now. Both halves are
            # wrong and the second is the dangerous one, because every age
            # downstream is measured from that stamp. What it cost is
            # `ChildProcess.liveness_age` - the check that stops the console
            # inventing health about a recorder adopted from an earlier run. A
            # `tasklist` that ERRORS rather than hangs reset the age to zero on
            # every attempt, so the age never grew and the check could never
            # fire, and the console went on reporting an adopted recorder as
            # healthy on the strength of a question nobody had answered for an
            # hour.
            #
            # This does not turn into a retry loop: `_pending` is only ever set
            # by `get`, so the rate is the caller's heartbeat either way.
            with self._lock:
                self._pending = False
            return
        with self._lock:
            self._value = value
            # Stamped when the read finished, not when it started. A read that
            # took longer than the staleness window would otherwise be expired
            # the moment it was written, and every caller would start another
            # one straight away - which is how a slow answer becomes a
            # permanent one, and what the radio's cache got wrong before.
            self._taken_at = self._clock()
            self._pending = False
