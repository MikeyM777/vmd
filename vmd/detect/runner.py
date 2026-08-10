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
        self.ignore_regions = list(ignore_regions)
        # Never None, so recording an event has one code path. The default
        # names nothing, which on the thermal is the correct answer and not a
        # placeholder.
        self.classifier = classifier or NullClassifier()

        self._open_capture = open_capture
        self._clock = clock
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
        self._mask_painted = not self.ignore_regions

        self.frames = 0
        self.events = 0
        self.errors = 0
        self.reopens = 0
        self.reason = "the stream has not been opened yet"
        self._frame_index = 0
        self._frame_times: dict[int, float] = {}
        self._frame_order: deque[int] = deque()

    # -- state ------------------------------------------------------------

    @property
    def opened(self) -> bool:
        return self._capture is not None

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

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
            "errors": self.errors,
            "reopens": self.reopens,
            "reason": self.reason,
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
        now = self._clock()
        if now < self._retry_at:
            return False
        try:
            capture = self._open_capture(self.url)
        except Exception as exc:  # noqa: BLE001 - an unreachable stream is not an error here
            logger.warning("%s: %s", self.stream, exc)
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
        self.reopen_delay = self.initial_reopen_delay
        self.reason = ""
        logger.info("%s: reading %s", self.stream, self.url)
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
        self._schedule_retry(self._clock())
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

    # -- frames -----------------------------------------------------------

    def _paint_mask(self, frame) -> None:
        """Build the ignore mask once, from the size of a real frame.

        The operator paints rectangles; only a frame knows how big the frame is.
        """
        if self._mask_painted:
            return
        height, width = frame.shape[:2]
        self.config.ignore_mask = mask_from_regions(self.ignore_regions, width, height)
        self._mask_painted = True

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
        """
        track = detection.track
        box = detection.box
        started = self._frame_times.get(track.first_frame, now)
        if self.store is None:
            return
        label, confidence = self._name(frame, box)
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
        except Exception:  # noqa: BLE001 - a locked database must not stop detection
            self.errors += 1
            logger.exception("%s: could not record an event; continuing", self.stream)
            return
        self.events += 1
        logger.info(
            "%s: movement at (%d, %d) %dx%d, travelled %.0f px%s",
            self.stream,
            box.x,
            box.y,
            box.w,
            box.h,
            track.travelled,
            # Blank means unidentified, not uncertain: most of what this system
            # sees is too small to name and is reported anyway.
            f" - looks like a {label} ({confidence:.0%})" if label else "",
        )
