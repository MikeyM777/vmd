"""The detection service: watch every stream the operator asked to watch.

A separate process, exactly like the recorder, for exactly the same reason: the
console must not be able to stop detection, and detection must not be able to
stop the console. It shares nothing with the recorder but the local stream, so
**detection stopping never stops recording.**

    go2rtc  --> detector process --> events.db --> console
       \\----> recorder --> segments + segments.db

Logging goes to stdout, which is where the console's Logs tab reads it from.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Callable

from vmd.detect.config import config_from_settings, regions_of
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

        self._store_factory = store_factory
        self._pipeline_factory = pipeline_factory or (lambda config: DetectionPipeline(config))
        self._stop = threading.Event()
        self.threads: list[threading.Thread] = []

        self.detectors = [
            StreamDetector(
                self._source_for(stream),
                stream.name,
                config_from_settings(stream, settings.detection),
                None,  # each thread opens its own store; see _work
                open_capture=open_capture,
                pipeline=self._pipeline_factory(config_from_settings(stream, settings.detection)),
                ignore_regions=regions_of(stream),
            )
            for stream in detected_streams(settings)
        ]

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
        try:
            while not self._stop.wait(interval):
                self._log_state_changes()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

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
            "configured": len(streams),
            "events": sum(s["events"] for s in streams),
            "events_db": str(self.events_path),
        }

    def _log_state_changes(self) -> None:
        """Say something only when something changed.

        This process runs for months. A heartbeat every few seconds would bury
        the one line that matters in the Logs tab.
        """
        for detector in self.detectors:
            state = detector.state()
            key = (state["opened"], state["reason"])
            if getattr(detector, "_last_logged", None) == key:
                continue
            detector._last_logged = key
            if state["opened"]:
                logger.info("%s: detecting", state["stream"])
            else:
                logger.warning("%s: %s", state["stream"], state["reason"])


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
