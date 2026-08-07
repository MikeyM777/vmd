"""Deciding which segment files ffmpeg has finished writing."""

from __future__ import annotations

import datetime
from pathlib import Path

SEGMENT_FORMAT = "%Y-%m-%d_%H-%M-%S"


def parse_segment_start(filename: str) -> float | None:
    """Epoch seconds encoded in a segment filename, or None if it does not match."""
    stem = Path(filename).stem
    try:
        return datetime.datetime.strptime(stem, SEGMENT_FORMAT).timestamp()
    except ValueError:
        return None


def find_closed_segments(
    directory: str | Path,
    now: float,
    settle_seconds: float = 5.0,
    seen: set[str] | None = None,
) -> list[Path]:
    """Segment files that are finished and not yet indexed.

    A file counts as finished when a newer file exists (ffmpeg has moved on) and it has
    not been written to for `settle_seconds`. Empty files and non-mp4 files are ignored.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    seen = seen or set()

    candidates = []
    for path in directory.glob("*.mp4"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size == 0:
            continue
        candidates.append((stat.st_mtime, path))

    if len(candidates) < 2:
        return []  # the only file present is the one being written

    candidates.sort()
    newest_mtime = candidates[-1][0]
    closed = []
    for mtime, path in candidates:
        if mtime == newest_mtime:
            continue
        if now - mtime < settle_seconds:
            continue
        if str(path) in seen:
            continue
        closed.append(path)
    return closed
