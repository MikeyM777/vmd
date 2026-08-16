"""The detection service: watch every stream the operator asked to watch.

A separate process, exactly like the recorder, for exactly the same reason: the
console must not be able to stop detection, and detection must not be able to
stop the console. It shares nothing with the recorder but the local stream, so
**detection stopping never stops recording.**

    go2rtc  --> detector process --> events.db      --> console
                                 \\-> detection.json --> console
       \\----> recorder --> segments + segments.db

Two things leave this process, and neither of them is a function call:

* Logging, which `logging.basicConfig` sends to stderr. The console spawns this
  process with stderr merged into a pipe and pumps that pipe into its Logs tab,
  so what is written here is what the operator reads. A detector this console
  adopted from an earlier run is the exception - its pipe belongs to the console
  that started it, and the Logs tab says so rather than showing nothing.
* `detection.json`, written beside events.db every few seconds. `status()` knows
  per stream whether the capture opened and, if not, why; the console is another
  process and cannot ask, so the answer is published rather than kept.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

# Deliberately light. Importing this module must cost sqlite3 and pydantic and
# nothing else: the console reads two facts out of it - where the status file
# is and which streams are detected - and the console has to open on a laptop
# where the vision stack is missing or will not load. Everything that needs
# numpy or cv2 - the config, the pipeline, the runner - is imported inside the
# one class that builds detectors, which is the only thing here that decodes.
from vmd.detect.events import EventStore
from vmd.settings import Settings, SettingsError, load_settings
from vmd.streaming.endpoint import is_live, local_source, read_endpoint

if TYPE_CHECKING:  # pragma: no cover - annotations only; `from __future__` defers them
    from vmd.detect.runner import StreamDetector

logger = logging.getLogger(__name__)

# Written by the console when it starts the streaming server, beside the
# settings it was started with. The same file the recorder reads.
DEFAULT_ENDPOINT_PATH = Path("streaming.json")

EVENTS_FILENAME = "events.db"

# Where the per-stream state is published for the console, beside events.db.
# Importable: this module no longer drags the detector's stack behind it, so
# the console can read this name from here rather than repeating the string.
STATUS_FILENAME = "detection.json"

# How often `streaming.json`, and the port it names, are asked about again.
#
# It used to be read exactly once, in __init__. go2rtc is started on a free
# port, so a restart can bring it back somewhere else - and a detector that had
# fallen back to the camera would then go on offering to return to an address
# nothing has answered on since, for ever, while a perfectly good server ran on
# the next port up. The recorder learned the same thing at the same time and
# uses the same interval; see SOURCE_CHECK_SECONDS in vmd\record_main.py.
#
# Fifteen seconds. The check is a small file read and one connection to
# 127.0.0.1, and the thing it is racing is an operator opening the console.
SOURCE_CHECK_SECONDS = 15.0

# How long an open stream may go without delivering a frame before it is called
# out. The camera can legitimately be slow - a re-encoded thermal at a few
# frames a second is a normal setting, and the design allows for a frame every
# three seconds - so this has to be far longer than any real frame interval and
# far shorter than a night. A minute is both.
#
# It exists because a `VideoCapture.read()` on a link that dropped without
# closing blocks inside ffmpeg, possibly for ever, and nothing on that thread
# runs while it does: the capture stays "open", the reason stays empty, and a
# dead camera looks exactly like a quiet perimeter.
STALLED_AFTER_SECONDS = 60.0

# How long a stream's picture may be bit-for-bit identical before it is called
# out. Movement is the difference between one frame and the next, so a picture
# that never changes cannot produce a detection however fast it arrives - a
# relay serving a cached keyframe, or a decoder repeating its last good picture
# after the link dropped, both report as a perfectly healthy stream.
#
# Five minutes, because the other explanation for an identical picture is a
# genuinely motionless scene an encoder is skipping wholesale, and that is not
# a fault. The warning is worded to hold either way: nothing can be detected
# from frames that are all the same, whichever of the two is true.
FROZEN_AFTER_SECONDS = 300.0

# How many things the classifier may be asked to name, and name none of, before
# the operator is told. It is the one failure here whose symptom is the correct
# answer: at 700 m a person is 13 pixels, nothing can name that, and an event
# with no label is what a working classifier produces most of the time. So a
# model that would not load, a weights file copied half over, or a budget missed
# on every call all look exactly like a classifier doing its job.
#
# Twenty-five, because a run of that many unnameable crops is entirely ordinary
# on the thermal and a run of that many on a stream the operator switched the
# classifier on for is worth one line.
NEVER_NAMED_AFTER = 25

# The frame rate below which the confirmation rule stops being able to confirm
# a person crossing.
#
# A track becomes an event after three of the last five frames and twelve
# pixels of travel. At 25 fps that is a fifth of a second. At one frame every
# three seconds it is fifteen, which is longer than it takes to cross most of
# anything. Measured on the owner's own labelled footage, decimated to stand in
# for a re-encoded stream: 7/8 person spans at 30 fps, 7/8 at 3 fps, 5/8 at
# 1 fps, 2/8 at 0.33 fps.
#
# So 3 fps: the lowest rate that lost nothing, and the point below which the
# operator has to be told, because the control that puts the stream there is
# this app's own ONVIF re-encode and nothing else would connect the two.
SLOW_STREAM_FPS = 3.0


def detected_streams(settings: Settings) -> list:
    """The streams to watch: enabled, ticked for detection, master switch on.

    A stream the operator disabled is off whatever its detection tick says -
    there is nothing to read from a stream nobody is pulling.
    """
    if not settings.detection.enabled:
        return []
    return [s for s in settings.camera.streams if s.enabled and s.detect]


class DetectionService:
    """Owns one detector per detected stream, each on its own thread.

    Threads rather than processes because the work is decoding, which releases
    the GIL, and because one detector dying must not take the others with it -
    which is a property of the loop inside StreamDetector, not of the process
    boundary.
    """

    def __init__(
        self,
        settings: Settings,
        endpoint_path: str | Path | None = None,
        # None rather than the real thing, so that naming this class does not
        # import OpenCV before anybody has decided to build a detector. The
        # defaults are resolved below, where the decoding actually starts.
        open_capture: Callable | None = None,
        pipeline_factory: Callable | None = None,
        store_factory: Callable[[Path], EventStore] = EventStore,
    ) -> None:
        from vmd.detect.pipeline import DetectionPipeline
        from vmd.detect.runner import open_capture_cv2

        self.settings = settings
        # Kept, not just read: the answer in it expires. See _recheck_sources.
        self.endpoint_path = Path(endpoint_path or DEFAULT_ENDPOINT_PATH)
        endpoint = read_endpoint(self.endpoint_path)
        self._endpoint = endpoint if endpoint and is_live(endpoint) else None
        self._last_source_check: float | None = None
        self.root = Path(settings.storage.root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Beside segments.db, because the two are reclaimed together: an event
        # that outlives its footage points at a file that is not there.
        self.events_path = self.root / EVENTS_FILENAME
        # Beside the events for the same reason: they describe the same run, and
        # a status file that outlived the directory it describes is a lie.
        self.status_path = self.root / STATUS_FILENAME

        self._store_factory = store_factory
        self._pipeline_factory = pipeline_factory or (lambda config: DetectionPipeline(config))
        self._stop = threading.Event()
        self.threads: list[threading.Thread] = []

        self.streams = detected_streams(settings)
        self.detectors = [
            self._detector_for(stream, open_capture or open_capture_cv2) for stream in self.streams
        ]

    def _detector_for(self, stream, open_capture):
        """One stream's detector, built around **one** config object.

        The single object matters. The ignore mask cannot be built until a
        frame has said how big a frame is, so the runner paints it onto its
        config when the first frame arrives - and the pipeline is what consults
        it. Building the config twice, once for each, gave the runner one
        object to paint and the pipeline another to read, and the operator's
        answer to a specific swaying tree silently did nothing at all.

        The imports are here rather than at the top of the file for the reason
        given there: this is where the vision stack starts being needed, and
        the console imports this module for two constants.
        """
        from vmd.detect.config import (
            classifier_for,
            config_from_settings,
            regions_of,
            shape_sizes_of,
            shapes_of,
        )
        from vmd.detect.runner import StreamDetector

        config = config_from_settings(stream, self.settings.detection)
        source = self._source_for(stream)
        return StreamDetector(
            source,
            stream.name,
            config,
            None,  # each thread opens its own store; see _work
            open_capture=open_capture,
            pipeline=self._pipeline_factory(config),
            ignore_regions=regions_of(stream),
            # And the areas he drew round rather than boxed. Both go, because a
            # settings file written before the drawing tool existed carries
            # rectangles and nothing already marked out may quietly come back.
            ignore_shapes=shapes_of(stream),
            shape_sizes=shape_sizes_of(stream),
            # Loads nothing here: the YOLO import is deferred to the first crop
            # worth naming, so this process starts on a machine with no torch
            # and no weights. Off for the thermal by default.
            classifier=classifier_for(stream, self.settings.detection),
            # Where the picture of what moved is written. The recordings root,
            # so it is inside what retention already looks after on a filling
            # disk - and so the console can work the path out from the event
            # rather than being told it. See `vmd/detect/stills.py`.
            stills_root=self.root,
            # The camera itself, when the local streaming server is what we
            # chose. Whether that server is the right one to read from was
            # decided once, from a port answering - which proves something is
            # listening on 127.0.0.1 and nothing about whether it serves this
            # stream. Without a second address, a go2rtc that had restarted
            # elsewhere or belonged to an older settings file took detection
            # off this stream permanently, and the status file blamed the
            # camera.
            fallback_url=stream.url if source != stream.url else "",
            # Which of the two `source` is, in the operator's words. The runner
            # cannot tell from an address, and the console has to be able to
            # say which streams are crossing the radio link twice.
            primary_source="local" if source != stream.url else "camera",
        )

    # -- where the frames come from ---------------------------------------

    def _source_for(self, stream) -> str:
        """Prefer the local streaming server, as the recorder does.

        The camera is already being pulled once and re-served on this machine.
        One more local consumer costs the radio link nothing; a second pull
        across the link would cost it the live picture.
        """
        local = local_source(self._endpoint, stream.name)
        if local:
            logger.info("detecting on %s from the local streaming server", stream.name)
            return local
        logger.info("detecting on %s directly from the camera", stream.name)
        return stream.url

    def _recheck_sources(self, now: float | None = None) -> None:
        """Ask `streaming.json` again where the local streaming server is.

        It was read exactly once, in `__init__`, and that answer expires. The
        console starts go2rtc on a free port, so a restart can bring it back
        somewhere else - and a detector that had fallen back to the camera
        would then keep offering to come back to an address nothing has
        answered on since, for ever, while a perfectly good server ran on the
        next port up. That is the same fault as the one this whole file is
        about, one level down, and it is what would have made the fix look like
        it worked and quietly not.

        The recorder does exactly this and says why at
        `SOURCE_CHECK_SECONDS` in vmd\\record_main.py. The two processes are
        deliberately the same shape here: how often is asked, what happens next
        is not, because moving a recorder means cutting the footage and moving
        a detector means a probe and a swap.

        Nothing here touches a capture. Each detector is handed a string and
        acts on it itself, on its own thread.
        """
        now = time.monotonic() if now is None else now
        if self._last_source_check is not None and 0 <= now - self._last_source_check < (
            SOURCE_CHECK_SECONDS
        ):
            return
        self._last_source_check = now
        try:
            endpoint = read_endpoint(self.endpoint_path)
            live = bool(endpoint) and is_live(endpoint)
        except Exception:  # noqa: BLE001 - housekeeping never ends the watch
            logger.exception("could not re-read %s; continuing", self.endpoint_path)
            return
        if not live:
            # No streaming server to be found. Detectors on the camera stay
            # there and keep asking; the address they ask about is the last one
            # that was real, which is the best guess available.
            return
        self._endpoint = endpoint
        for stream, detector in zip(self.streams, self.detectors):
            local = local_source(endpoint, stream.name)
            if local:
                detector.point_at_local(local)

    # -- running ------------------------------------------------------------

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def start(self) -> None:
        if self.threads:
            return
        for detector in self.detectors:
            thread = threading.Thread(
                target=self._work, args=(detector,), name=f"detect-{detector.stream}", daemon=True
            )
            self.threads.append(thread)
            thread.start()

    def _work(self, detector: StreamDetector) -> None:
        """One detector's whole life, including its own database connection.

        The connection is opened here rather than by the constructor because a
        sqlite connection belongs to the thread that created it. One file, one
        connection per thread, WAL doing what WAL is for.
        """
        store = None
        try:
            store = self._store_factory(self.events_path)
            detector.store = store
        except Exception:  # noqa: BLE001 - a stream still worth watching, just not recording
            logger.exception(
                "%s: the event store could not be opened; movement will be logged only",
                detector.stream,
            )
        try:
            detector.run()
        except Exception:  # noqa: BLE001 - one stream must never take the others down
            logger.exception("%s: detection stopped unexpectedly", detector.stream)
        finally:
            if store is not None:
                store.close()

    def run_forever(self, interval: float = 5.0) -> None:
        """Start the threads and watch them until stopped. Never raises."""
        self.start()
        if not self.detectors:
            logger.warning("no streams to detect on; nothing to do")
        # Published before the first wait, so a console that opens beside a
        # detector already running does not have to sit through an interval
        # before it can name the streams.
        self.write_status(interval)
        try:
            while not self._stop.wait(interval):
                # Asked here rather than on a detector's thread: it reads a
                # file and opens a socket, and the detector threads are for
                # decoding. Throttled to its own interval inside.
                self._recheck_sources()
                self._log_state_changes()
                self.write_status(interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            self.clear_status()

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the detector threads to finish."""
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self.threads:
            remaining = None if deadline is None else max(deadline - time.monotonic(), 0.0)
            thread.join(remaining)

    def stop(self) -> None:
        """Ask every detector to stop, and wait for it. Safe to call twice."""
        self._stop.set()
        for detector in self.detectors:
            detector.stop()
        self.wait(timeout=10.0)
        self.threads = [thread for thread in self.threads if thread.is_alive()]

    # -- reporting ----------------------------------------------------------

    def status(self) -> dict:
        streams = [detector.state() for detector in self.detectors]
        return {
            "streams": streams,
            # Deliberately a count, not a boolean. Detection running on the
            # thermal while the visible is unreachable is a normal Tuesday, and
            # one health flag would report that as failure.
            "detecting": sum(1 for s in streams if s["opened"]),
            # Open, and sending nothing. Counted apart from `detecting` because
            # a wedged read is counted in it and is the opposite of detecting.
            "stalled": sum(1 for s in streams if _stalled(s)),
            # Open, delivering at a healthy rate, and delivering a flat
            # rectangle. Counted apart from `stalled` because frames really are
            # arriving - it is only that there is nothing in them - and apart
            # from `detecting` because nothing there is being detected.
            "blind": sum(1 for s in streams if s.get("blind")),
            # Open, delivering, and delivering the same picture every time.
            "frozen": sum(1 for s in streams if _frozen(s)),
            # Open, delivering, and delivering too slowly for the confirmation
            # rule to confirm anything. See SLOW_STREAM_FPS.
            "slow": sum(1 for s in streams if _too_slow(s)),
            # The classifier is on for this stream and has never once named
            # anything. See NEVER_NAMED_AFTER: unlike everything else here this
            # is a note about an install, not about the perimeter.
            "never_named": sum(1 for s in streams if _never_named(s)),
            # Streams being read straight from the camera rather than through
            # the local streaming server. Each one is a second full-rate copy
            # of that stream on a >15 km, ~5 Mb/s radio link that barely
            # carries one - which the console spec calls "the difference
            # between recording and losing the live picture as well". Published
            # because the console is another process: a detector quietly
            # costing double the link is exactly the invisible fault this
            # project keeps repeating, and it used to be one warning line in a
            # ring of five hundred.
            #
            # A count, not a flag, for the same reason as `detecting`: one
            # stream on the camera while the other is local is a real state.
            # It does not say the fallback is wrong - a stream with no local
            # server is read from the camera on purpose - only that the link is
            # paying for it.
            "on_camera": sum(1 for s in streams if s.get("source") == "camera"),
            "configured": len(streams),
            "events": sum(s["events"] for s in streams),
            # Movement that was seen and confirmed but never reached the
            # database - a store that would not open, a disk that filled. It is
            # published separately because it is the one number that means the
            # operator's list is missing things it should have in it.
            "unrecorded": sum(s.get("unrecorded", 0) for s in streams),
            "events_db": str(self.events_path),
        }

    def write_status(self, interval: float = 5.0) -> None:
        """Publish `status()` where the console can read it. Never raises.

        The console is a separate process on purpose, so per-stream state that
        stays in this one is state the operator never sees - the console is left
        able to say only "detection is running", and a visible stream that has
        been unreachable since Tuesday looks exactly like one that is fine.
        A small JSON file is the seam this codebase already uses for a process
        publishing state; `streaming.json` is the same shape, read the same way.

        Written whole or not at all: a temporary file in the same directory,
        flushed to the platter, renamed over the destination. A rename is atomic
        within a filesystem, so a console reading at the wrong moment gets the
        whole of the previous write rather than half of this one - which it
        would otherwise report as a detector that has no streams.

        `written_at` and `interval` go in because the reader has to be able to
        tell stale from fresh, and cannot know how often this was told to write.

        A failure is logged and swallowed. A full disk should make the console
        say "unknown", which is true; it is not a reason to stop watching the
        perimeter.
        """
        payload = dict(self.status())
        payload["written_at"] = time.time()
        payload["interval"] = float(interval)
        try:
            _write_json_atomically(payload, self.status_path)
        except OSError:
            logger.warning("could not publish %s", self.status_path, exc_info=True)

    def clear_status(self) -> None:
        """Take the claim down on the way out.

        A detector that stopped cleanly and left its last report behind would
        have the console reading a dead process's words as current until the
        staleness window ran out.
        """
        try:
            self.status_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("could not remove %s", self.status_path, exc_info=True)

    def _log_state_changes(self) -> None:
        """Say something only when something changed.

        This process runs for months. A heartbeat every few seconds would bury
        the one line that matters in the Logs tab.
        """
        for detector in self.detectors:
            state = detector.state()
            self._say_if_never_named(detector, state)
            stalled = _stalled(state)
            slow = _too_slow(state)
            blind = bool(state.get("blind"))
            frozen = _frozen(state)
            # The source is part of what has changed, so that a stream which
            # moved onto the camera - or came back off it - produces a line
            # even when everything else about it reads the same. That change
            # is the one that costs the radio link a second copy of the
            # stream, and it used to be sayable only from the runner's own log.
            key = (
                state["opened"],
                state["reason"],
                stalled,
                blind,
                frozen,
                slow,
                state.get("source"),
            )
            if getattr(detector, "_last_logged", None) == key:
                continue
            detector._last_logged = key
            if not state["opened"]:
                logger.warning("%s: %s", state["stream"], state["reason"])
            elif stalled:
                # The read has not returned. Said as a warning because from
                # everywhere else this is indistinguishable from nothing having
                # walked past, and the two could not matter more differently.
                logger.warning(
                    "%s: the stream is open but has sent nothing for %.0f seconds; "
                    "nothing there is being watched",
                    state["stream"],
                    state["seconds_since_frame"],
                )
            elif blind:
                # Frames, at a healthy rate, with nothing in them. Said as a
                # warning for the same reason as a stall: from every other
                # reading this is a stream that is working.
                logger.warning(
                    "%s: frames are arriving but there is no picture in them - "
                    "%d in a row have been blank. Nothing there is being watched. "
                    "Check that this is the right stream and that the camera is "
                    "still sending video on it.",
                    state["stream"],
                    state.get("blank_frames", 0),
                )
            elif frozen:
                logger.warning(
                    "%s: the picture has not changed at all for %.0f seconds. "
                    "Either the stream has frozen on one frame or nothing in "
                    "view has moved; movement is found by comparing frames, so "
                    "nothing there can be detected while this is true.",
                    state["stream"],
                    state["seconds_since_change"],
                )
            elif slow:
                logger.warning(
                    "%s: only %.1f frames a second are arriving. Movement has to "
                    "be seen in three frames out of five before it counts, so at "
                    "this rate somebody can cross and be gone before the detector "
                    "has enough of them. Raise the frame rate for this stream.",
                    state["stream"],
                    state["fps"],
                )
            else:
                logger.info("%s: detecting, %s", state["stream"], _where_from(state))

    def _say_if_never_named(self, detector, state: dict) -> None:
        """Once per stream, and separate from its health.

        This is a note about an install - missing weights, a model that will
        not load, a budget missed on every call - and not a statement about the
        perimeter, so it does not belong in the chain above and must not
        displace a sentence that is.
        """
        if not _never_named(state) or getattr(detector, "_said_never_named", False):
            return
        detector._said_never_named = True
        logger.warning(
            "%s: the classifier has been asked to name %d things and has named "
            "none of them. Either everything it has seen is too small to name, "
            "which is normal at this range, or it is not working - look above "
            "for a line about the weights. Movement is still being reported "
            "either way; it just arrives without a label.",
            state["stream"],
            state.get("named_asked", 0),
        )


