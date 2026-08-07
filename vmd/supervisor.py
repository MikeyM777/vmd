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
    ) -> None:
        self.managed = managed
        self.restarts: dict[str, int] = {entry.name: 0 for entry in managed}
        self._clock = clock
        self._restart_delay = restart_delay
        self._next_attempt: dict[str, float] = {entry.name: 0.0 for entry in managed}
        self._started_once: set[str] = set()

    def tick(self) -> list[str]:
        """Check every service, start whatever is down. Returns the names started."""
        started: list[str] = []
        now = self._clock()
        for entry in self.managed:
            if entry.service.running:
                continue
            if now < self._next_attempt[entry.name]:
                continue
            try:
                entry.service.start()
            except Exception:  # noqa: BLE001 - one bad service must not stop the others
                logger.exception("failed to start %s", entry.name)
                self._next_attempt[entry.name] = now + self._restart_delay
                continue
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
