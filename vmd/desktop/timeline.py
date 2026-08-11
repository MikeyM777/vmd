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
from dataclasses import dataclass, field

from vmd.storage.index import Segment

# ------------------------------------------------------------------- the zooms
#
# Three, named the way the operator would say them. A whole day is what the bar
# has always drawn and is useless for aiming: at 1200 px one pixel is 72 seconds
# and there is no moment on it anyone can land on. An hour is the working zoom -
# a pixel is three seconds - and ten minutes is for reading a single event frame
# by frame.
#
# `None` for the whole day rather than 86400, because a local day is 23, 24 or
# 25 hours long and the whole of it is the whole of it whatever that is.
WHOLE_DAY = "Whole day"
ONE_HOUR = "1 hour"
TEN_MINUTES = "10 minutes"

ZOOM_SPANS: dict[str, float | None] = {
    WHOLE_DAY: None,
    ONE_HOUR: 3600.0,
    TEN_MINUTES: 600.0,
}
ZOOM_ORDER = (WHOLE_DAY, ONE_HOUR, TEN_MINUTES)


@dataclass(frozen=True)
class SeekTarget:
    """A file and how far into it to start."""

    path: str
    offset_seconds: float


@dataclass(frozen=True)
class ClipPart:
    """One file, and the piece of it a clip takes: where to start and how much.

    Both numbers are seconds inside that file, which is what ffmpeg's `-ss` and
    `-t` take. Nothing here knows about ffmpeg; it is the same arithmetic
    whoever spends it.
    """

    path: str
    start_offset: float
    duration: float


@dataclass(frozen=True)
class ClipPlan:
    """Everything on disk inside a range, and everything that is not.

    The gaps are carried rather than dropped because the sentence the operator
    gets afterwards depends on them: a clip that is shorter than the range he
    dragged has to say so, or he will believe he has the whole of it.
    """

    parts: list[ClipPart] = field(default_factory=list)
    gaps: list[tuple[float, float]] = field(default_factory=list)
    requested_seconds: float = 0.0

    @property
    def covered_seconds(self) -> float:
        return sum(part.duration for part in self.parts)

    @property
    def missing_seconds(self) -> float:
        return max(self.requested_seconds - self.covered_seconds, 0.0)

    @property
    def whole(self) -> bool:
        """Is what will be written the whole of what was asked for?

        A tenth of a second of rounding across a segment boundary is not a gap
        an operator can see, and calling it one would put a warning on every
        clip that crossed a file.
        """
        return self.missing_seconds <= 0.5


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


# ------------------------------------------------------------- the drawn window
#
# Everything above takes a start and an end, and none of it cares whether they
# are a whole day. That is what makes zooming a change to two numbers rather
# than a second drawing path: `coverage_bars`, `time_at` and the click already
# work against whatever window they are given.


def zoom_window(
    zoom: str, centre: float, day_start: float, day_end: float
) -> tuple[float, float]:
    """A window of the named zoom, holding `centre`, inside this day.

    Centred on the moment rather than anchored to it, so the operator's place is
    kept when he zooms in: he was looking at 14:32 and he is still looking at
    14:32, with less of the day either side of it. A window that would fall off
    either end of the day is slid back inside instead of being shortened - a
    short window at the edge would draw an hour's worth of pixels over ten
    minutes of day and every time read off it would be wrong.
    """
    span = ZOOM_SPANS.get(zoom)
    day = day_end - day_start
    if span is None or span >= day:
        return (day_start, day_end)
    start = centre - span / 2.0
    start = min(max(start, day_start), day_end - span)
    return (start, start + span)


def pan_window(
    view_start: float, view_end: float, by_seconds: float, day_start: float, day_end: float
) -> tuple[float, float]:
    """The same window, moved along the day and stopped at its ends."""
    span = view_end - view_start
    if span >= day_end - day_start:
        return (day_start, day_end)
    start = min(max(view_start + by_seconds, day_start), day_end - span)
    return (start, start + span)


def bring_into_view(
    view_start: float, view_end: float, when: float, day_start: float, day_end: float
) -> tuple[float, float]:
    """The same window, moved only if it does not already hold this moment.

    Being taken to a movement while zoomed into another hour has to show the
    movement. Left alone when it is already in view, because a window that
    re-centres on every seek makes the bar jump under a pointer that is trying
    to aim at it.
    """
    if view_start <= when <= view_end:
        return (view_start, view_end)
    span = view_end - view_start
    if span >= day_end - day_start:
        return (day_start, day_end)
    start = min(max(when - span / 2.0, day_start), day_end - span)
    return (start, start + span)


