"""What the playback timeline draws, and what a click on it means.

Coverage comes from the segment index, which knows the exact start and end of
every file on disk. The bar therefore shows what was actually recorded,
including the gaps - a timeline that draws an unbroken day it cannot prove is
worse than no timeline.

Known limitation - daylight saving: `day_bounds` computes midnight in local
time and adds a fixed 86400 seconds to get the "end" of the day. On a real
DST transition the local day is 23 or 25 hours long, so that arithmetic does
not land exactly on the following midnight. In the deployment locale
(Israel), DST ends in late October with clocks moving back an hour, so the
naive `start + DAY_SECONDS` lands one hour *before* the real next midnight -
the operator's calendar day view would be missing an hour of footage from
the end of that day (it would show up when they pick the *next* date
instead). In spring, DST starts with clocks moving forward, and the naive
end would land one hour *past* the real midnight, pulling an hour of the
next day's footage into this one. This is a known, reported limitation, not
silently patched: switching to UTC would fix the arithmetic but would change
what a "day" means to the operator, which is a design decision for whoever
owns this screen, not something to decide unilaterally here.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from vmd.storage.index import Segment

DAY_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class SeekTarget:
    """A file and how far into it to start."""

    path: str
    offset_seconds: float


def day_bounds(year: int, month: int, day: int) -> tuple[float, float]:
    """Midnight to midnight, in local time, as epoch seconds.

    Local rather than UTC because the operator picks a date from a calendar and
    means their own day. Segment filenames are UTC, which is a different problem
    already solved in storage.

    See the module docstring for the DST limitation this simple arithmetic has.
    """
    start = datetime.datetime(year, month, day).timestamp()
    return (start, start + DAY_SECONDS)


def coverage_bars(segments: list[Segment], day_start: float) -> list[tuple[float, float]]:
    """(left, width) as fractions of the day, for every segment that touches it.

    Segments are not assumed sorted. Overlapping segments each produce their own
    bar rather than being merged - the caller draws what the index actually says,
    including double coverage, rather than an idealised union.
    """
    day_end = day_start + DAY_SECONDS
    bars: list[tuple[float, float]] = []
    for segment in segments:
        start = max(segment.start, day_start)
        end = min(segment.end, day_end)
        if end <= start:
            continue
        bars.append(((start - day_start) / DAY_SECONDS, (end - start) / DAY_SECONDS))
    return bars


def time_at(fraction: float, day_start: float) -> float:
    """The epoch time a click at this fraction of the width means."""
    fraction = min(max(fraction, 0.0), 1.0)
    return day_start + fraction * DAY_SECONDS


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
