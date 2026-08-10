"""Showing a stream, and saying what it is doing - nothing more.

The pane watches; it does not intervene. VLC recovers from its own trouble far
better than the timers that used to sit here, and every disconnection reported
from the field traced back to one of those timers firing early. A stream is
restarted when VLC reports an error, or when the operator changes it. Never
because a frame was late.
"""

from __future__ import annotations

import logging
import time
from typing import Literal, Protocol, runtime_checkable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

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


VLC_OPTIONS = [
    # The source is on this machine, so this absorbs the laptop and nothing
    # else; the link's jitter was already absorbed by go2rtc.
    "--network-caching=300",
    "--rtsp-tcp",  # what both VLC and go2rtc negotiate anyway
    "--no-audio",  # never listened to: one less decode, one less failure
    "--no-video-title-show",
    "--avcodec-hw=any",  # hardware decode where the laptop offers it
]


class VlcVideoPane(QWidget):
    """A widget libVLC draws into."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        import vlc  # imported here so the module imports without libVLC present

        self.setStyleSheet("background: #050607;")
        self._vlc = vlc
        # Error is VLC failing; Ended is VLC finishing. On a live stream they
        # mean the same thing to an operator: the picture is gone and nothing
        # but a fresh show() will bring it back. VLC reaches Ended about ten
        # seconds after an RTSP source stops answering.
        self._gave_up = frozenset({vlc.State.Error, vlc.State.Ended})
        self._instance = vlc.Instance(VLC_OPTIONS)
        self._player = self._instance.media_player_new()
        self._url: str | None = None
        self._started_at = 0.0
        self._last_frame_at = 0.0
        self.frames_seen = 0
        self._last_count = -1

        # Polled rather than driven by VLC events: libVLC delivers events on its
        # own threads, and touching Qt from those is how a UI toolkit crashes.
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._sample)
        self._poll.start(250)

    def show_widget(self) -> None:
        """Realise the widget so it has a window handle for VLC to draw into."""
        super().show()

    def show(self, url: str) -> None:  # noqa: A003 - the protocol's name
        self._url = url
        self._started_at = time.monotonic()
        self._last_frame_at = 0.0
        self._last_count = -1
        self.frames_seen = 0
        media = self._instance.media_new(url)
        self._player.set_media(media)
        self._attach_surface()
        self._player.play()
        logger.info("showing %s", url)

    def stop(self) -> None:
        self._url = None
        self._player.stop()

    @property
    def state(self) -> PaneState:
        if self._url is None:
            return "stopped"
        if self._player.get_state() in self._gave_up:
            return "failed"
        if self._last_frame_at == 0.0:
            return "connecting"
        if time.monotonic() - self._last_frame_at > LATE_AFTER_SECONDS:
            return "late"
        return "playing"

    def _attach_surface(self) -> None:
        handle = int(self.winId())
        if hasattr(self._player, "set_hwnd"):
            self._player.set_hwnd(handle)
        else:  # pragma: no cover - not the deployment platform
            self._player.set_xwindow(handle)

    def _sample(self) -> None:
        """Count decoded frames. This is the only truth about whether a picture
        is arriving: VLC's state says Playing long after the pictures stop."""
        if self._url is None:
            return
        # Tearing an input down flushes the decoder into the display: measured
        # on a killed RTSP source, displayed_pictures leapt from 58 to 202 in
        # one sample as VLC went to Ended. Those are not frames arriving, and
        # crediting them would have the pane claim to be playing a stream that
        # had been dead for ten seconds.
        if self._player.get_state() != self._vlc.State.Playing:
            return
        stats = self._vlc.MediaStats()
        media = self._player.get_media()
        if media is None or not media.get_stats(stats):
            return
        count = int(stats.displayed_pictures)
        # A count of zero is not a picture. Without this the first sample after
        # play() would move off -1 and the pane would claim to be playing a
        # stream that has shown nothing.
        if count <= 0:
            return
        if count != self._last_count:
            self._last_count = count
            self.frames_seen = count
            self._last_frame_at = time.monotonic()
