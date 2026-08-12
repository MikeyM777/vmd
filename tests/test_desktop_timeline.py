"""Turning indexed segments into a drawable day, and clicks back into times."""

from __future__ import annotations

import random

from vmd.desktop.timeline import (
    ONE_HOUR,
    TEN_MINUTES,
    WHOLE_DAY,
    bring_into_view,
    clip_plan,
    coverage_bars,
    day_bounds,
    explain_gap,
    middle_of_the_footage,
    pan_window,
    seek_target,
    time_at,
    zoom_window,
)
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


# --------------------------------------------------------------------- zooming
#
# At whole-day, one pixel of a 1200 px bar is 72 seconds, and landing on a
# moment is not something anyone can do. The window the bar draws is therefore a
# span inside the day rather than always the whole of it, and the arithmetic of
# that window is here because it is the part that goes wrong silently: a window
# that slides off the end of the day draws times that are not in it, and a zoom
# that does not keep the playhead loses the operator's place at the exact moment
# he was trying to look at it more closely.


def test_the_whole_day_is_the_widest_window() -> None:
    start, end = day_bounds(2026, 8, 11)
    assert zoom_window(WHOLE_DAY, start + 3600, start, end) == (start, end)


def test_an_hour_window_is_an_hour_long() -> None:
    start, end = day_bounds(2026, 8, 11)
    view_start, view_end = zoom_window(ONE_HOUR, start + 12 * 3600, start, end)
    assert view_end - view_start == 3600.0


def test_ten_minutes_is_ten_minutes() -> None:
    start, end = day_bounds(2026, 8, 11)
    view_start, view_end = zoom_window(TEN_MINUTES, start + 12 * 3600, start, end)
    assert view_end - view_start == 600.0


def test_zooming_keeps_the_moment_it_was_given_inside_the_window() -> None:
    """The playhead does not move when the zoom changes: the operator was
    looking at 14:32 and is still looking at 14:32, closer."""
    start, end = day_bounds(2026, 8, 11)
    when = start + 14 * 3600 + 32 * 60
    for span in (WHOLE_DAY, ONE_HOUR, TEN_MINUTES):
        view_start, view_end = zoom_window(span, when, start, end)
        assert view_start <= when <= view_end


def test_a_window_at_the_start_of_the_day_does_not_run_off_the_front() -> None:
    """00:02 with an hour of zoom is the first hour of the day, not 23:32
    yesterday - a bar drawn over times that are not in the day it names."""
    start, end = day_bounds(2026, 8, 11)
    view_start, view_end = zoom_window(ONE_HOUR, start + 120, start, end)
    assert view_start == start
    assert view_end == start + 3600.0


def test_a_window_at_the_end_of_the_day_does_not_run_off_the_back() -> None:
    start, end = day_bounds(2026, 8, 11)
    view_start, view_end = zoom_window(ONE_HOUR, end - 120, start, end)
    assert view_end == end
    assert view_start == end - 3600.0


def test_a_window_wider_than_a_short_day_is_the_whole_short_day() -> None:
    """A 23-hour DST day is still a whole day, and a window cannot be longer
    than the thing it is a window into."""
    start, end = day_bounds(2026, 3, 27)
    assert zoom_window(WHOLE_DAY, start, start, end) == (start, end)


def test_panning_moves_the_window_without_changing_its_length() -> None:
    start, end = day_bounds(2026, 8, 11)
    view = zoom_window(ONE_HOUR, start + 12 * 3600, start, end)
    moved = pan_window(view[0], view[1], 900.0, start, end)
    assert moved[1] - moved[0] == view[1] - view[0]
    assert moved[0] == view[0] + 900.0


def test_panning_stops_at_the_edges_of_the_day() -> None:
    start, end = day_bounds(2026, 8, 11)
    view = zoom_window(ONE_HOUR, start + 120, start, end)
    assert pan_window(view[0], view[1], -99999.0, start, end) == (start, start + 3600.0)
    assert pan_window(start, start + 3600.0, 999999.0, start, end) == (end - 3600.0, end)


