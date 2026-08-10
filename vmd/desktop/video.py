"""Showing a stream, and saying what it is doing - nothing more.

The pane watches; it does not intervene. VLC recovers from its own trouble far
better than the timers that used to sit here, and every disconnection reported
from the field traced back to one of those timers firing early. A stream is
restarted when VLC reports an error, or when the operator changes it. Never
because a frame was late.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

PaneState = Literal["stopped", "connecting", "playing", "late", "failed"]

# A stream that has produced nothing for this long is reported as late. It is
# not touched: this number exists to put a word on the screen, not to trigger
# anything.
LATE_AFTER_SECONDS = 8.0


@runtime_checkable
class VideoPane(Protocol):
    """Anything that can show one stream."""

    def show(self, url: str) -> None: ...

    def stop(self) -> None: ...

    @property
    def state(self) -> PaneState: ...


class FakeVideoPane:
    """A pane with no video in it, for testing everything that uses one."""

    def __init__(self) -> None:
        self.url: str | None = None
        self.restarts = 0
        self._state: PaneState = "stopped"

    @property
    def state(self) -> PaneState:
        return self._state

    def show(self, url: str) -> None:
        if self.url is not None:
            self.restarts += 1
        self.url = url
        self._state = "connecting"

    def stop(self) -> None:
        self.url = None
        self._state = "stopped"

    # -- test control -----------------------------------------------------
    def pretend_playing(self) -> None:
        self._state = "playing"

    def pretend_late(self) -> None:
        self._state = "late"

    def pretend_failed(self) -> None:
        self._state = "failed"
