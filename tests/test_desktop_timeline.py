"""Turning indexed segments into a drawable day, and clicks back into times."""

from __future__ import annotations

import datetime
import random

from vmd.desktop.timeline import DAY_SECONDS, coverage_bars, day_bounds, seek_target, time_at
from vmd.storage.index import Segment


def segment(start: float, end: float, path: str = "a.mp4") -> Segment:
    return Segment(id=1, stream="thermal", path=path, start=start, end=end, size_bytes=10)


def test_a_day_is_midnight_to_midnight_local() -> None:
    start, end = day_bounds(2026, 8, 11)
    assert end - start == DAY_SECONDS


def test_coverage_is_a_fraction_of_the_day() -> None:
    start, _ = day_bounds(2026, 8, 11)
    bars = coverage_bars([segment(start + 3600, start + 7200)], start)
    assert len(bars) == 1
    left, width = bars[0]
    assert abs(left - 1 / 24) < 1e-6
    assert abs(width - 1 / 24) < 1e-6


def test_segments_outside_the_day_are_not_drawn() -> None:
    start, _ = day_bounds(2026, 8, 11)
    bars = coverage_bars([segment(start - 10000, start - 9000)], start)
    assert bars == []


def test_a_segment_crossing_midnight_is_clipped_to_the_day() -> None:
    start, _ = day_bounds(2026, 8, 11)
    bars = coverage_bars([segment(start + DAY_SECONDS - 60, start + DAY_SECONDS + 600)], start)
    left, width = bars[0]
    assert left + width <= 1.0 + 1e-9


def test_a_click_maps_to_a_time_in_that_day() -> None:
    start, _ = day_bounds(2026, 8, 11)
    assert time_at(0.0, start) == start
    assert time_at(0.5, start) == start + DAY_SECONDS / 2
    assert time_at(1.0, start) == start + DAY_SECONDS


def test_a_click_inside_a_segment_seeks_into_that_file() -> None:
    start, _ = day_bounds(2026, 8, 11)
    one = segment(start + 100, start + 400, "one.mp4")
    target = seek_target([one], start + 250)
    assert target is not None
    assert target.path == "one.mp4"
    assert abs(target.offset_seconds - 150) < 1e-6


def test_a_click_in_a_gap_finds_nothing() -> None:
    start, _ = day_bounds(2026, 8, 11)
    one = segment(start + 100, start + 200, "one.mp4")
    assert seek_target([one], start + 5000) is None


# --- Extra tests beyond the plan ---


def test_dst_day_documents_actual_behaviour() -> None:
    """day_bounds is midnight-to-midnight-plus-86400, not calendar-day-aware.

    On a real DST transition the local day is 23 or 25 hours long, so
    `start + DAY_SECONDS` does not land on the following midnight. This test
    pins down what actually happens on a known local DST date so the gap is
    visible rather than silently wrong. Israel (the deployment locale) moves
    clocks back (DST end, "fall back") in late October: on that date the
    local day is 25 hours long. If the machine's local zone has no DST, the
    difference collapses to zero and the assertion still documents that.
    """
    dst_end_date = datetime.date(2026, 10, 25)
    day_after = dst_end_date + datetime.timedelta(days=1)

    naive_start = datetime.datetime(
        dst_end_date.year, dst_end_date.month, dst_end_date.day
    ).timestamp()
    naive_end = naive_start + DAY_SECONDS

    real_next_midnight = datetime.datetime(
        day_after.year, day_after.month, day_after.day
    ).timestamp()

    # day_bounds() itself, for the same date:
    start, end = day_bounds(dst_end_date.year, dst_end_date.month, dst_end_date.day)
    assert start == naive_start
    assert end == naive_end

    # The documented discrepancy: on a machine observing DST with a fall-back
    # on this date, end != real_next_midnight (off by one hour's worth of
    # seconds). On a machine with no DST for this zone, they are equal - this
    # assertion records whichever is true for the environment running the
    # test, rather than asserting a specific offset.
    discrepancy = real_next_midnight - end
    assert discrepancy in (0.0, 3600.0, -3600.0)


def test_segments_not_assumed_sorted_coverage() -> None:
    start, _ = day_bounds(2026, 8, 11)
    segs = [
        segment(start + 10000, start + 11000, "c.mp4"),
        segment(start + 100, start + 200, "a.mp4"),
        segment(start + 5000, start + 5500, "b.mp4"),
    ]
    random.Random(42).shuffle(segs)
    bars = coverage_bars(segs, start)
    assert len(bars) == 3


def test_segments_not_assumed_sorted_seek() -> None:
    start, _ = day_bounds(2026, 8, 11)
    segs = [
        segment(start + 10000, start + 11000, "c.mp4"),
        segment(start + 100, start + 200, "a.mp4"),
        segment(start + 5000, start + 5500, "b.mp4"),
    ]
    random.Random(7).shuffle(segs)
    target = seek_target(segs, start + 5250)
    assert target is not None
    assert target.path == "b.mp4"


def test_overlapping_segments_do_not_produce_negative_width() -> None:
    start, _ = day_bounds(2026, 8, 11)
    segs = [
        segment(start + 100, start + 500, "first.mp4"),
        segment(start + 300, start + 700, "second.mp4"),
    ]
    bars = coverage_bars(segs, start)
    for _left, width in bars:
        assert width >= 0


def test_zero_length_segment_produces_no_bar() -> None:
    start, _ = day_bounds(2026, 8, 11)
    bars = coverage_bars([segment(start + 100, start + 100)], start)
    assert bars == []


def test_click_on_segment_boundary_picks_start_inclusive_end_exclusive() -> None:
    start, _ = day_bounds(2026, 8, 11)
    earlier = segment(start + 100, start + 200, "earlier.mp4")
    later = segment(start + 200, start + 300, "later.mp4")

    at_boundary = seek_target([earlier, later], start + 200)
    assert at_boundary is not None
    assert at_boundary.path == "later.mp4"
    assert abs(at_boundary.offset_seconds - 0) < 1e-6

    just_before = seek_target([earlier, later], start + 199.999999)
    assert just_before is not None
    assert just_before.path == "earlier.mp4"