# ------------------------------------------------------- why nothing is there
#
# A blank is the one answer this console may never give. What can honestly be
# said about a moment with no footage comes from three places and no others: the
# clock (a time in the future has not happened), the segment index (how far back
# the archive goes, how far forward, and where the holes in between are), and
# the recorder's own report of what it is doing right now.
#
# What is deliberately NOT said is a cause. Retention deletes by age and by
# budget and writes down that it declined, but nothing anywhere records which
# rule reclaimed which hour, so "the disk was full" would be a guess dressed as
# a finding. An operator deciding whether footage of a perimeter exists is
# exactly the person who must never be told a guess.


def gap_around(
    segments: list[Segment], when: float, window_start: float, window_end: float
) -> tuple[float, float]:
    """The stretch with no coverage that contains this moment, inside a window.

    Clamped to the window, so the ends of it are times the operator can read off
    the bar he is looking at.
    """
    edge_before = window_start
    edge_after = window_end
    for segment in segments:
        if segment.end <= when:
            edge_before = max(edge_before, segment.end)
        elif segment.start > when:
            edge_after = min(edge_after, segment.start)
    return (edge_before, edge_after)


def _stream_report(recorder: dict | None, stream: str) -> dict | None:
    """What a recorder's report says about one stream, if it says anything.

    Given None - which is what the caller hands over for a report too old to
    believe - this says nothing at all, which is the honest answer.
    """
    if not isinstance(recorder, dict):
        return None
    for entry in recorder.get("streams") or []:
        if isinstance(entry, dict) and entry.get("name") == stream:
            return entry
    return None


def explain_gap(
    when: float,
    segments: list[Segment],
    stream: str,
    archive: tuple[float, float] | None,
    now: float,
    recorder: dict | None = None,
) -> str:
    """Why there is no footage at this moment, in words, with nothing invented.

    `segments` is the day being drawn; `archive` is the first start and the last
    end this stream has anywhere in the index, which is what separates "before
    anything we still hold" from "a hole in the middle of a recorded day".
    `recorder` is a `recording.json` the caller has already decided is fresh
    enough to believe, or None.
    """
    clock = _clock(when)
    if when > now:
        return f"nothing at {clock} - that time has not happened yet"
    if archive is None:
        return f"nothing at {clock} - nothing has ever been recorded on {stream}"

    first, last = archive
    if when < first:
        return (
            f"nothing at {clock} - the recordings kept for {stream} start at "
            f"{_moment(first)}, and there is nothing before that"
        )
    if when >= last:
        said = (
            f"nothing at {clock} - the newest recording kept for {stream} "
            f"ends at {_moment(last)}"
        )
        report = _stream_report(recorder, stream)
        if report is not None and not report.get("running"):
            return f"{said}, and {stream} is not being recorded at the moment"
        if report is not None and report.get("running"):
            return (
                f"{said}. It is being recorded now - the last few minutes are "
                "always missing, because a recording is only listed once the "
                "file it is in has been finished"
            )
        return said

    edge_before, edge_after = gap_around(segments, when, first, last)
    return (
        f"nothing at {clock} - nothing was recorded on {stream} between "
        f"{_clock(edge_before)} and {_clock(edge_after)}"
    )


def _clock(when: float) -> str:
    return datetime.datetime.fromtimestamp(when).strftime("%H:%M:%S")


def _moment(when: float) -> str:
    return datetime.datetime.fromtimestamp(when).strftime("%d %B %Y %H:%M:%S")


# ------------------------------------------------------------- planning a clip


def clip_plan(segments: list[Segment], start: float, end: float) -> ClipPlan:
    """Which files, and which piece of each, make up this range.

    Pure: no filesystem and no ffmpeg. Everything the export has to say to the
    operator afterwards - it is shorter than you asked for, there is nothing
    here at all - falls out of what this returns, which is why it is worked out
    before anything is spawned rather than read back off a command's output.

    A range dragged from right to left is the same range: mark-out before
    mark-in is a direction, not a mistake.
    """
    if end < start:
        start, end = end, start
    plan_parts: list[ClipPart] = []
    gaps: list[tuple[float, float]] = []
    cursor = start
    for segment in sorted(segments, key=lambda s: (s.start, s.path)):
        if segment.end <= cursor or segment.start >= end:
            continue
        if segment.start > cursor:
            gaps.append((cursor, min(segment.start, end)))
            cursor = min(segment.start, end)
        piece_start = max(segment.start, cursor)
        piece_end = min(segment.end, end)
        if piece_end <= piece_start:
            continue
        plan_parts.append(
            ClipPart(
                path=segment.path,
                start_offset=piece_start - segment.start,
                duration=piece_end - piece_start,
            )
        )
        cursor = piece_end
    if cursor < end:
        gaps.append((cursor, end))
    return ClipPlan(parts=plan_parts, gaps=gaps, requested_seconds=end - start)
