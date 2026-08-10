"""Turning indexed segments into a drawable day, and clicks back into times."""

from __future__ import annotations

import random

from vmd.desktop.timeline import coverage_bars, day_bounds, seek_target, time_at
from vmd.storage.index import Segment

# An ordinary (non-DST-transition) day, for tests that don't care about the
# DST edge case.
ORDINARY_DAY_SECONDS = 24 * 60 * 60


def segment(start: float, end: float, path: str = "a.mp4") -> Segment:
    return Segment(id=1, stream="thermal", path=path, start=start, end=end, size_bytes=10)


def test_a_day_is_midnight_to_midnight_local() -> None:
    start, end = day_bounds(2026, 8, 11)
    assert end - start == ORDINARY_DAY_SECONDS


def test_coverage_is_a_fraction_of_the_day() -> None:
    start, end = day_bounds(2026, 8, 11)
    bars = coverage_bars([segment(start + 3600, start + 7200)], start, end)
    assert len(bars) == 1
    left, width = bars[0]
    assert abs(left - 1 / 24) < 1e-6
    assert abs(width - 1 / 24) < 1e-6


def test_segments_outside_the_day_are_not_drawn() -> None:
    start, end = day_bounds(2026, 8, 11)
    bars = coverage_bars([segment(start - 10000, start - 9000)], start, end)
    assert bars == []


def test_a_segment_crossing_midnight_is_clipped_to_the_day() -> None:
    start, end = day_bounds(2026, 8, 11)
    span = end - start
    bars = coverage_bars([segment(start + span - 60, start + span + 600)], start, end)
    left, width = bars[0]
    assert left + width <= 1.0 + 1e-9


def test_a_click_maps_to_a_time_in_that_day() -> None:
    start, end = day_bounds(2026, 8, 11)
    span = end - start
    assert time_at(0.0, start, end) == start
    assert time_at(0.5, start, end) == start + span / 2
    assert time_at(1.0, start, end) == end


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


def test_dst_end_day_is_measured_at_25_hours() -> None:
    """Israel's DST ends in late October, moving clocks back an hour; the
    local calendar day that contains the change is 25 hours long. day_bounds
    must measure this rather than assume a fixed 86400-second day.
    """
    start, end = day_bounds(2026, 10, 25)
    assert end - start == 25 * 3600


def test_dst_start_day_is_measured_at_23_hours() -> None:
    """Israel's DST starts in late March, moving clocks forward an hour; the
    local calendar day that contains the change is 23 hours long.
    """
    start, end = day_bounds(2026, 3, 27)
    assert end - start == 23 * 3600


def test_segment_in_dst_extra_hour_appears_in_that_days_coverage() -> None:
    """The operator-visible symptom of the old bug: a segment recorded during
    the 25th hour of a DST-end day must still show up in that day's bar,
    instead of silently falling off the end.
    """
    start, end = day_bounds(2026, 10, 25)
    span = end - start
    assert span == 25 * 3600

    extra_hour_segment = segment(start + 24 * 3600 + 600, start + 24 * 3600 + 1200)
    bars = coverage_bars([extra_hour_segment], start, end)

    assert len(bars) == 1
    left, width = bars[0]
    assert left + width <= 1.0 + 1e-9
    # Falls within the day's 25th hour, i.e. past the point a fixed-86400
    # assumption would have clipped it at.
    assert left > 24 / 25 - 1e-9


def test_segments_not_assumed_sorted_coverage() -> None:
    start, end = day_bounds(2026, 8, 11)
    segs = [
        segment(start + 10000, start + 11000, "c.mp4"),
        segment(start + 100, start + 200, "a.mp4"),
        segment(start + 5000, start + 5500, "b.mp4"),
    ]
    random.Random(42).shuffle(segs)
    bars = coverage_bars(segs, start, end)
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
    start, end = day_bounds(2026, 8, 11)
    segs = [
        segment(start + 100, start + 500, "first.mp4"),
        segment(start + 300, start + 700, "second.mp4"),
    ]
    bars = coverage_bars(segs, start, end)
    for _left, width in bars:
        assert width >= 0


def test_zero_length_segment_produces_no_bar() -> None:
    start, end = day_bounds(2026, 8, 11)
    bars = coverage_bars([segment(start + 100, start + 100)], start, end)
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