def test_a_window_is_brought_to_a_moment_outside_it() -> None:
    """Being taken to a movement while zoomed in must show the movement, not
    the hour the operator happened to be looking at before."""
    start, end = day_bounds(2026, 8, 11)
    view = zoom_window(ONE_HOUR, start + 3600, start, end)
    brought = bring_into_view(view[0], view[1], start + 20 * 3600, start, end)
    assert brought[0] <= start + 20 * 3600 <= brought[1]
    assert brought[1] - brought[0] == 3600.0


def test_a_window_already_holding_the_moment_is_left_alone() -> None:
    start, end = day_bounds(2026, 8, 11)
    view = zoom_window(ONE_HOUR, start + 12 * 3600, start, end)
    assert bring_into_view(view[0], view[1], start + 12 * 3600 + 60, start, end) == view


# ------------------------------------------------------ why footage is missing
#
# A gap used to be blank, and blank is the one thing this console may never be:
# an operator looking at a hole in the night has to be told whether nothing
# happened, nothing was kept, or nothing has been written down yet. Everything
# said here is something the segment index or the recorder's own report can
# prove. Nothing here guesses at a cause.


def test_a_time_that_has_not_happened_yet_says_so() -> None:
    start, _end = day_bounds(2026, 8, 11)
    said = explain_gap(
        when=start + 20 * 3600,
        segments=[],
        stream="thermal",
        archive=None,
        now=start + 10 * 3600,
    )
    assert "has not happened" in said.lower(), said


def test_a_stream_with_nothing_at_all_says_nothing_was_ever_recorded() -> None:
    start, _end = day_bounds(2026, 8, 11)
    said = explain_gap(
        when=start + 3600,
        segments=[],
        stream="thermal",
        archive=None,
        now=start + 20 * 3600,
    )
    assert "thermal" in said
    assert "nothing has ever been recorded" in said.lower(), said


def test_before_the_oldest_recording_says_how_far_back_the_archive_goes() -> None:
    """Retention deletes the oldest footage, and recording started when it
    started; both end as "there is nothing before this moment", which is the one
    thing that can be said without inventing a reason."""
    start, _end = day_bounds(2026, 8, 11)
    archive = (start + 6 * 3600, start + 20 * 3600)
    said = explain_gap(
        when=start + 3600,
        segments=[],
        stream="thermal",
        archive=archive,
        now=start + 22 * 3600,
    )
    assert "06:00" in said, said
    assert "thermal" in said
    for guess in ("disk", "full", "to make room", "budget"):
        assert guess not in said.lower(), said


def test_after_the_newest_recording_while_recording_is_running_explains_the_wait() -> None:
    """The last few minutes are always missing, because a recording is only
    catalogued once ffmpeg has closed the file. That is knowable, and it is the
    gap an operator meets most often."""
    start, _end = day_bounds(2026, 8, 11)
    archive = (start + 6 * 3600, start + 10 * 3600)
    said = explain_gap(
        when=start + 10 * 3600 + 60,
        segments=[],
        stream="thermal",
        archive=archive,
        now=start + 10 * 3600 + 120,
        recorder={"streams": [{"name": "thermal", "running": True, "held_back": False}]},
    )
    assert "10:00" in said, said
    assert "few minutes" in said.lower(), said


def test_after_the_newest_recording_while_the_stream_is_not_running_says_that() -> None:
    start, _end = day_bounds(2026, 8, 11)
    archive = (start + 6 * 3600, start + 10 * 3600)
    said = explain_gap(
        when=start + 12 * 3600,
        segments=[],
        stream="thermal",
        archive=archive,
        now=start + 13 * 3600,
        recorder={"streams": [{"name": "thermal", "running": False, "held_back": False}]},
    )
    assert "not being recorded" in said.lower(), said


