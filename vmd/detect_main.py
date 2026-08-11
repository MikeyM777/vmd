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
from typing import Callable

from vmd.detect.config import classifier_for, config_from_settings, regions_of
from vmd.detect.events import EventStore
from vmd.detect.pipeline import DetectionPipeline
from vmd.detect.runner import StreamDetector, open_capture_cv2
from vmd.settings import Settings, SettingsError, load_settings
from vmd.streaming.endpoint import is_live, local_source, read_endpoint

logger = logging.getLogger(__name__)

# Written by the console when it starts the streaming server, beside the
# settings it was started with. The same file the recorder reads.
DEFAULT_ENDPOINT_PATH = Path("streaming.json")

EVENTS_FILENAME = "events.db"

# Where the per-stream state is published for the console, beside events.db.
# `vmd.desktop.services` repeats this name rather than importing it: importing
# this module would pull cv2 and numpy into the window's process.
STATUS_FILENAME = "detection.json"

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
        open_capture: Callable = open_capture_cv2,
        pipeline_factory: Callable | None = None,
        store_factory: Callable[[Path], EventStore] = EventStore,
    ) -> None:
        self.settings = settings
        endpoint = read_endpoint(endpoint_path or DEFAULT_ENDPOINT_PATH)
        self._endpoint = endpoint if endpoint and is_live(endpoint) else None
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

        self.detectors = [
            self._detector_for(stream, open_capture) for stream in detected_streams(settings)
        ]

    def _detector_for(self, stream, open_capture) -> StreamDetector:
        """One stream's detector, built around **one** config object.

        The single object matters. The ignore mask cannot be built until a
        frame has said how big a frame is, so the runner paints it onto its
        config when the first frame arrives - and the pipeline is what consults
        it. Building the config twice, once for each, gave the runner one
        object to paint and the pipeline another to read, and the operator's
        answer to a specific swaying tree silently did nothing at all.
        """
        config = config_from_settings(stream, self.settings.detection)
        return StreamDetector(
            self._source_for(stream),
            stream.name,
            config,
            None,  # each thread opens its own store; see _work
            open_capture=open_capture,
            pipeline=self._pipeline_factory(config),
            ignore_regions=regions_of(stream),
            # Loads nothing here: the YOLO import is deferred to the first crop
            # worth naming, so this process starts on a machine with no torch
            # and no weights. Off for the thermal by default.
            classifier=classifier_for(stream, self.settings.detection),
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
            stalled = _stalled(state)
            key = (state["opened"], state["reason"], stalled)
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
            else:
                logger.info("%s: detecting", state["stream"])


def _stalled(state: dict) -> bool:
    """True when an open stream has gone quiet for longer than any camera would."""
    if not state.get("opened"):
        return False
    since = state.get("seconds_since_frame")
    return since is not None and since > STALLED_AFTER_SECONDS


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
