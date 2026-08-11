"""The recording service: record every enabled stream, index it, enforce retention."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Callable

from vmd.settings import Settings, SettingsError, load_settings
from vmd.streaming.endpoint import is_live, local_source, read_endpoint
from vmd.storage.discovery import find_closed_segments, parse_segment_start
from vmd.storage.index import SegmentIndex
from vmd.storage.recorder import SegmentRecorder
from vmd.storage.retention import apply_plan, plan_retention
from vmd.supervisor import Managed, Supervisor

logger = logging.getLogger(__name__)

# Written by the console when it starts the streaming server, beside the
# settings it was started with.
DEFAULT_ENDPOINT_PATH = Path("streaming.json")

# The detector's database, beside this service's own. Opened only if it is
# already there; see _event_store.
EVENTS_FILENAME = "events.db"


class RecordingService:
    """Owns the recorders, the index and the retention pass."""

    def __init__(
        self,
        settings: Settings,
        spawn: Callable | None = None,
        retention_interval: float = 60.0,
        settle_seconds: float = 5.0,
        endpoint_path: str | Path | None = None,
    ) -> None:
        self.settings = settings
        endpoint = read_endpoint(endpoint_path or DEFAULT_ENDPOINT_PATH)
        self._endpoint = endpoint if endpoint and is_live(endpoint) else None
        # How long a file must sit untouched before it counts as finished. The
        # same window guards both discovery and orphan adoption; adoption used
        # to have none, which made it the weaker of the two paths.
        self.settle_seconds = settle_seconds
        self.root = Path(settings.storage.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = SegmentIndex(self.root / "segments.db")
        # Opened on demand rather than here; see _event_store.
        self.events_path = self.root / EVENTS_FILENAME
        self._events = None
        self._events_failures = 0
        try:
            recorder_kwargs = {"spawn": spawn} if spawn else {}
            self.recorders = [
                SegmentRecorder(
                    stream=stream.name,
                    source_url=self._source_for(stream),
                    output_dir=self.root / stream.name,
                    segment_seconds=settings.storage.segment_seconds,
                    **recorder_kwargs,
                )
                for stream in settings.camera.streams
                if stream.enabled
            ]
            self.supervisor = Supervisor(
                [Managed(name=r.stream, service=r) for r in self.recorders]
            )
            self._seen: set[str] = {s.path for s in self.index.all()}
            self._last_warning: str | None = None
            # Retention runs on its own slower cadence; see _apply_retention.
            self.retention_interval = retention_interval
            self._last_retention = 0.0
            self._stuck_deletions = 0
            self._last_segment_at: dict[str, float] = {}
            self._started_at: dict[str, float] = {}
            self._stall_restarts = 0
            self._stage_failures: dict[str, int] = {}
            self._adopt_orphans()
        except Exception:
            # Nothing will hold a reference to this half-built service, so the
            # connection would leak. On Windows a lingering handle can make an
            # immediate retry fail with "database is locked".
            self.index.close()
            raise

    def _event_store(self):
        """The movement events, if a detector has ever written any.

        Opened lazily, and only when the file already exists. Opening it in the
        constructor would be simpler, and would create events.db on every
        machine - including the ones where detection was never turned on, where
        an empty database beside the recordings is a thing an operator has to
        ask about and a thing a backup has to carry. The recorder is not the
        detector, and it should not leave the detector's fingerprints.

        Checked on every retention pass rather than once, because detection can
        be ticked on in the Settings tab one afternoon while this service has
        been running since March: the store appears underneath a process that
        has already decided there was none, and the events written from then on
        must still be reclaimed with the footage they point at.

        The import is local for the same reason: `vmd.detect` pulls in the
        detector's whole stack, and a machine that only records must not need
        it installed to record.
        """
        if self._events is not None:
            return self._events
        if not self.events_path.exists():
            return None
        try:
            from vmd.detect.events import EventStore

            self._events = EventStore(self.events_path)
        except Exception:  # noqa: BLE001 - retention frees the disk with or without this
            self._events_failures += 1
            # Loud the first few times, then rare. This is retried every
            # retention pass for as long as the fault lasts, and a fault that
            # lasts for days must stay visible without burying the log.
            if self._events_failures <= 3 or self._events_failures % 100 == 0:
                logger.exception(
                    "the movement events could not be opened (%d times); "
                    "footage will be reclaimed without them",
                    self._events_failures,
                )
            self._events = None
        return self._events

    def _source_for(self, stream) -> str:
        """Prefer the local streaming server over the camera.

        The console already holds one connection to the camera and re-serves it
        on this machine. Recording from there means the stream crosses the radio
        link once instead of twice - which on a five megabit link is the
        difference between recording and losing the live picture as well.

        If the streaming server is not running, the camera is used directly:
        recording something is more important than recording it cheaply.
        """
        local = local_source(self._endpoint, stream.name)
        if local:
            logger.info("recording %s from the local streaming server", stream.name)
            return local
        logger.info("recording %s directly from the camera", stream.name)
        return stream.url

    def run_once(self, now: float | None = None) -> None:
        """One pass: keep recorders alive, index finished segments, apply retention.

        Each stage is isolated from the others. They used to run in sequence with
        no guard, which made a full disk self-locking: the index write failed,
        the exception ended the pass before retention could run, so nothing was
        deleted, so the disk stayed full - forever, and reported healthy.
        Retention is the stage that frees the disk, so it must run even when
        everything before it has failed.
        """
        now = time.time() if now is None else now
        for recorder in self.recorders:
            self._started_at.setdefault(recorder.stream, now)
        self._stage("supervisor", self.supervisor.tick)
        self._stage("indexing", self._index_new_segments, now)
        self._stage("stall check", self._restart_stalled, now)
        self._stage("retention", self._apply_retention, now)

    def _stage(self, name: str, work, *args) -> None:
        try:
            work(*args)
        except Exception:  # noqa: BLE001 - one broken stage must not skip the rest
            self._stage_failures[name] = self._stage_failures.get(name, 0) + 1
            count = self._stage_failures[name]
            # Loud the first few times, then rare: a fault that lasts for days
            # must stay visible in the log without burying everything else.
            if count <= 3 or count % 100 == 0:
                logger.exception("%s failed (%d times); continuing", name, count)

    def run_forever(self, interval: float = 5.0) -> None:
        """Run until interrupted. A failed pass must never end the process.

        This runs unattended for months. Any exception escaping run_once would stop
        recording permanently, since nothing outside this process restarts it, so a
        failed pass is logged and the loop continues. The sleep happens on the failure
        path too: without it a persistent fault, such as a full disk, would become a
        tight busy loop.
        """
        try:
            while True:
                try:
                    self.run_once()
                except Exception:  # noqa: BLE001 - a bad pass must not end the service
                    logger.exception("recording pass failed; continuing")
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        self.supervisor.stop_all()
        # stop_all() blocks until each ffmpeg has exited, so the segment it was writing
        # is now closed and valid. Index it before shutting down, or it stays on disk
        # while being invisible to the budget.
        try:
            self._index_new_segments(time.time())
        except Exception:  # noqa: BLE001 - shutdown must always complete
            logger.exception("final indexing pass failed")
        if self._events is not None:
            try:
                self._events.close()
            except Exception:  # noqa: BLE001 - shutdown must always complete
                logger.exception("the movement events would not close")
            self._events = None
        self.index.close()

    def status(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        stalled = set(self.stalled_streams(now))
        segments = self.index.all()
        used = sum(s.size_bytes for s in segments)
        oldest = segments[0].start if segments else None
        streams = [
            {
                "name": r.stream,
                "running": r.running,
                "stalled": r.stream in stalled,
                "restarts": self.supervisor.restarts.get(r.stream, 0),
                "exit_code": r.exit_code,
            }
            for r in self.recorders
        ]
        return {
            "streams": streams,
            # A stream that never starts successfully keeps `restarts` at zero, so
            # health must be derived from `running`, never from the restart count.
            # `all([])` is True, so a service with no streams at all would otherwise
            # report itself healthy while recording nothing. The CLI refuses to start
            # in that state, but status() is about to become a web API and must be
            # trustworthy on its own.
            "healthy": (
                bool(streams)
                and all(s["running"] and not s["stalled"] for s in streams)
                and not self._stuck_deletions
            ),
            "segments": len(segments),
            "used_bytes": used,
            "budget_bytes": self.settings.storage.budget_bytes,
            "oldest": oldest,
            "warning": self._last_warning,
            "stuck_deletions": self._stuck_deletions,
            "stall_restarts": self._stall_restarts,
            "restarts": dict(self.supervisor.restarts),
        }

    def _index_new_segments(self, now: float) -> None:
        for recorder in self.recorders:
            for path in find_closed_segments(
                recorder.output_dir, now=now, settle_seconds=self.settle_seconds, seen=self._seen
            ):
                start = parse_segment_start(path.name)
                if start is None:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                # The observed close time, not the nominal duration. A recorder that
                # died mid-segment wrote a short file, and recording that honestly is
                # what makes a dropout visible in the coverage timeline instead of
                # being papered over.
                end = max(stat.st_mtime, start)
                self.index.add(
                    stream=recorder.stream,
                    path=str(path),
                    start=start,
                    end=end,
                    size_bytes=stat.st_size,
                )
                self._seen.add(str(path))
                self._last_segment_at[recorder.stream] = now

    def stalled_streams(self, now: float | None = None) -> list[str]:
        """Streams whose process is alive but which have produced nothing recently.

        `running` only says the ffmpeg process exists. On a long wireless link the
        RTSP socket can die without closing, leaving ffmpeg blocked on a read: the
        process is alive, the supervisor is satisfied, and nothing is recorded.
        Segment production is the only signal that distinguishes the two.
        """
        now = time.time() if now is None else now
        limit = 2 * self.settings.storage.segment_seconds
        stalled = []
        for recorder in self.recorders:
            if not recorder.running:
                continue  # already visibly down; the supervisor handles that
            last = self._last_segment_at.get(
                recorder.stream, self._started_at.get(recorder.stream, now)
            )
            if now - last > limit:
                stalled.append(recorder.stream)
        return stalled

    def _restart_stalled(self, now: float) -> None:
        """Stop any stream that is alive but producing nothing, so it gets restarted.

        The supervisor only restarts a recorder whose process has exited. A recorder
        blocked on a dead RTSP socket reports itself as running indefinitely, so
        without this it would never recover on its own - detection alone would leave
        the stream dead until somebody looked at a dashboard.
        """
        limit = 2 * self.settings.storage.segment_seconds
        for stream in self.stalled_streams(now):
            recorder = next((r for r in self.recorders if r.stream == stream), None)
            if recorder is None:
                continue
            logger.warning(
                "%s is alive but has produced no segment for over %.0fs; restarting it",
                stream,
                limit,
            )
            recorder.stop()
            # Give the restarted recorder a fresh grace period, or it would be judged
            # stalled again before it has had time to write anything.
            self._started_at[stream] = now
            self._last_segment_at.pop(stream, None)
            self._stall_restarts += 1

    def _adopt_orphans(self) -> None:
        """Index segments already on disk that no current recorder is responsible for.

        Renaming or disabling a stream leaves its recordings behind. Without this they
        occupy the storage budget forever while being invisible to retention.

        Directories belonging to a currently configured recorder are deliberately
        skipped. Those are handled by _index_new_segments, which uses
        find_closed_segments and therefore never touches the file ffmpeg still has
        open. Sweeping them here would index the in-progress segment and expose a live
        recording to retention.
        """
        owned = {recorder.stream for recorder in self.recorders}
        for directory in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if directory.name in owned:
                continue
            candidates = []
            for path in directory.glob("*.mp4"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size == 0:
                    continue
                candidates.append((stat.st_mtime, path, stat))
            if not candidates:
                continue
            candidates.sort()
            # Skip the newest file, exactly as find_closed_segments does. If anything
            # is still writing into this directory it is that file, and indexing it
            # would expose a live recording to retention.
            #
            # "Newest" by mtime alone is not enough. A backwards clock step makes
            # a file written before the step look newer than the one being
            # written now, so the live file stops being last in this list. The
            # settle window closes that: a file touched within it is treated as
            # possibly still open, whatever the ordering says.
            settle = max(self.settle_seconds, 0.0)
            wall_now = time.time()
            for mtime, path, stat in candidates[:-1]:
                if wall_now - mtime < settle:
                    continue
                if str(path) in self._seen:
                    continue
                start = parse_segment_start(path.name)
                if start is None:
                    continue
                self.index.add(
                    stream=directory.name,
                    path=str(path),
                    start=start,
                    end=max(mtime, start),
                    size_bytes=stat.st_size,
                    commit=False,
                )
                self._seen.add(str(path))
                logger.info("adopted orphaned segment %s", path.name)
        self.index.commit()

    def _apply_retention(self, now: float) -> None:
        # Retention reads the entire index, which is expensive once the catalogue is
        # large. Its input only changes when a segment closes, so running it on the
        # 5-second loop cadence would be pure waste.
        # The elapsed check deliberately tolerates a clock that moves backwards. This
        # machine may correct its time by NTP after boot, and a backwards step would
        # otherwise stall retention for the length of the jump while the disk fills.
        # A negative elapsed means the clock changed, so run rather than wait.
        elapsed = now - self._last_retention
        if self._last_retention and 0 <= elapsed < self.retention_interval:
            return
        self._last_retention = now

        storage = self.settings.storage
        segments = self.index.all()
        plan = plan_retention(
            segments,
            now=now,
            budget_bytes=storage.budget_bytes,
            budget_enabled=storage.budget_enabled,
            retention_days=storage.retention_days,
            warn_at_fraction=storage.warn_at_fraction,
            bytes_per_second=self._write_rate(segments),
        )
        self._last_warning = plan.warning
        if plan.warning:
            logger.warning(plan.warning)
        # The events go with the footage they point at, or the movement list
        # ends up offering to play files that were reclaimed months ago.
        removed = apply_plan(plan, self.index, events=self._event_store())
        for segment in removed:
            self._seen.discard(segment.path)
        if removed:
            logger.info("retention removed %d segments", len(removed))

        # A file that cannot be deleted is retried forever. Counting only what was
        # removed would report a healthy number every pass while the budget is never
        # actually met, so the shortfall is tracked and surfaced in status().
        self._stuck_deletions = len(plan.delete) - len(removed)
        if self._stuck_deletions:
            logger.warning(
                "%d segment(s) could not be deleted; storage budget cannot be met",
                self._stuck_deletions,
            )

    @staticmethod
    def _write_rate(segments) -> float:
        """Bytes per second, measured from what has actually been recorded.

        This averages over the entire retained history, so a long outage makes the
        figure read low. It feeds only the operator-facing estimate of when footage
        will be deleted, never a deletion decision, so an optimistic estimate after a
        link outage is a display inaccuracy rather than a data-loss risk.
        """
        if len(segments) < 2:
            return 0.0
        span = segments[-1].end - segments[0].start
        if span <= 0:
            return 0.0
        return sum(s.size_bytes for s in segments) / span


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vmd-record", description="VMD recording service")
    parser.add_argument("--settings", default="settings.json", help="path to settings.json")
    parser.add_argument(
        "--streaming",
        default=None,
        help="where the console wrote the streaming server's ports "
        "(default: streaming.json beside the settings)",
    )
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between passes")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    endpoint_path = Path(args.streaming) if args.streaming else Path(args.settings).parent / "streaming.json"
    try:
        settings = load_settings(args.settings)
    except SettingsError as exc:
        # A broken settings file must fail with a readable message, not a traceback.
        # Nothing restarts this process, so an unhandled error here means the machine
        # records nothing until somebody notices.
        logger.error("%s", exc)
        return 1
    # Say which file was used. Without this, "running with defaults" looks identical
    # whether it is a genuine first run or a mistyped path.
    if Path(args.settings).exists():
        logger.info("settings loaded from %s", Path(args.settings).resolve())
    else:
        logger.warning(
            "no settings file at %s; using defaults", Path(args.settings).resolve()
        )
    if not [s for s in settings.camera.streams if s.enabled]:
        print(f"no enabled streams in {args.settings}; nothing to record")
        return 1
    service = RecordingService(settings, endpoint_path=endpoint_path)
    if args.once:
        try:
            service.run_once()
            print(service.status())
        finally:
            service.stop()
        return 0
    service.run_forever(interval=args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
