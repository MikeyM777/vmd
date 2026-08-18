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

    # -- the transport ----------------------------------------------------
    #
    # Playback had no pause at all. Re-watching the same ten seconds is the
    # single most common thing anyone does with security footage, and here it
    # cost a fresh click on a bar where one pixel is over a minute of the day.
    #
    # Every one of these is a request handed to the player. Nothing here
    # retries, nothing here restarts, and nothing here has a timer: the pane
    # watches and does not intervene, and every disconnection reported from the
    # field traced back to code in this class firing on its own.
    #
    # A live stream is not seekable and cannot be paused meaningfully. The Live
    # tab never calls any of this, and libVLC's answer to a request a live
    # stream cannot honour is to ignore it.

    @property
    def paused(self) -> bool: ...

    def set_paused(self, paused: bool) -> None: ...

    @property
    def rate(self) -> float: ...

    def set_rate(self, rate: float) -> None: ...

    def position_seconds(self) -> float | None: ...

    def seek_seconds(self, seconds: float) -> bool: ...


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
        self._paused = False
        self._rate = 1.0
        self._position: float | None = None

    @property
    def state(self) -> PaneState:
        return self._state

    def show(self, url: str, at_seconds: float = 0.0) -> None:
        if self.url is not None:
            self.restarts += 1
        self.url = url
        self.at_seconds = float(at_seconds)
        self._position = max(float(at_seconds), 0.0)
        # Paused, then taken to a movement: the operator pressed a button that
        # means "show me that", and handing him a still of it is the console
        # obeying the letter of a request nobody made. The speed is not reset
        # for the opposite reason - it is a way of watching rather than a
        # property of one file, and somebody working through a night at 8x does
        # not want it undone by every seek.
        self._paused = False
        self._state = "connecting"

    def stop(self) -> None:
        self.url = None
        self._position = None
        self._paused = False
        self._state = "stopped"

    # -- the transport ----------------------------------------------------

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)

    @property
    def rate(self) -> float:
        return self._rate

    def set_rate(self, rate: float) -> None:
        if float(rate) > 0.0:
            self._rate = float(rate)

    def position_seconds(self) -> float | None:
        return self._position

    def seek_seconds(self, seconds: float) -> bool:
        if self.url is None:
            return False
        self._position = max(float(seconds), 0.0)
        return True

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


# How far behind the camera the picture runs when nothing says otherwise. The
# same number as `Settings.live_delay_ms`, repeated here rather than imported so
# that a pane built directly - by a test, by a spike tool - behaves like the
# console does. If they ever disagree, this one is the wrong one.
DEFAULT_DELAY_MS = 120

# At or below this delay, libVLC is also told to keep no clock allowance at all.
#
# Kept as a threshold on the one number the operator sets, rather than as a
# second switch, because it is the same question asked once: how far is he
# willing to go for a picture that is not behind. The steps above this are the
# ones that must simply work.
TIGHT_CLOCK_AT_OR_BELOW_MS = 50


