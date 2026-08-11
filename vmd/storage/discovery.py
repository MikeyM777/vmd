"""Deciding which segment files ffmpeg has finished writing."""

from __future__ import annotations

import bisect
import datetime
from pathlib import Path

SEGMENT_FORMAT = "%Y-%m-%d_%H-%M-%S"

# Names may carry which ffmpeg run wrote them, as `_<number>` after the time.
#
# ffmpeg builds the name from the wall clock, and its segment muxer opens
# whatever name comes out for *writing*, which truncates a file already there.
# On a machine whose date is typed in by hand, a clock set back an hour produces
# an hour of names that already exist and silently overwrites an hour of
# footage. A number that changes with every run puts each run's names in a space
# of their own, so no run can ever be handed a name another one wrote.
#
# Optional, because every archive recorded before this has names without one and
# those files must go on reading exactly as they did.
RUN_SEPARATOR = "_"


def split_run(stem: str) -> tuple[str, int]:
    """A name's time part and the run that wrote it; run 0 means it does not say.

    Unambiguous by construction: the last field of a bare segment name is
    `%H-%M-%S`, which is never all digits, so a trailing all-digit field can only
    be a run number.
    """
    head, separator, tail = stem.rpartition(RUN_SEPARATOR)
    if separator and tail.isdigit():
        return head, int(tail)
    return stem, 0


def parse_segment_start(filename: str) -> float | None:
    """Epoch seconds encoded in a segment filename, or None if it does not match.

    Filenames are UTC: the recorder runs ffmpeg with TZ=UTC so that names stay monotonic
    across daylight-saving transitions. Reading them as local time would shift every
    timestamp by the UTC offset.
    """
    stem, _run = split_run(Path(filename).stem)
    try:
        parsed = datetime.datetime.strptime(stem, SEGMENT_FORMAT)
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.timezone.utc).timestamp()


def highest_run(directory: str | Path) -> int:
    """The largest run number any segment in this directory carries.

    Read from the files rather than remembered, because the recorder is
    restarted - by the logon task, by the console, by itself - and a counter
    that lived only in memory would start again at the same value every time and
    collide with what the last process wrote.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return 0
    highest = 0
    try:
        for path in directory.glob("*.mp4"):
            _stem, run = split_run(path.stem)
            highest = max(highest, run)
    except OSError:
        return highest
    return highest


def segment_starts(directory: str | Path) -> list[float]:
    """Every segment start this directory holds, in order.

    Includes the file ffmpeg currently has open. That file is not indexable, but
    its name says when the one before it stopped, which is the only thing here
    that knows where a segment's coverage really ends.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    starts = [parse_segment_start(path.name) for path in directory.glob("*.mp4")]
    return sorted(start for start in starts if start is not None)


def next_segment_start(starts: list[float], start: float) -> float | None:
    """The first start after this one, from a sorted list, or None if it is last."""
    index = bisect.bisect_right(starts, start)
    return starts[index] if index < len(starts) else None


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
    closed = []
    for mtime, path in candidates[:-1]:
        if now - mtime < settle_seconds:
            continue
        if str(path) in seen:
            continue
        closed.append(path)
    return closed