def test_a_gap_between_two_recordings_names_both_ends_of_it() -> None:
    start, _end = day_bounds(2026, 8, 11)
    segments = [
        segment(start + 8 * 3600, start + 9 * 3600, "a.mp4"),
        segment(start + 11 * 3600, start + 12 * 3600, "b.mp4"),
    ]
    said = explain_gap(
        when=start + 10 * 3600,
        segments=segments,
        stream="thermal",
        archive=(start + 8 * 3600, start + 12 * 3600),
        now=start + 20 * 3600,
    )
    assert "09:00" in said and "11:00" in said, said
    assert "thermal" in said


def test_no_reason_is_ever_invented() -> None:
    """The rule that matters more than any of the sentences: a cause that
    cannot be shown is not offered. "Nothing was recorded here" is honest; "the
    disk was full", when nobody knows that, is worse than a blank."""
    start, _end = day_bounds(2026, 8, 11)
    forbidden = ("disk was full", "out of space", "power", "crashed", "camera failed")
    cases = [
        dict(when=start + 3600, segments=[], archive=None, now=start + 2 * 3600),
        dict(
            when=start + 3600,
            segments=[],
            archive=(start + 2 * 3600, start + 5 * 3600),
            now=start + 6 * 3600,
        ),
        dict(
            when=start + 6 * 3600,
            segments=[],
            archive=(start, start + 5 * 3600),
            now=start + 7 * 3600,
        ),
    ]
    for case in cases:
        said = explain_gap(stream="thermal", **case).lower()
        for guess in forbidden:
            assert guess not in said, said


def test_a_recorder_report_nobody_believes_still_leaves_a_sentence() -> None:
    """A recorder that wedged an hour ago left a file saying every stream was
    fine. The caller hands None for a report it does not believe, and what is
    left has to be a sentence."""
    start, _end = day_bounds(2026, 8, 11)
    said = explain_gap(
        when=start + 12 * 3600,
        segments=[],
        stream="thermal",
        archive=(start + 6 * 3600, start + 10 * 3600),
        now=start + 13 * 3600,
        recorder=None,
    )
    assert said.strip()
    assert "10:00" in said, said


# ------------------------------------------------------------- planning a clip
#
# What ffmpeg is asked to copy, worked out before anything is spawned. A range
# spanning several files, a range crossing a gap, and a range over nothing at
# all are three different answers, and the operator is owed a different sentence
# for each.


def test_a_clip_inside_one_file_is_one_piece_of_it() -> None:
    start, _end = day_bounds(2026, 8, 11)
    segments = [segment(start, start + 300, "a.mp4")]
    plan = clip_plan(segments, start + 60, start + 120)
    assert len(plan.parts) == 1
    part = plan.parts[0]
    assert part.path == "a.mp4"
    assert part.start_offset == 60.0
    assert part.duration == 60.0
    assert plan.missing_seconds == 0.0


def test_a_clip_across_a_segment_boundary_is_two_pieces() -> None:
    start, _end = day_bounds(2026, 8, 11)
    segments = [
        segment(start, start + 300, "a.mp4"),
        segment(start + 300, start + 600, "b.mp4"),
    ]
    plan = clip_plan(segments, start + 280, start + 320)
    assert [p.path for p in plan.parts] == ["a.mp4", "b.mp4"]
    assert plan.parts[0].start_offset == 280.0 and plan.parts[0].duration == 20.0
    assert plan.parts[1].start_offset == 0.0 and plan.parts[1].duration == 20.0
    assert plan.covered_seconds == 40.0


def test_a_clip_crossing_a_gap_is_shorter_than_the_range_asked_for() -> None:
    start, _end = day_bounds(2026, 8, 11)
    segments = [
        segment(start, start + 300, "a.mp4"),
        segment(start + 600, start + 900, "b.mp4"),
    ]
    plan = clip_plan(segments, start + 200, start + 700)
    assert plan.requested_seconds == 500.0
    assert plan.covered_seconds == 200.0
    assert plan.missing_seconds == 300.0
    assert plan.gaps == [(start + 300, start + 600)]