def vlc_options(
    delay_ms: int = DEFAULT_DELAY_MS, flip: bool = False, boxes: bool = False
) -> list[str]:
    """What libVLC is started with, and every line of it is about delay.

    "Compared to the FLIR browser GUI our VMD is much later. It's unacceptable."
    It was, and there were four causes, of which the caching figure everybody
    reaches for first was the smallest.

    `--clock-jitter` is the big one, and it is the one that is no longer set for
    everybody. libVLC defaults it to 5000 - five seconds of tolerance for a
    source whose clock wanders - and pays for that tolerance in buffer. Taking
    the allowance away is most of the delay this console had against the
    camera's own web page, and it is also the only thing here that can stop a
    picture outright: with no allowance at all, a stream whose timestamps wander
    has every frame arrive at a time libVLC thinks is wrong, and it discards
    them. So it is set only at the fastest step - see TIGHT_CLOCK_AT_OR_BELOW_MS
    - because a picture 300 ms behind beats a picture that is not there.

    `--clock-synchro=0` stops libVLC trying to lock its output clock to the
    sender's, and travels with it for the same reason. Locking is right for a
    film with a soundtrack, where drift is audible; there is no audio here at
    all - `--no-audio` sees to that - and the
    only thing the lock can do to a live picture is hold frames back until the
    clock agrees they are due.

    `--live-caching` is the one that was missing. The RTSP access module reads
    the LIVE caching figure, not the network one, for a stream it considers
    live, so `--network-caching=300` on its own was being applied to a path that
    was not asking about it. Both are set to the same figure, so that whichever
    one this build consults gets the answer the operator chose.

    `--drop-late-frames` and `--skip-frames` are libVLC's defaults already and
    are not repeated. They matter to the outcome and they are not this file's to
    claim: a frame that arrives late is dropped rather than pushing everything
    behind it further back, which is what stops a delay that has happened once
    from becoming permanent.

    Hardware decoding stays, and it is the weakest of these decisions. It
    buffers a frame or two - about 40-80 ms at 25 fps - which is a real part of
    the remaining delay. It was kept because this console draws two pictures
    beside each other on a machine that also records and detects; at the FHD
    these cameras actually send, rather than the 4K assumed when this was
    written, software decoding is well within a desktop's reach and the trade is
    worth reopening. `spike/probe_delay.py` runs a pane with it off, side by
    side with one that has it on, so that is measured on the machine rather than
    argued about here.
    """
    delay = max(0, int(delay_ms))
    options = [
        # The source is on this machine, so this absorbs the desktop and nothing
        # else; the link's jitter was already absorbed by go2rtc.
        f"--network-caching={delay}",
        f"--live-caching={delay}",
        "--rtsp-tcp",  # what both VLC and go2rtc negotiate anyway
        "--no-audio",  # never listened to: one less decode, one less failure
        "--no-video-title-show",
        "--avcodec-hw=any",  # hardware decode where the machine offers it
    ]
    if boxes:
        # The box round what moved, composited by libVLC itself rather than
        # drawn over its window - see `vmd/desktop/boxes.py` for why that is the
        # only way it can be done at all. A sub-source is a subpicture, the same
        # road subtitles take, so it survives hardware decoding and fullscreen.
        #
        # Opacity 0 and a blank file to start with: the filter has to exist from
        # the moment the pane does, because it cannot be added to a running
        # instance, and a pane is built long before anything moves in front of
        # it.
        options.extend(
            [
                "--sub-source=logo",
                "--logo-x=0",
                "--logo-y=0",
                "--logo-opacity=0",
            ]
        )
    if flip:
        # libVLC's own transform filter, so the turning happens where the
        # picture is drawn and nowhere else: the recorder and the detector both
        # read the stream themselves and neither of them passes through here.
        options.extend(["--video-filter=transform", "--transform-type=180"])
    if delay <= TIGHT_CLOCK_AT_OR_BELOW_MS:
        # Only at the fastest setting, and this is a retreat from shipping them
        # to everybody.
        #
        # "The VMD is totally stuck while the FLIR GUI is working perfectly."
        # These two are the only options here that change what libVLC does with
        # a frame rather than how many it keeps, and they are the two that can
        # stop a picture outright: with no clock allowance at all, a stream
        # whose timestamps wander - which a camera re-encoding on the fly can
        # certainly produce - has every frame arrive at a time libVLC thinks is
        # wrong, and it discards them.
        #
        # They are worth having, and they are most of the delay this console had
        # against the camera's own web page. But a picture that is 300 ms behind
        # beats a picture that is not there, so they belong at the setting whose
        # name says it is the extreme one and not at the one the console starts
        # on. See LIVE_DELAY_CHOICES in `vmd/desktop/settings_tab.py`.
        options.extend(["--clock-jitter=0", "--clock-synchro=0"])
    return options


