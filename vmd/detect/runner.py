"""Reading one stream and turning what moves in it into rows.

This is the only file in the package that opens anything. Everything it depends
on - the pipeline, the filters, the tracker - is arithmetic, and everything it
talks to - the capture, the clock, the store - is injected, so the whole of it
is testable without a camera, a socket or a second of real time.

The rule this file exists to keep: **it does not die.** A bad frame, a broken
pipeline, a locked database, a stream that never opens - each of those is a
logged sentence and another turn round the loop. A detector that has silently
stopped is worse than no detector, because the operator cannot see the
difference.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from typing import Callable, Sequence

import numpy as np

from vmd.detect.classify import UNNAMED, NullClassifier, named
from vmd.detect.mask import mask_from_areas
from vmd.detect.events import EventStore
from vmd.detect.pipeline import DetectionConfig, DetectionPipeline

logger = logging.getLogger(__name__)

# How many reads may come back empty before the capture is considered dead.
# A dropped frame is normal on a radio link; forty of them in a row is a socket
# that died without closing, which ffmpeg and OpenCV both report as silence.
DEFAULT_MAX_READ_FAILURES = 40

# Reopen backoff. Starts at a second so a stream that blinked comes back almost
# at once, and stops at half a minute so a camera that is off for the night is
# not being dialled continuously.
DEFAULT_REOPEN_DELAY = 1.0
DEFAULT_MAX_REOPEN_DELAY = 30.0

# How long the loop waits after a pass that produced no frame. Without it, a
# stream that is down becomes a busy loop on a machine that is also decoding
# video for the screen.
DEFAULT_IDLE_SLEEP = 0.2

# How many frame timestamps to remember, so an event can be dated from the frame
# the track *started* on rather than the frame it was confirmed on. A few
# seconds at any frame rate, and bounded, because this process runs for months.
FRAME_TIME_HISTORY = 512

# How many frames must have arrived before the delivered frame rate is worth
# quoting. Fewer than this and one slow frame on a radio link is the whole
# measurement.
MIN_FRAMES_TO_MEASURE_RATE = 16

# How coarsely a frame is looked at when asking whether there is a picture in
# it. Every sixteenth row and column of a 1080p frame is 68x120 values, which
# costs microseconds and is far more than enough to tell a photograph from a
# flat rectangle. It has to be cheap: it runs on every frame of every stream on
# a laptop that is also decoding video for the screen.
FRAME_SAMPLE_STRIDE = 16

# How much lighter the lightest sampled value may be than the darkest before the
# frame is allowed to count as a picture. Two, not zero, because a decoder can
# put a dither of one level onto a frame that is otherwise flat. No real view of
# anything - not even a wall at night, which carries sensor noise - is this
# uniform.
BLANK_SPREAD = 2

# How many blank frames in a row before it is called out. A single flat frame
# happens at a keyframe boundary or when the camera's own auto-exposure gives
# up for an instant; ten in a row is a stream with nothing in it.
BLANK_FRAMES_BEFORE_BLIND = 10

# How many attempts on one address must produce no frame before the other
# address is tried. Three, because one failure is a streaming server that has
# not finished coming up and is not a wrong address - and going to the camera
# directly costs the radio link a second copy of the stream, which is the whole
# thing the local server exists to avoid.
#
# An attempt that produced no frame is either an open that failed or an open
# that succeeded and then delivered nothing until the read-failure rule dropped
# it. Both are the same fact about the address - there is no picture down it -
# and counting only the first meant a server that accepted every connection and
# served nothing was never given up on.
OPEN_FAILURES_BEFORE_FALLBACK = 3

# How long the detector reads the camera directly before asking whether the
# local streaming server is back.
#
# The fallback used to be one-way: it rotated on failure and never on success,
# so a single go2rtc restart - which the console performs on every material
# settings change - left detection pulling the camera across a >15 km, ~5 Mb/s
# radio link for the life of the process, beside go2rtc's own pull. The link
# barely carries one copy. Losing the live picture is the failure this whole
# system exists not to have.
#
# Two minutes: long enough that a go2rtc which is still coming up is not handed
# detection and asked to drop it again, short enough that the doubled link cost
# is measured in minutes rather than months.
DEFAULT_RETURN_AFTER = 120.0

# The longest that wait may grow to when going back keeps not working. Ten
# minutes, which is far shorter than a backoff would normally climb to: every
# minute spent on the camera is a minute the link is carrying the stream twice,
# so the cost of asking too often is one refused connection on 127.0.0.1 and
# the cost of asking too rarely is the thing being fixed.
DEFAULT_MAX_RETURN_DELAY = 600.0

# How long a return to the local server has to last, delivering frames, before
# it counts as having worked - the settled period, the same shape as the
# supervisor's `stable_after` and the live panes' "forgiven after five good
# readings". A return that ended sooner than this was not a recovery, it was a
# flap, and the next attempt waits twice as long.
#
# Five minutes, because giving up on an address is itself slow: the read-failure
# rule wants forty silent reads and the reopen ladder climbs to half a minute,
# so a couple of minutes on an address proves nothing either way.
DEFAULT_SETTLED_AFTER = 300.0

# How many failed attempts to go back are spelled out, and how often they are
# mentioned after that. This process runs for months, and at one attempt every
# two minutes an unthrottled line would own the 500-line ring the operator
# reads within a day. The same shape as the console's own restart throttle.
RETURN_TRIES_SPELLED_OUT = 1
RETURN_TRIES_BETWEEN_REMINDERS = 30


# A password inside a URL. The same expression as `vmd.desktop.logs`, copied
# rather than imported because importing that module would pull Qt into the
# detector process - which is the one process on this machine that must be able
# to run on a laptop with no window system at all. Every part is length-bounded
# for the reason given there: an unbounded prefix backtracks across a very long
# line at every position in it.
_CREDENTIALS = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]{0,15}://[^\s/@:]{1,256}):([^\s/@]{0,256})@")


def without_credentials(text: str) -> str:
    """The same text with any password inside a URL taken out of it.

    RTSP carries the camera's credentials in the address, and when go2rtc is
    down this process falls back to the camera's own URL - so the line saying
    which stream it is reading would otherwise put the camera's password on the
    operator's screen, and in any photograph of it.

    The username is kept. Which account was refused is half the diagnosis of a
    401, and it is not the secret.
    """
    if "://" not in text or "@" not in text:
        return text
    return _CREDENTIALS.sub(r"\1:****@", text)


# What OpenCV's FFmpeg backend is told before it opens an RTSP stream.
#
# "After a couple of seconds it beeped." Part of that is here: FFmpeg buffers a
# stream on the way in, and every one of those buffered frames is a frame the
# detector will look at some seconds after the thing it shows has finished
# happening. The console's own picture had the same fault in a different library
# and it is fixed there - see `vmd/desktop/video.py:vlc_options` - and this is
# the same fix on the path that decides when the room is told.
#
#   rtsp_transport tcp   what go2rtc serves and what VLC negotiates anyway.
#                        Named rather than left to FFmpeg's UDP-first probe,
#                        which costs a second before it gives up.
#   fflags nobuffer      do not fill a buffer before handing over the first
#                        frame. This is the one that matters.
#   flags low_delay      do not hold frames back waiting for reordering.
#   reorder_queue_size 0 the RTSP demuxer's own version of the same thing.
#   max_delay 500000     half a second, in microseconds, rather than FFmpeg's
#                        default of ten times that.
LOW_LATENCY_CAPTURE = (
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|reorder_queue_size;0|max_delay;500000"
)

# How far behind the live picture the detector may fall before it starts
# throwing frames away, and the most it will throw away at once.
#
# A stream that has fallen behind never catches up on its own: `read()` hands
# over the OLDEST buffered frame, not the newest, so a detector that cannot keep
# up with 25 fps of 4K does not drop to a slower rate - it drops further behind,
# every second, for as long as it runs. The beep is then late by however long it
# has been running, which is the worst shape a fault can have on a system nobody
# restarts for months.
MAX_FRAMES_TO_SKIP = 120


def open_capture_cv2(url: str):
    """Open a stream with OpenCV. Returns None if it did not open.

    Imported lazily so that importing this module - which the console does, to
    read state - does not pull OpenCV into a process that only wants a
    dataclass.

    Tried twice for a live stream: once asking FFmpeg not to buffer, and if that
    does not open, once without asking. `fflags nobuffer` is the right setting
    for a detector and it is not free - it can leave a stricter demuxer without
    the parameter sets it wanted before the first keyframe - and a detector that
    will not open at all is very much worse than one that is a second late.
    """
    import os

    import cv2

    live = url.lower().startswith(("rtsp://", "rtsps://"))
    if live:
        # An environment variable rather than an argument, because that is the
        # only seam OpenCV offers into the FFmpeg backend. Set immediately
        # before the open and left set: this process opens nothing else.
        was = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = LOW_LATENCY_CAPTURE
        try:
            capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        finally:
            if was is None:
                os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
            else:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = was
        if capture.isOpened():
            _ask_for_the_shortest_queue(capture)
            return capture
        capture.release()
        logger.warning(
            "%s would not open without buffering; opening it the ordinary way, "
            "which costs about a second of delay before an alarm",
            without_credentials(url),
        )

    capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not capture.isOpened():
        capture.release()
        return None
    if live:
        _ask_for_the_shortest_queue(capture)
    return capture


def _ask_for_the_shortest_queue(capture) -> None:
    """Ask the backend to keep one frame rather than a queue of them.

    Honoured by some backends and quietly ignored by others, FFmpeg among them
    on most builds - which is why `_catch_up` exists and does not depend on
    this. It costs one call and removes the buffer outright where it works.
    """
    try:
        import cv2

        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:  # noqa: BLE001 - a hint that was refused is not a failure
        logger.debug("this capture would not be asked for a shorter queue", exc_info=True)


def reported_fps(capture) -> float | None:
    """What the stream says its frame rate is, if it says anything believable.

    Asked of the capture rather than measured, because this is needed for a
    different question than `StreamDetector.fps` answers. That one is how fast
    this detector is PROCESSING, which is what the confirmation rule counts in;
    this is how fast frames are ARRIVING, which is what says how many of them
    piled up while the last one was being looked at.
    """
    try:
        import cv2

        value = float(capture.get(cv2.CAP_PROP_FPS))
    except Exception:  # noqa: BLE001 - a capture that will not say is not a fault
        return None
    # A file says 25 and a camera usually does too; a stream that has not worked
    # it out yet says 0, and some say 90000 - the RTP clock rate wearing a frame
    # rate's clothes. Anything outside what a camera can actually produce is not
    # an answer.
    if not 1.0 <= value <= 240.0:
        return None
    return value


class StreamDetector:
    """One stream: open it, read it, feed the pipeline, write what it finds.

    One instance per stream, run on its own thread. It owns its `EventStore`,
    because a sqlite connection belongs to the thread that made it.
    """

    def __init__(
        self,
        url: str,
        stream_name: str,
        config: DetectionConfig | None = None,
        store: EventStore | None = None,
        open_capture: Callable = open_capture_cv2,
        pipeline=None,
        ignore_regions: Sequence[Sequence[int]] = (),
        # The areas he drew round, alongside the ones he boxed. Both, because a
        # settings file written before the drawing tool existed carries
        # rectangles and nothing he already marked out may quietly come back.
        ignore_shapes: Sequence[Sequence[Sequence[int]]] = (),
        shape_sizes: Sequence[Sequence[int]] = (),
        classifier=None,
        clock: Callable[[], float] = time.time,
        # Deliberately a second clock. `clock` stamps events, so it has to be
        # the wall clock the operator reads. The reopen schedule is a duration
        # and must not be: this laptop is offline, its clock is set by hand,
        # and a correction of an hour backwards measured on the wall clock
        # leaves a dead stream untouched for that hour.
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_read_failures: int = DEFAULT_MAX_READ_FAILURES,
        reopen_delay: float = DEFAULT_REOPEN_DELAY,
        max_reopen_delay: float = DEFAULT_MAX_REOPEN_DELAY,
        idle_sleep: float = DEFAULT_IDLE_SLEEP,
        # The other way to the same picture, usually the camera itself when
        # `url` is the local streaming server. Whether to read through that
        # server is decided once, from a port answering, and the answer was
        # then kept for the life of the process: a server that had restarted
        # elsewhere, or one belonging to an older settings file, meant every
        # open failed for ever while the camera was reachable throughout and
        # was never tried again. Detection off, permanently, with a reason in
        # the status file that names the wrong thing.
        fallback_url: str = "",
        # What `url` is, in the operator's words: the local streaming server,
        # or the camera itself. Only needed when there is no `fallback_url` to
        # name the camera - a stream read straight from the camera because no
        # streaming server was found is on the camera from its first frame, and
        # costs the radio link exactly what one that fell back to it costs. The
        # console has to be able to say so: a detector silently costing double
        # the link is the invisible fault this project keeps repeating.
        primary_source: str = "local",
        # When the camera is being pulled directly, how long before the local
        # server is asked whether it is back, how far that wait may grow when
        # going back keeps not working, and how long a return has to last
        # before it counts as one. See the constants.
        return_after: float = DEFAULT_RETURN_AFTER,
        max_return_delay: float = DEFAULT_MAX_RETURN_DELAY,
        settled_after: float = DEFAULT_SETTLED_AFTER,
    ) -> None:
        self.url = url
        # The address to prefer - the local streaming server whenever there is
        # one - and the camera's own address. Both are kept apart from
        # `sources`, which is the rotation order and moves: "which way to the
        # picture is this" has to survive the rotation, or the detector cannot
        # tell going back from going away, which is exactly what it could not
        # tell before. `sources` is still tried best first, and still rotated
        # only after an address has really failed, because pulling the camera
        # directly puts a second copy of the stream on the radio link.
        self.preferred_url = url
        self.camera_url = fallback_url or (url if primary_source == "camera" else "")
        self._order_sources(url)
        self._failed_opens = 0
        # The way back. `_return_at` is meaningful only while the fallback is
        # in use; `_local_since` is when the preferred address was last taken
        # up, and is None while it is not the one in use.
        self.initial_return_delay = return_after
        self.max_return_delay = max_return_delay
        self.settled_after = settled_after
        self._return_delay = return_after
        self._return_at = 0.0
        self._local_since: float | None = None
        self._frames_when_taken = 0
        self._return_tries = 0
        # A new address for the local streaming server, put here by another
        # thread and acted on by this detector's own. Never a capture and never
        # a release: a `VideoCapture` released while the thread that owns it is
        # inside `read()` is a crash in C, not an exception, so nothing outside
        # this loop is allowed to touch one. The house rule for coming back the
        # other way, and this is it: store a value, let the loop read it.
        self._moved_to: str | None = None
        # How many times the picture has been taken from somewhere else. It is
        # published, because the number that says nothing has changed is the
        # number that says the link cost has not changed either.
        self.source_changes = 0
        self.stream = stream_name
        self.config = config or DetectionConfig()
        self.store = store
        self.pipeline = pipeline or DetectionPipeline(self.config)
        # The pipeline is what actually consults the config, so the config this
        # object paints the ignore mask onto has to be the pipeline's own. An
        # injected pipeline may have been built around a different object, and
        # a mask painted onto the one nobody reads is an operator watching a
        # tree he has painted out go on alarming.
        self.config = getattr(self.pipeline, "config", None) or self.config
        self.ignore_regions = list(ignore_regions)
        self.ignore_shapes = [
            [(int(x), int(y)) for x, y in shape] for shape in ignore_shapes
        ]
        # The size of the picture each outline was drawn on. Without it a mask
        # drawn at one resolution silently covers the wrong part of another.
        self.shape_sizes = [(int(w), int(h)) for w, h in shape_sizes]
        # Never None, so recording an event has one code path. The default
        # names nothing, which on the thermal is the correct answer and not a
        # placeholder.
        self.classifier = classifier or NullClassifier()
        # Whether anything is being asked at all, and how it is going. "Unnamed"
        # is the normal and correct answer on this system - at 700 m a person is
        # 13 pixels and nothing can name it - which is exactly what hides a
        # classifier that is switched on and has never once worked. A model that
        # would not load, a budget that is always missed, a weights file copied
        # half over: every one of those looks like a quiet, correct classifier,
        # and only the counts tell them apart.
        self.classifying = not isinstance(self.classifier, NullClassifier)
        self.named_asked = 0
        self.named = 0

        self._open_capture = open_capture
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self.max_read_failures = max_read_failures
        self.initial_reopen_delay = reopen_delay
        self.reopen_delay = reopen_delay
        self.max_reopen_delay = max_reopen_delay
        self.idle_sleep = idle_sleep

        self._capture = None
        self._retry_at = 0.0
        self._read_failures = 0
        self._stop = threading.Event()
        # (height, width) the ignore mask was last painted at, or None while
        # nothing has been painted. Not a boolean: the frame can change size.
        self._mask_size: tuple[int, int] | None = None

        self.frames = 0
        self.events = 0
        # Confirmed tracks that never reached the database. Counted separately
        # from `events` so the console cannot show a healthy-looking number for
        # movement that is in no list the operator can open.
        self.unrecorded = 0
        self.errors = 0
        self.reopens = 0
        # Frames thrown away without being looked at, because they had already
        # been overtaken. Counted rather than done quietly: it is the difference
        # between a detector keeping up with the camera and one watching a
        # perimeter as it was some seconds ago, and it is the number that says
        # which. See `_catch_up`.
        self.skipped = 0
        # What the stream says its frame rate is, read once when it opens. Not
        # `self.fps`, which is how fast this detector is processing - see
        # `reported_fps` for why the two are different questions.
        self._stream_fps: float | None = None
        # Whether this is a live source at all. A file is not: skipping frames
        # in a file throws away footage nobody can get back, and every test in
        # this suite reads one.
        self._live = str(url).lower().startswith(("rtsp://", "rtsps://"))
        self.reason = "the stream has not been opened yet"
        self._frame_index = 0
        # When the last frame arrived, so that something outside this thread can
        # tell a quiet perimeter from a read that has wedged. `read()` on a
        # socket that stopped talking blocks inside ffmpeg, and while it does,
        # no code here runs: the read-failure counter does not advance, the
        # capture is still open, and `reason` is still the empty string the last
        # good frame set. Nothing but the clock can show the difference.
        # On the steady clock, not the wall one. This is a duration - how long
        # the stream has been silent - and the laptop's wall clock is set by
        # hand. An hour's correction backwards makes the silence negative, and
        # a negative silence is under every threshold there is, so the one
        # reading that tells a wedged read from a quiet perimeter would report
        # "fine" for exactly as long as the correction was worth.
        self._last_frame_at: float | None = None
        self._frame_times: dict[int, float] = {}
        self._frame_order: deque[int] = deque()

        # What the frames actually contain. `read()` returning True proves a
        # buffer came back, not that there is a picture in it: a decoder given
        # a stream it cannot make sense of hands back success and a rectangle
        # of one flat value, and a relay that cached a keyframe hands back the
        # same picture for ever. Movement is found by comparing a frame with
        # the ones before it, so either of those produces no detection ever -
        # while every other reading here says the stream is healthy. Nothing
        # else in this loop can tell them from a perimeter with nobody on it.
        self.blank_frames = 0
        self._last_sample: np.ndarray | None = None
        self._picture_changed_at: float | None = None

    # -- state ------------------------------------------------------------

    @property
    def opened(self) -> bool:
        return self._capture is not None

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    @property
    def on_fallback(self) -> bool:
        """True while the picture is coming from anywhere but the first choice."""
        return self.url != self.preferred_url

    @property
    def source(self) -> str:
        """Which way to the picture is in use: "local" or "camera".

        Answered from the camera's own address rather than from which way this
        detector last rotated, because a stream that never had a local server
        is on the camera from its first frame and costs the link exactly as
        much as one that fell back to it.
        """
        return "camera" if self.camera_url and self.url == self.camera_url else "local"

    @property
    def blind(self) -> bool:
        """True when frames are arriving and there is no picture in them."""
        return self.blank_frames >= BLANK_FRAMES_BEFORE_BLIND

    @property
    def fps(self) -> float | None:
        """Frames a second, as actually delivered. None until there are enough.

        Measured rather than configured. The confirmation rule counts frames -
        three of the last five - so the frame rate is what decides how long a
        person has to be visible before they become an event, and the frame
        rate is not a fixed property of the camera: this app re-encodes the
        stream over ONVIF while it is running.

        Taken over the whole remembered window rather than the last interval,
        because one late frame on a radio link is normal and is not news.
        """
        if len(self._frame_order) < MIN_FRAMES_TO_MEASURE_RATE:
            return None
        first = self._frame_times.get(self._frame_order[0])
        last = self._frame_times.get(self._frame_order[-1])
        if first is None or last is None or last <= first:
            # Including a clock that was set backwards mid-window: no answer is
            # better than a negative frame rate.
            return None
        return (len(self._frame_order) - 1) / (last - first)

    def state(self) -> dict:
        """What the console shows for this stream, per stream.

        Detection continuing on the thermal while the visible is unreachable is
        normal, so this is never merged into one health flag. `reason` is a
        sentence because it is read by an operator on a hill, not by a
        developer with the source open.
        """
        return {
            "stream": self.stream,
            "opened": self.opened,
            # Which way to the picture this stream is being read through, and
            # which address that is. Published because the console is another
            # process and cannot ask - and because a detector that has fallen
            # back to the camera is putting a second full-rate copy of the
            # stream on a link that barely carries one, which used to be
            # visible as a single warning line in a ring of five hundred.
            #
            # The address has its password taken out of it: this is read off a
            # screen on a hill, and photographed.
            "source": self.source,
            "source_url": without_credentials(self.url),
            "source_changes": self.source_changes,
            "frames": self.frames,
            "events": self.events,
            "unrecorded": self.unrecorded,
            "errors": self.errors,
            "reopens": self.reopens,
            # Frames overtaken before they were looked at. Published because it
            # is the difference between a detector watching the perimeter now
            # and one watching it as it was some seconds ago, and because a
            # number climbing steadily here is the one honest sign that this
            # machine is too slow for the picture it has been given.
            "skipped": self.skipped,
            # What the stream says it delivers, beside "fps" below, which is
            # what this detector manages. The gap between the two is how hard
            # it is working - see `_catch_up`.
            "stream_fps": self._stream_fps,
            "reason": self.reason,
            # How much moved, and what each rejection rule threw away. Every one
            # of those rules deletes real detections when it is set wrongly and
            # says nothing when it does, so the counts are published: a rule
            # that has rejected everything this stream has ever produced is a
            # rule that is wrong, and it is now a number rather than a guess.
            "blobs": getattr(self.pipeline, "blobs_seen", 0),
            "rejected": dict(getattr(self.pipeline, "rejected", {}) or {}),
            "suppressed": getattr(self.pipeline, "frames_suppressed", 0),
            # How long this open capture has been silent, counted from the last
            # frame or, if none has arrived, from when it opened. None while
            # nothing is open. Read from whichever thread is asking, which is
            # the point: the detector's own thread is the one that may be
            # blocked inside a read that will not return.
            "seconds_since_frame": (
                None if self._last_frame_at is None else self._monotonic() - self._last_frame_at
            ),
            "fps": self.fps,
            # Frames arriving with nothing in them. Published separately from
            # `frames`, because `frames` is the number that made this look
            # healthy while the operator was being shown nothing.
            "blind": self.blind,
            "blank_frames": self.blank_frames,
            # How long the picture has been identical. None until two frames
            # have arrived to compare. A stream that never changes can never
            # produce a detection, whatever its frame rate says.
            # Is anything being asked to name what moved, how often, and how
            # often it managed to. Published because an unnamed event is the
            # right answer here and a classifier that has never once answered
            # produces the same picture as one that is working perfectly.
            "classifying": self.classifying,
            "named_asked": self.named_asked,
            "named": self.named,
            "seconds_since_change": (
                None
                if self._picture_changed_at is None
                else self._monotonic() - self._picture_changed_at
            ),
        }

    # -- the loop ---------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        """Read until told to stop. Never raises."""
        while not self._stop.is_set():
            try:
                progressed = self.step()
            except Exception:  # noqa: BLE001 - the loop is the last line of defence
                self.errors += 1
                logger.exception("%s: detection pass failed; continuing", self.stream)
                progressed = False
            if not progressed and not self._stop.is_set():
                # A stream that is down must wait, or it becomes a busy loop on
                # the same machine that is decoding video for the screen.
                self._sleep(self.idle_sleep)
        self.close()

    def step(self) -> bool:
        """One frame. Returns True when a frame was actually read and fed."""
        # Both asked before the read. The first takes up a local streaming
        # server that has moved; the second is the one thing in this loop whose
        # job is to end a direct pull of the camera.
        self._take_up_a_moved_local_server()
        self._go_back_if_the_local_server_is_back()
        if self._capture is None and not self._try_open():
            return False

        # Before the read, because the read is what hands over the oldest frame
        # in the queue. Everything this throws away is a picture of a moment
        # that has already been overtaken by a later one.
        self._catch_up()

        try:
            ok, frame = self._capture.read()
        except Exception as exc:  # noqa: BLE001 - a broken capture is a reopen, not a crash
            logger.warning("%s: reading failed: %s", self.stream, exc)
            ok, frame = False, None

        if not ok or frame is None:
            self._note_read_failure()
            return False

        self._read_failures = 0
        # A frame arrived, so this address works, whatever it did before it.
        # This and not a successful open is what clears the count that decides
        # to try the other address: an open that succeeds and then delivers
        # nothing is the failure that count exists to catch.
        self._failed_opens = 0
        # Cleared before the frame is looked at, not after: a frame that
        # arrives is news the moment it arrives, and `_inspect_picture` puts
        # its own sentence back if there is nothing in that frame.
        self.reason = ""
        now = self._clock()
        # Read once and passed down. Two readings of the same clock inside one
        # frame are two different numbers, and the tests drive both clocks
        # through the same stepping object on purpose.
        steady = self._monotonic()
        self._last_frame_at = steady
        index = self._frame_index
        self._frame_index += 1
        self.frames += 1
        self._remember_frame_time(index, now)
        self._inspect_picture(frame, steady)
        self._paint_mask(frame)

        try:
            detections = self.pipeline.feed(frame, index)
        except Exception:  # noqa: BLE001 - one bad frame must not end detection
            self.errors += 1
            logger.exception("%s: the pipeline failed on frame %d; continuing", self.stream, index)
            return True

        for detection in detections:
            self._record(detection, now, frame)
        return True

    def _catch_up(self) -> int:
        """Throw away the frames that arrived while the last one was being read.

        This is the fault behind "after a couple of seconds it beeped", and its
        shape is worse than its size. `read()` hands over the OLDEST frame in
        the queue, so a detector that cannot process 25 frames a second of 4K
        does not settle at a slower rate - it falls further behind every second,
        for as long as the process runs. On a machine nobody restarts for
        months that is an alarm that is minutes late by the end of the week, and
        nothing on the screen would ever have said so.

        Every frame skipped here is a picture of a moment a later frame already
        describes better. Nothing is lost that the detector could have used: the
        confirmation rule counts frames it has SEEN - three of the last five -
        so skipping lowers the rate it sees at and does not break the rule.
        `SLOW_STREAM_FPS` in `vmd/detect_main.py` is where a rate too low to
        confirm anything is already noticed and said out loud.

        Two things stop it. How far behind the clock says it is, which is the
        estimate, and how long a `grab` takes, which is the measurement: a grab
        that took about a frame interval was waiting for the wire rather than
        emptying a queue, so the queue is empty and this has done its job. The
        second is what makes this safe on a stream that is not behind at all -
        one grab, one measurement, stop - and on a link that has just come back,
        where the estimate would otherwise say to skip thousands.

        Only for a live stream, and only when the stream said its frame rate.
        Both unknowns default to doing nothing at all, which is what this loop
        did for its whole life before today.
        """
        if not self._live or self._capture is None:
            return 0
        if self._stream_fps is None or self._last_frame_at is None:
            return 0
        grab = getattr(self._capture, "grab", None)
        if grab is None:
            return 0

        interval = 1.0 / self._stream_fps
        # Minus one: the frame this loop is about to read is the one it wants.
        behind = int((self._monotonic() - self._last_frame_at) / interval) - 1
        if behind <= 0:
            return 0

        skipped = 0
        for _ in range(min(behind, MAX_FRAMES_TO_SKIP)):
            started = self._monotonic()
            try:
                if not grab():
                    break
            except Exception:  # noqa: BLE001 - a broken grab is the read's problem
                break
            skipped += 1
            if self._monotonic() - started > interval * 0.5:
                # That one came off the wire rather than out of a queue, so
                # there is no queue left to empty.
                break

        if skipped:
            self.skipped += skipped
        return skipped

    def close(self) -> None:
        self._release()

    # -- opening and reopening --------------------------------------------

    def _try_open(self) -> bool:
        now = self._monotonic()
        if now < self._retry_at:
            return False
        try:
            capture = self._open_capture(self.url)
        except Exception as exc:  # noqa: BLE001 - an unreachable stream is not an error here
            # The message is scrubbed too: what failed to open is usually named
            # in it, and what failed to open is a URL with a password in it.
            logger.warning("%s: %s", self.stream, without_credentials(str(exc)))
            capture = None
        if capture is None:
            self._schedule_retry(now)
            self._failed_opens += 1
            self.reason = (
                f"the stream could not be opened; trying again in "
                f"{self.reopen_delay:.0f} seconds"
            )
            self._try_the_other_address()
            return False
        self._capture = capture
        # Asked once, here, rather than on every frame: it is a property of the
        # stream that was just opened, and asking a capture anything costs a
        # call into FFmpeg. A stream that will not say leaves `_catch_up` doing
        # nothing, which is what this loop did before it existed.
        self._stream_fps = reported_fps(capture)
        # `_failed_opens` is deliberately not cleared here. Opening is not the
        # same as delivering, and clearing it on an open meant a server that
        # accepted every connection and served no frames was reopened for ever
        # and the other address was never tried.
        self._read_failures = 0
        # The silence is timed from here, not from the first frame. A capture
        # can wedge on its first read as easily as on its thousandth, and "no
        # frame has ever arrived" must not read as "nothing to report".
        self._last_frame_at = self._monotonic()
        self.reopen_delay = self.initial_reopen_delay
        self.reason = ""
        logger.info("%s: reading %s", self.stream, without_credentials(self.url))
        return True

    def _order_sources(self, first: str) -> None:
        """The addresses to try, `first` first, then whatever else is known.

        Rebuilt rather than edited, because there are only ever two of them and
        both can change underneath this object: the local streaming server
        moves when go2rtc is restarted on another port, and the camera's own
        address is the one thing here that never does.
        """
        ordered = [first]
        for other in (self.preferred_url, self.camera_url):
            if other and other not in ordered:
                ordered.append(other)
        self.sources = ordered

    def _try_the_other_address(self) -> None:
        """After enough failures on one address, try the other one.

        There are usually two ways to the same picture: the local streaming
        server, which is what everything should use because the camera is
        already being pulled once, and the camera itself. Which one is used was
        decided once at start-up from a port answering, and never revisited -
        so a streaming server that restarted on another port, or one left over
        from an older settings file, took detection off this stream for the
        life of the process while the camera was reachable throughout.

        Rotated rather than switched, so a camera that is genuinely down does
        not leave the detector stuck on whichever address it happened to be
        trying when the camera came back.

        This half - going away - was never the problem. It rotated on failure
        and never on success, so the *first* time it fired, detection moved
        onto its own crossing of the radio link and stayed there for the life
        of the process. The other half is `_go_back_if_the_local_server_is_back`,
        and this method's job here is to plan it: from the moment the camera is
        being pulled directly, something has to be counting the minutes until
        the local server is asked again.
        """
        if len(self.sources) < 2 or self._failed_opens < OPEN_FAILURES_BEFORE_FALLBACK:
            return
        self._failed_opens = 0
        self.sources.append(self.sources.pop(0))
        self.url = self.sources[0]
        self.source_changes += 1
        now = self._monotonic()
        if self.on_fallback:
            # Detection has just moved onto the camera, which is a second
            # crossing of the radio link. From here the only thing that ends
            # that is going back, so the way back is planned now.
            self._plan_the_way_back(now)
            logger.warning(
                "%s: that address has produced no frames %d times running; reading %s "
                "instead. The camera is now being pulled a second time across the "
                "radio link; the local streaming server will be tried again in "
                "%.0f seconds.",
                self.stream,
                OPEN_FAILURES_BEFORE_FALLBACK,
                without_credentials(self.url),
                self._return_delay,
            )
            return
        self._took_the_local_server(now)
        logger.warning(
            "%s: that address has produced no frames %d times running; trying %s instead",
            self.stream,
            OPEN_FAILURES_BEFORE_FALLBACK,
            without_credentials(self.url),
        )

    # -- the way back to the local server ----------------------------------

    def point_at_local(self, url: str) -> None:
        """The local streaming server is at `url` now. Safe to call from anywhere.

        go2rtc is started on a free port, so a restart can bring it back
        somewhere else - and this process reads that file once, at start-up.
        Without this, "try the local server again" would for ever try an
        address that nothing has answered on since the restart, and detection
        would sit on the camera exactly as it did before, having asked politely
        every two minutes.

        Only a string is stored. What to do about it is decided by the thread
        that owns the capture, on its next pass.
        """
        if url and url != self.preferred_url:
            self._moved_to = url

    def _take_up_a_moved_local_server(self) -> None:
        """Act on `point_at_local`, on this detector's own thread.

        If the camera is being pulled, nothing is dropped: the way back simply
        now leads to the new address, on the schedule already planned. If the
        old local address is the one being read, it is let go - whatever is
        behind that port, it is not the streaming server this stream belongs
        to any more - and the new one is opened on the next pass.
        """
        url = self._moved_to
        if url is None:
            return
        self._moved_to = None
        if url == self.preferred_url:
            return
        was_reading_it = self.url == self.preferred_url
        self.preferred_url = url
        self._order_sources(url if was_reading_it else self.url)
        logger.info(
            "%s: the local streaming server is now at %s",
            self.stream,
            without_credentials(url),
        )
        if not was_reading_it:
            return
        self.url = url
        self._release()
        self._retry_at = 0.0
        self.reopen_delay = self.initial_reopen_delay
        self._failed_opens = 0
        self._read_failures = 0
        self._took_the_local_server(self._monotonic())
        self.reason = "the local streaming server moved; opening it at its new address"

    def _go_back_if_the_local_server_is_back(self) -> None:
        """While the camera is being pulled directly, ask whether it still has to be.

        The fallback used to be one-way. It rotated on failure and never on
        success, so the first go2rtc restart moved detection onto its own
        crossing of the radio link and left it there for the life of the
        process - one warning line, in a ring of five hundred, for a stream
        that "barely carries one" copy.

        Three rules, and each of them is what stops this being worse than the
        bug:

        * **Not before the settled period.** Rotating on every hiccup is worse
          than staying put, so nothing is asked for the first `return_after`
          seconds on the camera, and a return that did not last doubles that.
        * **Nothing is let go until the replacement is proved.** The local
          server is opened *and read from* while the camera is still open and
          still being read. The camera is released afterwards, so the gap in
          watching the perimeter is one pass of this loop rather than a reopen.
        * **A failure changes nothing.** If the local server is genuinely gone,
          the camera keeps being read. Detecting from the wrong place beats not
          detecting.

        Asked on this thread, and that is safe only because the address being
        probed is on 127.0.0.1: nothing listening there is a refused connection
        in microseconds, and a go2rtc that is up answers at once. Probing an
        address across the radio link this way would hold up the read loop for
        as long as that link took to say no, which is the thing this file is
        most careful never to do.
        """
        if self._capture is None or not self.on_fallback:
            return
        now = self._monotonic()
        if now < self._return_at:
            return
        capture = self._open_and_prove(self.preferred_url)
        if capture is None:
            self._return_tries += 1
            self._return_at = now + self._return_delay
            self._say_it_is_still_not_there()
            return
        self._take(capture, self.preferred_url, now)

    def _open_and_prove(self, url: str):
        """Open an address and get a frame out of it, or return None.

        Opening is not enough. go2rtc answering on 127.0.0.1 proves something
        is listening and nothing at all about whether it serves this stream -
        which is the same mistake as deciding the source once from a port
        answering, made again in the other direction. A probe that opened and
        was believed would hand detection to a server with no picture behind
        it and take it off a camera that was working.

        The frame it reads is thrown away. It belongs to a stream that is about
        to have its background model rebuilt anyway.
        """
        try:
            capture = self._open_capture(url)
        except Exception as exc:  # noqa: BLE001 - a server that is not there is not an error
            logger.debug("%s: %s", self.stream, without_credentials(str(exc)))
            return None
        if capture is None:
            return None
        try:
            ok, frame = capture.read()
        except Exception:  # noqa: BLE001 - a capture that raises is a capture that failed
            logger.debug("%s: the probe capture could not be read", self.stream, exc_info=True)
            ok, frame = False, None
        if ok and frame is not None:
            return capture
        self._let_go(capture)
        return None

    def _take(self, capture, url: str, now: float) -> None:
        """Read from `capture` from now on, and let go of whatever was open.

        In this order on purpose: the new capture is already open and has
        already delivered a frame before the old one is released, so there is
        no moment at which this detector has nothing to watch the perimeter
        with. What it does cost is the background model, which is reset for the
        same reason a reopen resets it - the picture may not be the size or the
        latency the model was built from - and which is rebuilt in a few
        frames.
        """
        self._release()
        self._capture = capture
        self.url = url
        # Best first again, so a later failure rotates the way it always did.
        self._order_sources(url)
        self.source_changes += 1
        self._read_failures = 0
        self._failed_opens = 0
        self._retry_at = 0.0
        self.reopen_delay = self.initial_reopen_delay
        self._last_frame_at = now
        self.reason = ""
        self._took_the_local_server(now)
        try:
            self.pipeline.reset()
        except Exception:  # noqa: BLE001 - a model that would not reset is not a reason to stop
            logger.exception("%s: could not reset the pipeline", self.stream)
        logger.info(
            "%s: the local streaming server is answering again; reading %s. The camera "
            "is no longer being pulled a second time across the radio link.",
            self.stream,
            without_credentials(url),
        )

    def _took_the_local_server(self, now: float) -> None:
        """Start the clock on whether this return sticks."""
        self._local_since = now
        self._frames_when_taken = self.frames
        self._return_tries = 0

    def _plan_the_way_back(self, now: float) -> None:
        """Decide when the local server is next asked, and say why it is that long.

        The settled period, the same rule the supervisor applies to a child
        that keeps dying: a stay that was shorter than `settled_after`, or one
        that delivered no frames at all, was not a recovery - it was a flap,
        and flapping between two addresses costs a reset background model each
        time. So it doubles, to a cap that is deliberately low because every
        minute on the camera is a minute the link carries the stream twice.

        A first fallback has nothing to judge and gets the plain wait.
        """
        if self._local_since is None:
            self._return_delay = self.initial_return_delay
        elif now - self._local_since < self.settled_after or self.frames <= self._frames_when_taken:
            self._return_delay = min(self._return_delay * 2, self.max_return_delay)
        else:
            # It worked for long enough to count. Whatever has just happened is
            # a fresh fault, not the last one continuing.
            self._return_delay = self.initial_return_delay
        self._local_since = None
        self._return_tries = 0
        self._return_at = now + self._return_delay

    def _say_it_is_still_not_there(self) -> None:
        """Report that the link is still carrying two copies, without saying it
        every two minutes for months.

        Spelled out the first time and then rarely: this process runs for
        months, and the ring the operator reads holds five hundred lines. The
        console reads the same fact out of `detection.json` on every heartbeat,
        which is where an operator looking for it will find it.
        """
        if (
            self._return_tries <= RETURN_TRIES_SPELLED_OUT
            or self._return_tries % RETURN_TRIES_BETWEEN_REMINDERS == 0
        ):
            logger.warning(
                "%s: the local streaming server is still not serving this stream "
                "(%d attempts); still reading the camera directly, which costs the "
                "radio link a second copy of it.",
                self.stream,
                self._return_tries,
            )

    def _schedule_retry(self, now: float) -> None:
        self._retry_at = now + self.reopen_delay
        self.reopen_delay = min(self.reopen_delay * 2, self.max_reopen_delay)

    def _note_read_failure(self) -> None:
        self._read_failures += 1
        if self._read_failures < self.max_read_failures:
            return
        # The socket is alive and silent, which is what a dead radio link looks
        # like from here. Only reopening recovers it.
        self.reason = (
            f"the stream opened but delivered no frames "
            f"({self._read_failures} reads in a row); reopening it"
        )
        logger.warning("%s: %s", self.stream, self.reason)
        self._release()
        self.reopens += 1
        self._read_failures = 0
        # An address that opens and then delivers nothing has failed as
        # completely as one that would not open, and until this was counted the
        # other address was never tried against it.
        self._failed_opens += 1
        self._try_the_other_address()
        self._schedule_retry(self._monotonic())
        # The camera may have been moved while it was unreachable, so the
        # background model is about to be a model of a view that no longer
        # exists.
        try:
            self.pipeline.reset()
        except Exception:  # noqa: BLE001
            logger.exception("%s: could not reset the pipeline", self.stream)

    def _let_go(self, capture) -> None:
        """Release one capture, whether or not it is the one being read.

        Best-effort: a capture that will not release is a leaked handle in a
        process that is going to go on watching the perimeter either way.
        """
        try:
            capture.release()
        except Exception:  # noqa: BLE001 - releasing is best-effort
            logger.debug("%s: releasing the capture failed", self.stream, exc_info=True)

    def _release(self) -> None:
        if self._capture is None:
            return
        self._let_go(self._capture)
        self._capture = None
        # There is nothing open to have gone silent, and a number left over
        # from the capture before this one would be read as one that had. The
        # same goes for what the last capture's frames looked like: comparing
        # the first frame of a new stream with the last frame of a dead one
        # answers no question anybody asked.
        self._last_frame_at = None
        self.blank_frames = 0
        self._last_sample = None
        self._picture_changed_at = None

    # -- frames -----------------------------------------------------------

    def _inspect_picture(self, frame, now: float) -> None:
        """Ask whether there is anything in this frame, and whether it moved.

        Two failures wear the same disguise, and this loop cannot see either of
        them without looking at the pixels:

        * **A blank frame.** A decoder handed a stream it cannot make sense of
          returns success and a rectangle of one flat value, exactly as ffmpeg
          does when it grabs the first frame off a live stream. Every guard
          upstream passes - ok is True, the frame is not None, the count
          climbs, the frame rate is healthy - and background subtraction on a
          flat picture finds nothing, for ever.
        * **A frozen frame.** A relay that cached a keyframe, or a decoder
          repeating its last good picture after the link dropped. Movement is
          the difference between one frame and the next, so a picture that
          never changes cannot produce a detection whatever its frame rate is.

        Both report precisely what a quiet perimeter reports. This does not act
        on either - a stream with no picture in it is still read, because the
        picture may come back and dropping it would be the same mistake in the
        other direction - it only makes the difference sayable.

        Cheap on purpose: one strided view, no copy of the frame, arithmetic on
        a few thousand values. Guarded, because a frame is whatever the capture
        handed back, and being unable to inspect it is not a reason to stop
        watching the perimeter.
        """
        try:
            sample = np.asarray(frame)[::FRAME_SAMPLE_STRIDE, ::FRAME_SAMPLE_STRIDE]
            if sample.size == 0:
                return
            spread = float(sample.max()) - float(sample.min())
            unchanged = (
                self._last_sample is not None
                and self._last_sample.shape == sample.shape
                and np.array_equal(self._last_sample, sample)
            )
            # A copy: the capture is entitled to reuse its own buffer, and a
            # view of a buffer that is overwritten in place would compare equal
            # to itself for ever and call every stream frozen.
            self._last_sample = sample.copy()
        except Exception:  # noqa: BLE001 - an unreadable frame is not a crash
            logger.debug("%s: could not inspect the picture", self.stream, exc_info=True)
            return

        if not unchanged or self._picture_changed_at is None:
            self._picture_changed_at = now

        if spread <= BLANK_SPREAD:
            self.blank_frames += 1
        else:
            self.blank_frames = 0

        if self.blind:
            self.reason = (
                f"frames are arriving but there is no picture in them - "
                f"{self.blank_frames} in a row have been blank. Nothing there "
                f"can be detected while this is true."
            )

    def _paint_mask(self, frame) -> None:
        """Build the ignore mask from the size of a real frame, and rebuild it
        when that size changes.

        The operator boxes some areas and draws round others; only a frame knows
        how big the frame is. And a frame is not a fixed size: this app
        re-encodes the camera over ONVIF while it is running, so the stream can
        change resolution without anybody asking it to. A mask painted once at
        the first size stops lining up with the picture the moment it changes -
        the tree the operator painted out comes back, and a patch of ground he
        never painted goes quiet - so the size it was painted at is remembered
        and checked.

        Both kinds go into the one mask. The rectangles are what every settings
        file in existence carries, the outlines are what he can draw now, and a
        mask that honoured only one of them would silently give him back a
        swaying treeline he had already dealt with.
        """
        if not self.ignore_regions and not self.ignore_shapes:
            return
        height, width = frame.shape[:2]
        if self._mask_size == (height, width):
            return
        if self._mask_size is not None:
            logger.info(
                "%s: the picture is now %dx%d; repainting the ignored areas",
                self.stream,
                width,
                height,
            )
        self.config.ignore_mask = mask_from_areas(
            self.ignore_regions,
            self.ignore_shapes,
            width,
            height,
            drawn_at=self.shape_sizes,
        )
        self._mask_size = (height, width)

    def _remember_frame_time(self, index: int, now: float) -> None:
        self._frame_times[index] = now
        self._frame_order.append(index)
        while len(self._frame_order) > FRAME_TIME_HISTORY:
            self._frame_times.pop(self._frame_order.popleft(), None)

    def _name(self, frame, box) -> tuple[str, float]:
        """Ask the classifier what this was. Never raises, never blocks long.

        The classifier is asked once per event and not once per frame, so the
        frame budget is untouched on every frame that confirmed nothing; and it
        is asked through a budget, so the one frame that did confirm something
        cannot be held up by a model that has wedged. Whatever comes back -
        including nothing - the caller writes the event.
        """
        if not self.classifying:
            # Nothing was asked, so nothing failing to answer is not a fact
            # about anything. Counting it would make the thermal's correct and
            # deliberate silence look like a broken model.
            return UNNAMED
        self.named_asked += 1
        try:
            answer = named(self.classifier.classify(frame, box))
        except Exception:  # noqa: BLE001 - a name is never worth an event
            self.errors += 1
            logger.exception("%s: classifying failed; the event is unnamed", self.stream)
            return UNNAMED
        if answer[0]:
            self.named += 1
        return answer

    def _record(self, detection, now: float, frame=None) -> None:
        """Write one confirmed track, whether or not anything can name it.

        The order here is the design: the row is written for every confirmed
        track, and the label is decoration on it. The classifier has no veto -
        there is no confidence below which this returns early, because at 700 m
        the thing the operator most needs to hear about is exactly the thing
        nothing can name.

        Nor does the database. A store that could not be opened, or one that
        refuses the write, costs the row and must not also cost the sentence:
        a person crossing the perimeter that produced no line anywhere is the
        exact failure the whole system exists to prevent, and a full disk is a
        perfectly ordinary way to arrive at it. So every confirmed track is
        announced, and the announcement says whether it reached the database.
        """
        track = detection.track
        box = detection.box
        # Never after `now`. The two readings come from a clock the operator
        # sets by hand, and one corrected between the track's first frame and
        # its confirmation would stamp an event that ends before it starts -
        # which overlaps no window, so `between()` never returns it and the
        # mark is missing from every part of the timeline.
        started = min(self._frame_times.get(track.first_frame, now), now)
        label, confidence = self._name(frame, box)

        stored = False
        problem = ""
        if self.store is None:
            problem = "there is no event database open; this is logged only"
        else:
            try:
                self.store.add(
                    stream=self.stream,
                    started=started,
                    ended=now,
                    box=(box.x, box.y, box.w, box.h),
                    travelled_px=track.travelled,
                    label=label,
                    confidence=confidence,
                    clip_path="",
                )
                stored = True
            except Exception as exc:  # noqa: BLE001 - a locked database must not stop detection
                self.errors += 1
                problem = f"it could not be recorded ({exc}); this is logged only"
                logger.exception("%s: could not record an event; continuing", self.stream)

        if stored:
            self.events += 1
        else:
            self.unrecorded += 1
        self._announce(box, track.travelled, label, confidence, problem)

    def _announce(
        self, box, travelled: float, label: str, confidence: float, problem: str = ""
    ) -> None:
        """Say what moved, at the volume the situation deserves.

        A recorded event is information; one that never reached the database is
        a warning, because the operator's list will not have it in it.
        """
        message = "%s: movement at (%d, %d) %dx%d, travelled %.0f px%s%s"
        arguments = (
            self.stream,
            box.x,
            box.y,
            box.w,
            box.h,
            travelled,
            # Blank means unidentified, not uncertain: most of what this system
            # sees is too small to name and is reported anyway.
            f" - looks like a {label} ({confidence:.0%})" if label else "",
            f" - {problem}" if problem else "",
        )
        if problem:
            logger.warning(message, *arguments)
        else:
            logger.info(message, *arguments)
