"""The recording service: record every enabled stream, index it, enforce retention."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Callable

from vmd.settings import Settings, load_settings
from vmd.storage.discovery import find_closed_segments, parse_segment_start
from vmd.storage.index import SegmentIndex
from vmd.storage.recorder import SegmentRecorder
from vmd.storage.retention import apply_plan, plan_retention
from vmd.supervisor import Managed, Supervisor

logger = logging.getLogger(__name__)


class RecordingService:
    """Owns the recorders, the index and the retention pass."""

    def __init__(
        self,
        settings: Settings,
        spawn: Callable | None = None,
        retention_interval: float = 60.0,
    ) -> None:
        self.settings = settings
        self.root = Path(settings.storage.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = SegmentIndex(self.root / "segments.db")

        recorder_kwargs = {"spawn": spawn} if spawn else {}
        self.recorders = [
            SegmentRecorder(
                stream=stream.name,
                source_url=stream.url,
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

    def run_once(self, now: float | None = None) -> None:
        """One pass: keep recorders alive, index finished segments, apply retention."""
        now = time.time() if now is None else now
        self.supervisor.tick()
        self._index_new_segments(now)
        self._apply_retention(now)

    def run_forever(self, interval: float = 5.0) -> None:
        try:
            while True:
                self.run_once()
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        self.supervisor.stop_all()
        self.index.close()

    def status(self) -> dict:
        segments = self.index.all()
        used = sum(s.size_bytes for s in segments)
        oldest = segments[0].start if segments else None
        streams = [
            {
                "name": r.stream,
                "running": r.running,
                "restarts": self.supervisor.restarts.get(r.stream, 0),
                "exit_code": r.exit_code,
            }
            for r in self.recorders
        ]
        return {
            "streams": streams,
            # A stream that never starts successfully keeps `restarts` at zero, so
            # health must be derived from `running`, never from the restart count.
            "healthy": all(s["running"] for s in streams) and not self._stuck_deletions,
            "segments": len(segments),
            "used_bytes": used,
            "budget_bytes": self.settings.storage.budget_bytes,
            "oldest": oldest,
            "warning": self._last_warning,
            "stuck_deletions": self._stuck_deletions,
            "restarts": dict(self.supervisor.restarts),
        }

    def _index_new_segments(self, now: float) -> None:
        for recorder in self.recorders:
            for path in find_closed_segments(recorder.output_dir, now=now, seen=self._seen):
                start = parse_segment_start(path.name)
                if start is None:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                self.index.add(
                    stream=recorder.stream,
                    path=str(path),
                    start=start,
                    end=start + self.settings.storage.segment_seconds,
                    size_bytes=size,
                )
                self._seen.add(str(path))

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
        removed = apply_plan(plan, self.index)
        if removed:
            for segment in plan.delete:
                self._seen.discard(segment.path)
            logger.info("retention removed %d segments", removed)

        # A file that cannot be deleted is retried forever. Counting only what was
        # removed would report a healthy number every pass while the budget is never
        # actually met, so the shortfall is tracked and surfaced in status().
        self._stuck_deletions = len(plan.delete) - removed
        if self._stuck_deletions:
            logger.warning(
                "%d segment(s) could not be deleted; storage budget cannot be met",
                self._stuck_deletions,
            )

    @staticmethod
    def _write_rate(segments) -> float:
        """Bytes per second, measured from what has actually been recorded."""
        if len(segments) < 2:
            return 0.0
        span = segments[-1].end - segments[0].start
        if span <= 0:
            return 0.0
        return sum(s.size_bytes for s in segments) / span


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vmd-record", description="VMD recording service")
    parser.add_argument("--settings", default="settings.json", help="path to settings.json")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between passes")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    settings = load_settings(args.settings)
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
    service = RecordingService(settings)
    if args.once:
        service.run_once()
        print(service.status())
        service.stop()
        return 0
    service.run_forever(interval=args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