# Kept as a name because tools and tests read it. It is the default set, and
# `VlcVideoPane` builds its own when it is told a different delay.
VLC_OPTIONS = vlc_options()


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

    def __init__(
        self,
        parent: QWidget | None = None,
        delay_ms: int = DEFAULT_DELAY_MS,
        flip: bool = False,
        boxes: bool = False,
    ) -> None:
        super().__init__(parent)
        vlc = load_vlc()

        self.setStyleSheet("background: #050607;")
        self._vlc = vlc
        # Error is VLC failing; Ended is VLC finishing. On a live stream they
        # mean the same thing to an operator: the picture is gone and nothing
        # but a fresh show() will bring it back. VLC reaches Ended about ten
        # seconds after an RTSP source stops answering.
        self._gave_up = frozenset({vlc.State.Error, vlc.State.Ended})
        # Per pane, because the delay is a setting and a saved change rebuilds
        # the panes. An instance built once at import would hold whatever the
        # first console of the morning was started with.
        self._instance = vlc.Instance(vlc_options(delay_ms, flip=flip, boxes=boxes))
        self._boxes = bool(boxes)
        self._player = self._instance.media_player_new()
        self._url: str | None = None
        self._last_frame_at = 0.0
        self.frames_seen = 0
        self._last_count = -1
        # Once libVLC has been handed its player back, nothing here may touch it
        # again: that is a use-after-free in a C library, not an exception.
        self._released = False
        # The transport, held here as well as in libVLC. Held because the answer
        # has to be available the instant a button is drawn, and asking a player
        # that has no media yet gives an answer about nothing.
        self._paused = False
        self._rate = 1.0
        # Where the last show() asked to start. It is what `position_seconds`
        # answers until libVLC has opened the file and has a time of its own -
        # otherwise the readout under the picture would sit at 00:00:00 for the
        # second or two after every seek, which reads as a failed seek.
        self._asked_for = 0.0

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
        # See FakeVideoPane.show: a pane taken to a new moment starts running,
        # and the speed the operator chose survives the seek.
        self._paused = False
        self._asked_for = max(float(at_seconds), 0.0)
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
        # Re-applied to the new media rather than assumed to carry over: libVLC
        # keeps the rate on the player, but a player handed media it cannot play
        # at that rate resets it, and a console whose speed control and player
        # disagree is a console lying about how fast the footage is moving.
        self._apply_rate()
        logger.info("showing %s from %.1f s in", url, start)

    def stop(self) -> None:
        self._url = None
        self._paused = False
        if self._released:
            return
        self._player.stop()

    def video_size(self) -> tuple[int, int]:
        """The picture's own size, or (0, 0) while nothing is playing.

        The VIDEO's size and not the widget's. Detection boxes are in frame
        coordinates and libVLC composites the overlay into the frame before any
        of it is scaled to the window, so this is the size the overlay has to
        be drawn at for the box to land on the thing.
        """
        if self._released:
            return (0, 0)
        try:
            width, height = self._player.video_get_size(0)
        except Exception:  # noqa: BLE001 - a player with no picture yet
            return (0, 0)
        return (int(width or 0), int(height or 0))

    def show_overlay(self, path: str) -> bool:
        """Draw this picture over the video. Says whether libVLC was asked.

        The file has already been written by whoever called this, on a worker -
        see `vmd/desktop/boxes.py`, which measures why. All this does is name it
        and turn the opacity up, which are two calls into libVLC and cost
        nothing.
        """
        if not self._boxes or self._released:
            return False
        try:
            vlc = self._vlc
            self._player.video_set_logo_string(vlc.VideoLogoOption.logo_file, str(path))
            self._player.video_set_logo_int(vlc.VideoLogoOption.logo_x, 0)
            self._player.video_set_logo_int(vlc.VideoLogoOption.logo_y, 0)
            self._player.video_set_logo_int(vlc.VideoLogoOption.logo_opacity, 255)
            return True
        except Exception:  # noqa: BLE001 - a box may never cost the picture
            logger.exception("the box could not be put on the picture")
            return False

    def hide_overlay(self) -> bool:
        """Take the box off. Opacity rather than an empty file, because it is
        one call and cannot fail on a disk that has gone."""
        if not self._boxes or self._released:
            return False
        try:
            self._player.video_set_logo_int(self._vlc.VideoLogoOption.logo_opacity, 0)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("the box could not be taken off the picture")
            return False

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

    # -- the transport ----------------------------------------------------
    #
    # Nothing below starts a timer, retries anything, or restarts a stream. Each
    # one is a request passed straight to libVLC and an answer read back out,
    # and each is guarded, because the tests drive this class with stub players
    # and a pane that refuses to show a picture because a stub has no opinion
    # about the playback rate is a worse failure than a stub that says nothing.

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        paused = bool(paused)
        self._paused = paused
        if self._released or self._url is None:
            return
        act = getattr(self._player, "set_pause", None)
        if act is not None:
            act(1 if paused else 0)

    @property
    def rate(self) -> float:
        return self._rate

    def set_rate(self, rate: float) -> None:
        """Quarter speed to eight times. Zero is not slow motion.

        A rate of zero is a player that never advances again, wearing the face
        of a player that is running; a negative one is not something libVLC
        plays at all. Both are refused here rather than handed on.
        """
        rate = float(rate)
        if rate <= 0.0:
            return
        self._rate = rate
        if self._released:
            return
        self._apply_rate()

    def _apply_rate(self) -> None:
        act = getattr(self._player, "set_rate", None)
        if act is not None:
            act(self._rate)

    def position_seconds(self) -> float | None:
        """How far into the file the picture is, or None when nothing is on.

        libVLC answers -1 while it is still opening the media, which is not a
        position. Until it has one, this is the moment the last show() asked
        for - otherwise the clock under the picture would drop to 00:00:00 for
        a second after every seek, which reads as a seek that failed.
        """
        if self._released or self._url is None:
            return None
        reader = getattr(self._player, "get_time", None)
        if reader is None:
            return self._asked_for
        try:
            milliseconds = reader()
        except Exception:  # noqa: BLE001 - a reading, not a control
            logger.debug("the player would not say where it is", exc_info=True)
            return self._asked_for
        if milliseconds is None or milliseconds < 0:
            return self._asked_for
        return float(milliseconds) / 1000.0

    def seek_seconds(self, seconds: float) -> bool:
        """Move within the file that is already open. False means it did not.

        `set_time` is used here and deliberately not in `show`: it only takes
        effect once libVLC has the media open, which after `play()` it does not
        yet, and the ways of waiting for that are the two things this class is
        forbidden to do. By the time anything is being sought, it is playing -
        the media is open and there is nothing to wait for.

        The caller has to be told when this did not happen, because the answer
        then is to open the file again at the right moment, and a skip that
        silently did nothing is a console that has stopped following the clock
        it is drawing.
        """
        if self._released or self._url is None:
            return False
        act = getattr(self._player, "set_time", None)
        if act is None:
            return False
        target = max(float(seconds), 0.0)
        try:
            act(int(target * 1000))
        except Exception:  # noqa: BLE001 - a refused seek is not a crash
            logger.debug("the player would not move to %.1f s", target, exc_info=True)
            return False
        self._asked_for = target
        return True

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
        self._leave_the_pointer_to_qt()

    def _leave_the_pointer_to_qt(self) -> None:
        """Stop libVLC from taking the mouse and the keyboard off this widget.

        Handed an HWND, libVLC does not draw into it: it creates a child window
        of its own inside it - class name `VLC video output ...` - and that
        window sits above the Qt widget and owns every point of the picture.
        Measured with `WindowFromPoint` over a playing pane: by default the
        window under the pointer is libVLC's, so no mouse event over the video
        ever reaches Qt at all. That is why the steering used to need a
        transparent widget laid over the whole wall to catch drags.

        With mouse input given back, the same measurement returns the pane's own
        Qt window, and an event filter on the pane sees the drags directly. The
        keyboard goes back for the same reason and a second one: libVLC's own
        shortcuts are bound to keys this console steers with, and a camera that
        slews because VLC also thought the arrow key was for it is a hazard.

        Guarded rather than assumed: the tests drive this class with stub
        players, and a pane that refuses to show a picture because a stub has no
        opinion about the mouse is a worse failure than a stub that never
        answers the question.
        """
        for question, answer in (
            ("video_set_mouse_input", False),
            ("video_set_key_input", False),
        ):
            act = getattr(self._player, question, None)
            if act is not None:
                act(answer)

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
