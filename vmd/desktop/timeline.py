"""What the playback timeline draws, and what a click on it means.

Coverage comes from the segment index, which knows the exact start and end of
every file on disk. The bar therefore shows what was actually recorded,
including the gaps - a timeline that draws an unbroken day it cannot prove is
worse than no timeline.

A local day is not always 86400 seconds. On a daylight-saving transition it is
23 or 25 hours - in the deployment locale (Israel) the spring transition
shortens the day and the autumn transition lengthens it by an hour. An earlier
version of `day_bounds` computed the end of the day as `start + 86400`, which
on the 25-hour autumn date landed one hour before the real midnight: the last
hour of that day's footage would have silently fallen off the end of the bar
and only appeared under the following day instead. That is not acceptable on
a system whose purpose is that nothing gets past it, so `day_bounds` now
advances the calendar date and re-resolves through the local zone instead of
assuming a fixed length, and `coverage_bars`/`time_at` are given the resulting
span rather than assuming one, so a 23- or 25-hour day is drawn at its actual
length.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from vmd.storage.index import Segment


@dataclass(frozen=True)
class SeekTarget:
    """A file and how far into it to start."""

    path: str
    offset_seconds: float


def day_bounds(year: int, month: int, day: int) -> tuple[float, float]:
    """Start of this local day and start of the next, as epoch seconds.

    Local rather than UTC because the operator picks a date from a calendar and
    means their own day. Segment filenames are UTC, which is a different problem
    already solved in storage.

    `timedelta(days=1)` on a naive datetime advances the calendar date; each of
    the two naive datetimes is then resolved to epoch seconds through the local
    zone independently, so the gap between them is the real length of that
    local day - 23, 24, or 25 hours - rather than a fixed 86400.
    """
    start = datetime.datetime(year, month, day)
    end = start + datetime.timedelta(days=1)
    return (start.timestamp(), end.timestamp())


def coverage_bars(
    segments: list[Segment], day_start: float, day_end: float
) -> list[tuple[float, float]]:
    """(left, width) as fractions of the day, for every segment that touches it.

    Segments are not assumed sorted. Overlapping segments each produce their own
    bar rather than being merged - the caller draws what the index actually says,
    including double coverage, rather than an idealised union.

    Fractions are of the actual span (`day_end - day_start`), not a fixed
    86400, so a 23- or 25-hour DST day still fills the bar edge to edge.
    """
    span = day_end - day_start
    bars: list[tuple[float, float]] = []
    for segment in segments:
        start = max(segment.start, day_start)
        end = min(segment.end, day_end)
        if end <= start:
            continue
        bars.append(((start - day_start) / span, (end - start) / span))
    return bars


def time_at(fraction: float, day_start: float, day_end: float) -> float:
    """The epoch time a click at this fraction of the width means."""
    fraction = min(max(fraction, 0.0), 1.0)
    return day_start + fraction * (day_end - day_start)


def seek_target(segments: list[Segment], when: float) -> SeekTarget | None:
    """The file covering this moment, and how far into it, or None for a gap.

    Segments are not assumed sorted. A moment exactly on the boundary between
    two adjacent segments belongs to the later one - each segment's start is
    inclusive and its end is exclusive.
    """
    for segment in segments:
        if segment.start <= when < segment.end:
            return SeekTarget(path=segment.path, offset_seconds=when - segment.start)
    return None