def test_a_clip_over_nothing_at_all_has_no_pieces() -> None:
    start, _end = day_bounds(2026, 8, 11)
    segments = [segment(start, start + 300, "a.mp4")]
    plan = clip_plan(segments, start + 1000, start + 2000)
    assert plan.parts == []
    assert plan.covered_seconds == 0.0
    assert plan.missing_seconds == 1000.0


def test_a_backwards_range_is_read_the_way_it_was_dragged() -> None:
    """Mark out before mark in is a drag from right to left, not a mistake."""
    start, _end = day_bounds(2026, 8, 11)
    segments = [segment(start, start + 300, "a.mp4")]
    plan = clip_plan(segments, start + 120, start + 60)
    assert plan.requested_seconds == 60.0
    assert len(plan.parts) == 1 and plan.parts[0].start_offset == 60.0


def test_the_pieces_come_out_in_time_order_whatever_order_the_index_gave() -> None:
    start, _end = day_bounds(2026, 8, 11)
    segments = [
        segment(start + 600, start + 900, "c.mp4"),
        segment(start, start + 300, "a.mp4"),
        segment(start + 300, start + 600, "b.mp4"),
    ]
    plan = clip_plan(segments, start + 100, start + 800)
    assert [p.path for p in plan.parts] == ["a.mp4", "b.mp4", "c.mp4"]


# --------------------------------------------------- where to zoom to, with no
# playhead
#
# He opens a day and presses "1 hour" without having clicked anywhere. There is
# no playhead, so there is nothing saying which hour he means - and the middle
# of the window is the middle of the clock, which on a console that recorded
# 00:00 to 01:35 is 11:30 and an empty bar under a line still reading "1h 25m
# recorded". The answer has to be a moment there is footage at.


def _seg(start: float, end: float) -> Segment:
    return Segment(
        id=1, stream="thermal", path="a.mp4", start=start, end=end, size_bytes=1
    )


def test_the_middle_of_a_single_stretch_of_footage_is_its_middle() -> None:
    start, _end = day_bounds(2026, 8, 11)
    middle = middle_of_the_footage([_seg(start, start + 3600)])
    assert middle == start + 1800.0


def test_the_middle_of_the_footage_is_never_in_a_gap() -> None:
    """Half a day apart, which is the case the middle of the clock gets wrong.

    Two blocks with twelve hours of nothing between them: the midpoint BETWEEN
    them is the middle of the gap, and half the recorded time either side of the
    answer is inside one of the blocks. That is the whole difference - this is a
    median of recorded time, not a midpoint of the span.
    """
    start, _end = day_bounds(2026, 8, 11)
    blocks = [_seg(start, start + 600), _seg(start + 12 * 3600, start + 12 * 3600 + 600)]
    middle = middle_of_the_footage(blocks)
    assert any(s.start <= middle <= s.end for s in blocks), middle


def test_the_middle_of_the_footage_weighs_the_long_stretch_over_the_short_one() -> None:
    """An hour in the morning and one minute in the evening is a morning."""
    start, _end = day_bounds(2026, 8, 11)
    blocks = [_seg(start + 3600, start + 7200), _seg(start + 20 * 3600, start + 20 * 3600 + 60)]
    assert start + 3600 <= middle_of_the_footage(blocks) <= start + 7200


def test_the_order_the_index_hands_them_over_does_not_matter() -> None:
    start, _end = day_bounds(2026, 8, 11)
    blocks = [_seg(start + 7200, start + 10800), _seg(start, start + 3600)]
    assert middle_of_the_footage(blocks) == middle_of_the_footage(list(reversed(blocks)))


def test_no_footage_has_no_middle() -> None:
    """None rather than a number: a caller that invented one would be back to
    aiming at a moment nothing was recorded at."""
    assert middle_of_the_footage([]) is None
    start, _end = day_bounds(2026, 8, 11)
    assert middle_of_the_footage([_seg(start, start)]) is None
