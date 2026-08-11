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

from vmd.detect.classify import UNNAMED, NullClassifier, named
from vmd.detect.config import mask_from_regions
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


def open_capture_cv2(url: str):
    """Open a stream with OpenCV. Returns None if it did not open.

    Imported lazily so that importing this module - which the console does, to
    read state - does not pull OpenCV into a process that only wants a
    dataclass.
    """
    import cv2

    capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not capture.isOpened():
        capture.release()
        return None
    return capture


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
    ) -> None:
        self.url = url
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
        # Never None, so recording an event has one code path. The default
        # names nothing, which on the thermal is the correct answer and not a
        # placeholder.
        self.classifier = classifier or NullClassifier()

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
        self.reason = "the stream has not been opened yet"
        self._frame_index = 0
        # When the last frame arrived, so that something outside this thread can
        # tell a quiet perimeter from a read that has wedged. `read()` on a
        # socket that stopped talking blocks inside ffmpeg, and while it does,
        # no code here runs: the read-failure counter does not advance, the
        # capture is still open, and `reason` is still the empty string the last
        # good frame set. Nothing but the clock can show the difference.
        self._last_frame_at: float | None = None
        self._frame_times: dict[int, float] = {}
        self._frame_order: deque[int] = deque()

    # -- state ------------------------------------------------------------

    @property
    def opened(self) -> bool:
        return self._capture is not None

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

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
            "frames": self.frames,
            "events": self.events,
            "unrecorded": self.unrecorded,
            "errors": self.errors,
            "reopens": self.reopens,
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
                None if self._last_frame_at is None else self._clock() - self._last_frame_at
            ),
            "fps": self.fps,
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
        if self._capture is None and not self._try_open():
            return False

        try:
            ok, frame = self._capture.read()
        except Exception as exc:  # noqa: BLE001 - a broken capture is a reopen, not a crash
            logger.warning("%s: reading failed: %s", self.stream, exc)
            ok, frame = False, None

        if not ok or frame is None:
            self._note_read_failure()
            return False

        self._read_failures = 0
        self.reason = ""
        now = self._clock()
        self._last_frame_at = now
        index = self._frame_index
        self._frame_index += 1
        self.frames += 1
        self._remember_frame_time(index, now)
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
            self.reason = (
                f"the stream could not be opened; trying again in "
                f"{self.reopen_delay:.0f} seconds"
            )
            return False
        self._capture = capture
        self._read_failures = 0
        # The silence is timed from here, not from the first frame. A capture
        # can wedge on its first read as easily as on its thousandth, and "no
        # frame has ever arrived" must not read as "nothing to report".
        self._last_frame_at = self._clock()
        self.reopen_delay = self.initial_reopen_delay
        self.reason = ""
        logger.info("%s: reading %s", self.stream, without_credentials(self.url))
        return True

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
        self._schedule_retry(self._monotonic())
        # The camera may have been moved while it was unreachable, so the
        # background model is about to be a model of a view that no longer
        # exists.
        try:
            self.pipeline.reset()
        except Exception:  # noqa: BLE001
            logger.exception("%s: could not reset the pipeline", self.stream)

    def _release(self) -> None:
        if self._capture is None:
            return
        try:
            self._capture.release()
        except Exception:  # noqa: BLE001 - releasing is best-effort
            logger.debug("%s: releasing the capture failed", self.stream, exc_info=True)
        self._capture = None
        # There is nothing open to have gone silent, and a number left over
        # from the capture before this one would be read as one that had.
        self._last_frame_at = None

    # -- frames -----------------------------------------------------------

    def _paint_mask(self, frame) -> None:
        """Build the ignore mask from the size of a real frame, and rebuild it
        when that size changes.

        The operator paints rectangles; only a frame knows how big the frame is.
        And a frame is not a fixed size: this app re-encodes the camera over
        ONVIF while it is running, so the stream can change resolution without
        anybody asking it to. A mask painted once at the first size stops lining
        up with the picture the moment it changes - the tree the operator
        painted out comes back, and a patch of ground he never painted goes
        quiet - so the size it was painted at is remembered and checked.
        """
        if not self.ignore_regions:
            return
        height, width = frame.shape[:2]
        if self._mask_size == (height, width):
            return
        if self._mask_size is not None:
            logger.info(
                "%s: the picture is now %dx%d; repainting the ignored regions",
                self.stream,
                width,
                height,
            )
        self.config.ignore_mask = mask_from_regions(self.ignore_regions, width, height)
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
        try:
            return named(self.classifier.classify(frame, box))
        except Exception:  # noqa: BLE001 - a name is never worth an event
            self.errors += 1
            logger.exception("%s: classifying failed; the event is unnamed", self.stream)
            return UNNAMED

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