def _where_from(state: dict) -> str:
    """Which way to the picture this stream is being read through, in words.

    Said on every line that reports a healthy stream, because "detecting" was
    true both of a stream costing the radio link nothing and of one costing it
    a second full-rate copy, and the operator could not tell them apart.
    """
    if state.get("source") == "camera":
        return (
            "straight from the camera - that is a second copy of this stream "
            "across the radio link"
        )
    return "from the local streaming server"


def _stalled(state: dict) -> bool:
    """True when an open stream has gone quiet for longer than any camera would."""
    if not state.get("opened"):
        return False
    since = state.get("seconds_since_frame")
    return since is not None and since > STALLED_AFTER_SECONDS


def _never_named(state: dict) -> bool:
    """True when the classifier is on for this stream and has named nothing."""
    if not state.get("classifying"):
        return False
    return state.get("named_asked", 0) >= NEVER_NAMED_AFTER and state.get("named", 0) == 0


def _frozen(state: dict) -> bool:
    """True when an open, delivering stream has sent the same picture throughout."""
    if not state.get("opened") or state.get("blind"):
        # A blank stream is frozen too, by definition. It is reported as blank
        # instead, because "there is no picture" is a diagnosis and "the picture
        # is not changing" is a symptom of several things.
        return False
    since = state.get("seconds_since_change")
    return since is not None and since > FROZEN_AFTER_SECONDS


