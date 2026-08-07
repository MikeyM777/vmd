"""Keeps services alive. One failing service must never affect another."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Protocol

logger = logging.getLogger(__name__)


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
                started_at = self._up_since.get(entry.name)
                if started_at is not None and now - started_at >= self._stable_after:
                    self.failures[entry.name] = 0
                continue
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
            self._up_since[entry.name] = now
            started.append(entry.name)
            self._next_attempt[entry.name] = now + self._restart_delay
            if entry.name in self._started_once:
                self.restarts[entry.name] += 1
            else:
                self._started_once.add(entry.name)
        return started

    def stop_all(self) -> None:
        for entry in self.managed:
            try:
                entry.service.stop()
            except Exception:  # noqa: BLE001 - shutdown must always complete
                logger.exception("failed to stop %s", entry.name)
