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
from typing import Any, Callable, Literal, Protocol, runtime_checkable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from vmd.desktop.libvlc import RECORDING, RESTART, VlcUnavailable, prepare

logger = logging.getLogger(__name__)

PaneState = Literal["stopped", "connecting", "playing", "late", "failed"]

# A stream that has produced nothing for this long is reported as late. It is
# not touched: this number exists to put a word on the screen, not to trigger
# anything.
LATE_AFTER_SECONDS = 8.0


@runtime_checkable
class VideoPane(Protocol):
    """Anything that can show one stream, optionally from part-way in.

    `at_seconds` is how far into the media to start, and it has a default so
    that everything which only ever shows a live stream is unchanged. It exists
    for Playback: an operator who clicks 14:32 on the timeline was being given
    the file containing 14:32 played from its beginning, which with a
    five-minute segment is up to five minutes from the moment they asked for.
    A timeline that lands you minutes from the event is not playback.

    A live stream is not seekable and the demuxer ignores the request, so
    nothing has to know which kind it is holding.
    """

    def show(self, url: str, at_seconds: float = 0.0) -> None: ...

    def stop(self) -> None: ...

    @property
    def state(self) -> PaneState: ...


class FakeVideoPane:
    """A pane with no video in it, for testing everything that uses one."""

    def __init__(self) -> None:
        self.url: str | None = None
        self.restarts = 0
        self.released = False
        # Where the last `show` was asked to start. Recorded rather than acted
        # on, so a widget test can assert what the console asked for without
        # libVLC being installed - and so the assertion is about the console's
        # arithmetic, which is the part a unit test can actually settle.
        self.at_seconds = 0.0
        self._state: PaneState = "stopped"

    @property
    def state(self) -> PaneState:
        return self._state

    def show(self, url: str, at_seconds: float = 0.0) -> None:
        if self.url is not None:
            self.restarts += 1
        self.url = url
        self.at_seconds = float(at_seconds)
        self._state = "connecting"

    def stop(self) -> None:
        self.url = None
        self._state = "stopped"

    def release(self) -> None:
        self.url = None
        self.released = True
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


def _import_vlc() -> Any:
    import vlc  # imported here so the module imports without libVLC present

    return vlc


def load_vlc(
    prepare: Callable[[], Any] = prepare,
    import_vlc: Callable[[], Any] = _import_vlc,
) -> Any:
    """Find VLC, then import python-vlc against what was found.

    The order is the whole point: python-vlc looks for the library once, while
    it is being imported, and lives with whatever it concluded for the rest of
    the process. By the time it runs it is being told where to look.

    Whatever comes back out is turned into one sentence. python-vlc answers a
    library it cannot load with `sys.exit(1)` - inside a widget's constructor,
    at start-up, where `SystemExit` walks straight past the guard that keeps the
    console open when there is no video. A missing picture must never be a
    missing window.
    """
    found = prepare()
    where = f" in {found.folder}" if found is not None else " on this machine"
    try:
        return import_vlc()
    # SystemExit is python-vlc's own answer to a library it cannot load, and it
    # is not an Exception: it walks straight past the guard that keeps the
    # console open when there is no video, and takes the whole window with it.
    except (SystemExit, ImportError, OSError) as exc:
        logger.exception("the VLC%s would not load", where)
        raise VlcUnavailable(
            f"The VLC{where} would not start. Install VLC for Windows again. "
            f"{RESTART} {RECORDING}"
        ) from exc


class VlcVideoPane(QWidget):
    """A widget libVLC draws into."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        vlc = load_vlc()

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
        self._last_frame_at = 0.0
        self.frames_seen = 0
        self._last_count = -1
        # Once libVLC has been handed its player back, nothing here may touch it
        # again: that is a use-after-free in a C library, not an exception.
        self._released = False

        # Polled rather than driven by VLC events: libVLC delivers events on its
        # own threads, and touching Qt from those is how a UI toolkit crashes.
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._sample)
        self._poll.start(250)

    def show_widget(self) -> None:
        """Realise the widget so it has a window handle for VLC to draw into."""
        super().show()

    def show(self, url: str, at_seconds: float = 0.0) -> None:  # noqa: A003
        """Play this, from the beginning or from part-way in.

        The position is a media option and not a `set_time` after `play()`, and
        that is the whole of the difficulty. `set_time` only takes effect once
        libVLC has the media open and playing; called straight after `play()` on
        a file that has not been opened yet it is silently dropped, and the
        alternatives are both things this file is forbidden to do - poll on the
        GUI thread until the state turns, or set a timer to try again later.
        Every disconnection ever reported from the field traced back to a timer
        in this class firing early, which is why there is no timer here.

        `:start-time` is read by the demuxer while the media is being opened, so
        there is nothing to wait for and nothing to retry: by the time anything
        is playing it is already playing from the right place. It is ignored by
        a live stream, which is not seekable, so the Live tab needs to know
        nothing about any of this.
        """
        if self._released:
            logger.warning("not showing %s: this pane has been released", url)
            return
        self._url = url
        self._last_frame_at = 0.0
        self._last_count = -1
        self.frames_seen = 0
        media = self._instance.media_new(url)
        # Never negative: libVLC reads this as a float and a negative one is not
        # a position in any file. The console clamps too; this is the floor
        # under whatever reaches here.
        start = max(float(at_seconds), 0.0)
        if start > 0.0:
            media.add_option(f":start-time={start:.3f}")
        self._player.set_media(media)
        self._attach_surface()
        self._player.play()
        logger.info("showing %s from %.1f s in", url, start)

    def stop(self) -> None:
        self._url = None
        if self._released:
            return
        self._player.stop()

    def release(self) -> None:
        """Hand libVLC back what it gave out. The pane is finished after this.

        python-vlc frees nothing when its objects are collected - neither
        `Instance` nor `MediaPlayer` defines `__del__` - so dropping a pane
        leaves a player, its decoder threads and a whole libVLC instance alive
        for the rest of the process. The panes are rebuilt whenever the streams
        change, and each rebuild would otherwise leave another instance behind
        on a machine that runs for months.

        Everything here is guarded and done once. A second release is a double
        free inside a C library, which is not an exception - it is the console
        disappearing.
        """
        if self._released:
            return
        self._released = True
        self._poll.stop()
        self._url = None
        for name, obj in (("player", self._player), ("instance", self._instance)):
            for step in ("stop", "release"):
                action = getattr(obj, step, None)
                if action is None:
                    continue
                try:
                    action()
                except Exception:  # noqa: BLE001 - a leak beats a crash on shutdown
                    logger.exception("the video %s would not %s", name, step)

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