def _too_slow(state: dict) -> bool:
    """True when the stream is arriving too slowly for a track to be confirmed."""
    if not state.get("opened"):
        return False
    fps = state.get("fps")
    return fps is not None and fps < SLOW_STREAM_FPS


def _write_json_atomically(payload: dict, path: Path) -> None:
    """Write JSON so that the file on disk is never half written.

    The same shape as `save_settings`, and for the same reason: a plain write
    truncates first, so a reader arriving mid-write finds an empty or spliced
    file. The temporary file is in the destination's own directory because
    os.replace is only atomic within one filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(json.dumps(payload, indent=2))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def install_signal_handlers(service: DetectionService):
    """Stop cleanly on SIGTERM and Ctrl-C, and return the handler for testing.

    `taskkill` without /F and the console's own shutdown both arrive as SIGTERM.
    Without this the process is killed with two sqlite connections open, which
    leaves a -wal file the next run has to recover.
    """

    def handle(signum, _frame):
        logger.info("signal %s received; stopping detection", signum)
        service.stop()

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            signal.signal(number, handle)
        except (ValueError, OSError):
            # Not the main thread, or a signal this platform will not let us
            # take. Not a reason to refuse to detect.
            logger.debug("could not install a handler for %s", name)
    return handle


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vmd-detect", description="VMD detection service")
    parser.add_argument("--settings", default="settings.json", help="path to settings.json")
    parser.add_argument(
        "--streaming",
        default=None,
        help="where the console wrote the streaming server's ports "
        "(default: streaming.json beside the settings)",
    )
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between status checks")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    endpoint_path = (
        Path(args.streaming) if args.streaming else Path(args.settings).parent / "streaming.json"
    )
    try:
        settings = load_settings(args.settings)
    except SettingsError as exc:
        # A broken settings file must fail with a readable message, not a
        # traceback: nothing restarts this process on its own.
        logger.error("%s", exc)
        return 1

    if Path(args.settings).exists():
        logger.info("settings loaded from %s", Path(args.settings).resolve())
    else:
        logger.warning("no settings file at %s; using defaults", Path(args.settings).resolve())

    if not detected_streams(settings):
        # Not an error. The operator may be recording only, and a process that
        # spun on an empty list would look like a working detector.
        print(f"no stream has detection enabled in {args.settings}; nothing to detect")
        return 0

    service = DetectionService(settings, endpoint_path=endpoint_path)
    if args.once:
        try:
            for detector in service.detectors:
                store = EventStore(service.events_path)
                detector.store = store
                try:
                    detector.step()
                finally:
                    store.close()
            print(service.status())
        finally:
            service.stop()
        return 0

    install_signal_handlers(service)
    service.run_forever(interval=args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
