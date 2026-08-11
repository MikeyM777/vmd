"""Playback, against a fake pane and a real index."""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

import pytest

from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtGui import QColor

from vmd.desktop.playback import BOTH, EVENT_LEAD_SECONDS, PlaybackTab
from vmd.desktop.style import PALETTE
from vmd.desktop.timeline import day_bounds, time_at
from vmd.desktop.video import FakeVideoPane
from vmd.detect.events import Event
from vmd.storage.index import SegmentIndex


def build(qtbot, tmp_path: Path):
    index = SegmentIndex(tmp_path / "segments.db")
    pane = FakeVideoPane()
    tab = PlaybackTab(index=index, pane=pane)
    qtbot.addWidget(tab)
    return tab, pane, index


def day_span(year: int = 2026, month: int = 8, day: int = 11) -> float:
    start, end = day_bounds(year, month, day)
    return end - start


# ------------------------------------------------------- the plan's own tests


def test_a_day_with_nothing_recorded_says_so(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        tab.show_day(2026, 8, 11, stream="thermal")
        assert tab.coverage == []
        assert "nothing" in tab.status_text.lower()
    finally:
        index.close()


def test_recorded_segments_become_coverage(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _ = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "a.mp4"), start + 3600, start + 5400, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        assert len(tab.coverage) == 1
    finally:
        index.close()


def test_clicking_inside_coverage_opens_that_file_at_that_offset(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _ = day_bounds(2026, 8, 11)
        path = tmp_path / "a.mp4"
        index.add("thermal", str(path), start + 3600, start + 5400, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        # 3600 s into the day, plus 30 s - compute the fraction from the real day length
        tab.click_at((3600 + 30) / (day_bounds(2026, 8, 11)[1] - day_bounds(2026, 8, 11)[0]))
        assert pane.url is not None
        assert path.name in pane.url
        assert tab.seek_offset == 30
    finally:
        index.close()


def test_clicking_a_gap_explains_rather_than_playing_something_else(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _ = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "a.mp4"), start + 3600, start + 5400, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.click_at(0.9)
        assert pane.url is None
        assert "no recording" in tab.status_text.lower()
    finally:
        index.close()


# ------------------------------------------------------------ the real widget
#
# The tests above call click_at directly, which proves the maths and nothing
# about the wiring. The browser version got exactly the wiring wrong: it
# measured the click against the wrong element and scrubbed to a time nobody
# asked for. These press the mouse at a known pixel of a known width.


def test_a_click_on_the_bar_seeks_to_the_time_under_the_pointer(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, end = day_bounds(2026, 8, 11)
        span = end - start
        path = tmp_path / "midday.mp4"
        # One segment covering the whole day, so any click lands inside it and
        # the offset is exactly the time the click meant.
        index.add("thermal", str(path), start, end, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")

        tab.bar.resize(200, 24)
        qtbot.mouseClick(tab.bar, Qt.MouseButton.LeftButton, pos=QPoint(100, 12))

        # x=100 of width 200 is half way through the day.
        assert tab.playhead_time == start + span / 2
        assert tab.seek_offset == span / 2
        assert pane.url is not None and path.name in pane.url
    finally:
        index.close()


def test_a_click_at_a_quarter_of_the_width_means_a_quarter_of_the_day(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, end = day_bounds(2026, 8, 11)
        span = end - start
        index.add("thermal", str(tmp_path / "all.mp4"), start, end, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")

        tab.bar.resize(400, 24)
        qtbot.mouseClick(tab.bar, Qt.MouseButton.LeftButton, pos=QPoint(100, 12))

        assert tab.playhead_time == start + span / 4
        assert tab.seek_offset == span / 4
    finally:
        index.close()


def test_the_bar_draws_recorded_time_and_leaves_gaps_empty(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, end = day_bounds(2026, 8, 11)
        span = end - start
        # The first half of the day recorded, the second half not.
        index.add("thermal", str(tmp_path / "a.mp4"), start, start + span / 2, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")

        tab.bar.resize(200, 24)
        image = tab.bar.grab().toImage()
        # Measured against the width the bar was actually painted at: it lives
        # in a layout, which has the last word on how wide it is.
        width = image.width()
        assert image.pixelColor(round(width * 0.25), 12) == QColor(PALETTE["ok"])
        assert image.pixelColor(round(width * 0.75), 12) == QColor(PALETTE["well"])
    finally:
        index.close()


def test_the_bar_draws_a_playhead_where_it_was_clicked(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, end = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "a.mp4"), start, end, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")

        tab.bar.resize(200, 24)
        before = tab.bar.grab().toImage()
        assert not any(
            before.pixelColor(x, 12) == QColor(PALETTE["accent"]) for x in range(200)
        )

        qtbot.mouseClick(tab.bar, Qt.MouseButton.LeftButton, pos=QPoint(100, 12))
        after = tab.bar.grab().toImage()
        marked = [x for x in range(200) if after.pixelColor(x, 12) == QColor(PALETTE["accent"])]
        assert marked, "the playhead was not drawn"
        assert all(abs(x - 100) <= 2 for x in marked)
    finally:
        index.close()


# ---------------------------------------------------- choosing day and stream


def test_a_stream_only_in_the_index_can_still_be_chosen(qtbot, tmp_path: Path) -> None:
    """Playback answers "what is on disk", so a stream dropped from settings
    still has recordings worth watching."""
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _ = day_bounds(2026, 8, 11)
        index.add("retired", str(tmp_path / "r.mp4"), start + 60, start + 120, 1000)
        index.add("thermal", str(tmp_path / "t.mp4"), start + 60, start + 120, 1000)
        tab.refresh_streams()
        assert tab.stream_names() == ["retired", "thermal"]
    finally:
        index.close()


def test_choosing_another_stream_redraws_that_stream(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _ = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "t.mp4"), start + 60, start + 120, 1000)
        index.add("visible", str(tmp_path / "v1.mp4"), start + 60, start + 120, 1000)
        index.add("visible", str(tmp_path / "v2.mp4"), start + 300, start + 360, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        assert len(tab.coverage) == 1

        tab.stream_selector.setCurrentText("visible")
        assert len(tab.coverage) == 2
    finally:
        index.close()


def test_choosing_another_day_redraws_that_day(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _ = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "t.mp4"), start + 60, start + 120, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        assert len(tab.coverage) == 1

        tab.date_selector.setDate(QDate(2026, 8, 12))
        assert tab.coverage == []
        assert "nothing" in tab.status_text.lower()
    finally:
        index.close()


# ------------------------------------------------------------ an unread index


class BrokenIndex:
    """An index whose file cannot be read."""

    def all(self, stream: str | None = None):
        raise sqlite3.DatabaseError("database disk image is malformed")


def test_an_index_that_cannot_be_read_says_so_instead_of_crashing(qtbot) -> None:
    pane = FakeVideoPane()
    tab = PlaybackTab(index=BrokenIndex(), pane=pane)
    qtbot.addWidget(tab)

    assert tab.refresh_streams() == []
    tab.show_day(2026, 8, 11, stream="thermal")
    assert tab.coverage == []
    assert "recordings" in tab.status_text.lower(), tab.status_text


# ----------------------------------------------------------------- the seeking


def test_the_file_is_handed_over_as_a_url_with_the_offset_recorded(
    qtbot, tmp_path: Path
) -> None:
    """VideoPane.show takes a URL and nothing else; the offset is recorded here
    and applied by the player."""
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _ = day_bounds(2026, 8, 11)
        path = tmp_path / "a.mp4"
        index.add("thermal", str(path), start + 3600, start + 5400, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.click_at((3600 + 45) / day_span())
        assert pane.url == path.as_uri()
        assert tab.seek_offset == 45
    finally:
        index.close()


def test_a_click_in_a_gap_names_the_time_and_leaves_the_picture_alone(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _ = day_bounds(2026, 8, 11)
        path = tmp_path / "a.mp4"
        index.add("thermal", str(path), start + 3600, start + 5400, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.click_at((3600 + 45) / day_span())
        assert pane.url is not None

        tab.click_at(10800 / day_span())  # 03:00, nothing recorded
        assert "03:00" in tab.status_text
        assert pane.url == path.as_uri()  # still showing what it was showing
        assert pane.restarts == 0
    finally:
        index.close()


# ----------------------------------------------------------- movement marks
#
# The same events the Live tab lists, drawn on the day they happened. A click
# on one seeks to five seconds before the movement, because an event that
# starts on the first frame you see is one you have already missed.


class FakeEvents:
    """A reader with the EventStore's shape.

    It filters by stream and deliberately *not* by time: the tab must not draw
    a mark for a day it is not showing, and a reader that had already dropped
    those events would prove nothing about that.
    """

    def __init__(self, events=None) -> None:
        self.events = list(events or [])

    def between(self, start: float, end: float, stream: str | None = None):
        return [e for e in self.events if stream is None or e.stream == stream]


class BrokenEvents:
    def between(self, start: float, end: float, stream: str | None = None):
        raise sqlite3.DatabaseError("database disk image is malformed")


def movement(event_id: int, started: float, stream: str = "thermal"):
    return Event(
        id=event_id,
        stream=stream,
        started=started,
        ended=started + 4.0,
        box=(10, 20, 13, 30),
        travelled_px=51.0,
    )


def build_with_events(qtbot, tmp_path: Path, events):
    index = SegmentIndex(tmp_path / "segments.db")
    pane = FakeVideoPane()
    tab = PlaybackTab(index=index, pane=pane, events=events)
    qtbot.addWidget(tab)
    return tab, pane, index


def test_movement_in_the_day_becomes_a_mark(qtbot, tmp_path: Path) -> None:
    start, end = day_bounds(2026, 8, 11)
    span = end - start
    events = FakeEvents([movement(1, start + 3600)])
    tab, _, index = build_with_events(qtbot, tmp_path, events)
    try:
        index.add("thermal", str(tmp_path / "a.mp4"), start, end, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        assert [round(f, 6) for f, _ in tab.event_marks] == [round(3600 / span, 6)]
    finally:
        index.close()


def test_movement_from_another_day_is_not_drawn(qtbot, tmp_path: Path) -> None:
    """A mark at the edge of the bar would claim movement at midnight."""
    start, end = day_bounds(2026, 8, 11)
    events = FakeEvents([movement(1, start - 7200), movement(2, end + 7200)])
    tab, _, index = build_with_events(qtbot, tmp_path, events)
    try:
        index.add("thermal", str(tmp_path / "a.mp4"), start, end, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        assert tab.event_marks == []
    finally:
        index.close()


def test_marks_follow_the_chosen_stream(qtbot, tmp_path: Path) -> None:
    start, end = day_bounds(2026, 8, 11)
    events = FakeEvents(
        [
            movement(1, start + 3600, stream="thermal"),
            movement(2, start + 7200, stream="visible"),
            movement(3, start + 9000, stream="visible"),
        ]
    )
    tab, _, index = build_with_events(qtbot, tmp_path, events)
    try:
        index.add("thermal", str(tmp_path / "t.mp4"), start, end, 1000)
        index.add("visible", str(tmp_path / "v.mp4"), start, end, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        assert len(tab.event_marks) == 1

        tab.stream_selector.setCurrentText("visible")
        assert len(tab.event_marks) == 2
    finally:
        index.close()


def test_the_bar_draws_a_mark_where_the_movement_was(qtbot, tmp_path: Path) -> None:
    start, end = day_bounds(2026, 8, 11)
    span = end - start
    events = FakeEvents([movement(1, start + span / 2)])
    tab, _, index = build_with_events(qtbot, tmp_path, events)
    try:
        index.add("thermal", str(tmp_path / "a.mp4"), start, end, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.bar.resize(200, 24)

        image = tab.bar.grab().toImage()
        # Measured against the width the bar was actually painted at: it lives
        # in a layout, which has the last word on how wide it is.
        middle = round(0.5 * image.width())
        marked = [
            x for x in range(image.width()) if image.pixelColor(x, 12) == QColor(PALETTE["alarm"])
        ]
        assert marked, "the movement was not drawn"
        assert all(abs(x - middle) <= 3 for x in marked)
    finally:
        index.close()


def test_clicking_a_mark_seeks_five_seconds_before_the_movement(
    qtbot, tmp_path: Path
) -> None:
    """An event that starts on the first frame you see is one you have missed."""
    start, end = day_bounds(2026, 8, 11)
    span = end - start
    events = FakeEvents([movement(1, start + span / 2)])
    tab, pane, index = build_with_events(qtbot, tmp_path, events)
    try:
        path = tmp_path / "a.mp4"
        index.add("thermal", str(path), start, end, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")

        tab.bar.resize(200, 24)
        # Middle of whatever width the bar ended up with: the layout has the
        # last word on that, and the mark is at the middle of the day.
        qtbot.mouseClick(
            tab.bar, Qt.MouseButton.LeftButton, pos=QPoint(round(tab.bar.width() / 2), 12)
        )

        assert tab.playhead_time == start + span / 2 - 5.0
        assert tab.seek_offset == span / 2 - 5.0
        assert pane.url == path.as_uri()
    finally:
        index.close()


def test_a_click_within_the_tolerance_prefers_the_mark(qtbot, tmp_path: Path) -> None:
    """Twenty seconds, on a bar 200 px wide where one pixel is seven minutes.
    The pixel under the pointer is never the second the movement began."""
    start, end = day_bounds(2026, 8, 11)
    span = end - start
    at = start + span / 2
    events = FakeEvents([movement(1, at)])
    tab, _, index = build_with_events(qtbot, tmp_path, events)
    try:
        index.add("thermal", str(tmp_path / "a.mp4"), start, end, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")

        tab.click_at((at + 20.0 - start) / span, width=200)  # twenty seconds away
        assert abs(tab.playhead_time - (at - 5.0)) < 1.0
    finally:
        index.close()


def test_a_click_outside_the_tolerance_is_an_ordinary_seek(qtbot, tmp_path: Path) -> None:
    start, end = day_bounds(2026, 8, 11)
    span = end - start
    at = start + span / 2
    events = FakeEvents([movement(1, at)])
    tab, _, index = build_with_events(qtbot, tmp_path, events)
    try:
        index.add("thermal", str(tmp_path / "a.mp4"), start, end, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")

        # Five minutes away on a 1200 px bar: four pixels clear of the red.
        tab.click_at((at + 300.0 - start) / span, width=1200)
        assert abs(tab.playhead_time - (at + 300.0)) < 1.0
    finally:
        index.close()


def test_movement_whose_footage_is_gone_says_so_rather_than_playing_something_else(
    qtbot, tmp_path: Path
) -> None:
    """Retention reclaims footage, and a mark can outlive the file it points
    at. It is answered the way a click in a gap is answered: name the time,
    leave the picture alone."""
    start, end = day_bounds(2026, 8, 11)
    span = end - start
    events = FakeEvents([movement(1, start + span / 2)])
    tab, pane, index = build_with_events(qtbot, tmp_path, events)
    try:
        # Footage everywhere except where the movement was.
        index.add("thermal", str(tmp_path / "a.mp4"), start, start + span / 4, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")

        tab.click_at(0.5, width=200)

        assert pane.url is None
        assert "no recording" in tab.status_text.lower()
    finally:
        index.close()


def test_an_event_store_that_cannot_be_read_still_draws_the_day(
    qtbot, tmp_path: Path
) -> None:
    """The coverage comes from the segment index. Losing the movement marks
    must not lose the footage they were drawn over."""
    start, end = day_bounds(2026, 8, 11)
    tab, _, index = build_with_events(qtbot, tmp_path, BrokenEvents())
    try:
        index.add("thermal", str(tmp_path / "a.mp4"), start, end, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        assert len(tab.coverage) == 1
        assert tab.event_marks == []
    finally:
        index.close()


# ------------------------------------------------- how close a click has to be
#
# Wrong in both directions before now. Six PIXELS on a bar spanning a whole day
# redirected a click to an event up to eight and a half minutes away, and made
# plain time-seeking impossible on a day with 113 marks. Thirty SECONDS on a
# 1200 px bar is 0.42 of a pixel, so a mark drawn three pixels wide had 0.83 px
# of it that could be hit: the operator aimed at the red line, missed, and was
# given footage from a minute away with nothing saying he had missed.
#
# The rule now is the one that is true at every width: the target is at least as
# big as the thing drawn. Four pixels of target for three pixels of mark,
# whatever the window is dragged to.


def test_a_click_clear_of_a_mark_means_the_time_not_the_mark(
    qtbot, tmp_path: Path
) -> None:
    """Five minutes away on a 1000 px bar is three pixels clear of the red."""
    start, _end = day_bounds(2026, 8, 11)
    at = start + 12 * 3600
    tab, _pane, index = build_with_events(qtbot, tmp_path, FakeEvents([movement(1, at)]))
    index.add(stream="thermal", path=str(tmp_path / "a.mp4"), start=start,
              end=start + 86400, size_bytes=1)
    tab.show_day(2026, 8, 11, stream="thermal")

    later = at + 300.0
    tab.click_at((later - start) / day_span(), width=1000)

    assert abs(tab.playhead_time - later) < 1.0, (
        "a click five minutes away was redirected to a mark"
    )


def test_a_click_a_few_seconds_from_a_mark_still_means_the_mark(
    qtbot, tmp_path: Path
) -> None:
    start, _end = day_bounds(2026, 8, 11)
    at = start + 12 * 3600
    tab, _pane, index = build_with_events(qtbot, tmp_path, FakeEvents([movement(1, at)]))
    index.add(stream="thermal", path=str(tmp_path / "a.mp4"), start=start,
              end=start + 86400, size_bytes=1)
    tab.show_day(2026, 8, 11, stream="thermal")

    tab.click_at((at + 10.0 - start) / day_span(), width=1000)

    assert abs(tab.playhead_time - (at - EVENT_LEAD_SECONDS)) < 1.0


def test_the_mark_is_at_least_as_big_to_click_as_it_is_to_look_at(
    qtbot, tmp_path: Path
) -> None:
    """The whole rule, at a narrow window and a wide one.

    Pixels per second change with the width, so the tolerance has to as well:
    a fixed duration is 0.83 px of target at 1200 and 1.76 px at 2540, and the
    mark is drawn three pixels wide at both.
    """
    from vmd.desktop.playback import MARK_WIDTH

    start, _end = day_bounds(2026, 8, 11)
    at = start + 12 * 3600
    tab, _pane, index = build_with_events(qtbot, tmp_path, FakeEvents([movement(1, at)]))
    index.add(stream="thermal", path=str(tmp_path / "a.mp4"), start=start,
              end=start + 86400, size_bytes=1)
    tab.show_day(2026, 8, 11, stream="thermal")

    for width in (1200, 1900, 2540, 3840):
        seconds_per_pixel = day_span() / width
        target_pixels = 2.0 * tab.mark_tolerance_seconds(width) / seconds_per_pixel
        assert target_pixels >= MARK_WIDTH, (
            f"at {width} px the mark is {MARK_WIDTH} px to look at and "
            f"{target_pixels:.2f} px to click"
        )
        # And not so much bigger that it eats the bar around it.
        assert target_pixels <= 2 * MARK_WIDTH, f"at {width} px: {target_pixels:.2f} px"


def test_a_click_on_the_red_the_operator_can_see_means_the_mark(
    qtbot, tmp_path: Path
) -> None:
    """The pixel he aimed at is drawn red, so it means the mark - at a narrow
    window and at a wide one, where a pixel is worth very different amounts of
    the day."""
    start, _end = day_bounds(2026, 8, 11)
    at = start + 12 * 3600
    tab, _pane, index = build_with_events(qtbot, tmp_path, FakeEvents([movement(1, at)]))
    index.add(stream="thermal", path=str(tmp_path / "a.mp4"), start=start,
              end=start + 86400, size_bytes=1)
    tab.show_day(2026, 8, 11, stream="thermal")

    for width in (1200, 3840):
        seconds_per_pixel = day_span() / width
        for edge in (-1.5, -0.5, 0.0, 0.5, 1.5):
            when = at + edge * seconds_per_pixel
            tab.click_at((when - start) / day_span(), width=width)
            assert abs(tab.playhead_time - (at - EVENT_LEAD_SECONDS)) < 1.0, (
                f"a click {edge} px from the middle of a 3 px mark missed it "
                f"at {width} px wide"
            )


def test_a_deliberate_seek_is_not_swallowed_at_any_width(
    qtbot, tmp_path: Path
) -> None:
    """Four pixels clear of the mark is plain time, whatever the width. This is
    the half the six-pixel tolerance got wrong."""
    start, _end = day_bounds(2026, 8, 11)
    at = start + 12 * 3600
    tab, _pane, index = build_with_events(qtbot, tmp_path, FakeEvents([movement(1, at)]))
    index.add(stream="thermal", path=str(tmp_path / "a.mp4"), start=start,
              end=start + 86400, size_bytes=1)
    tab.show_day(2026, 8, 11, stream="thermal")

    for width in (1200, 1900, 2540, 3840):
        seconds_per_pixel = day_span() / width
        when = at + 4.0 * seconds_per_pixel
        tab.click_at((when - start) / day_span(), width=width)
        assert abs(tab.playhead_time - when) < 1.0, (
            f"a seek four pixels clear of a mark was swallowed at {width} px wide"
        )


def test_a_day_full_of_marks_still_has_moments_that_can_be_clicked(
    qtbot, tmp_path: Path
) -> None:
    """113 marks in a day was a real day. Every second of it was within 2.8 s of
    a mark, so nothing but marks could be reached."""
    start, _end = day_bounds(2026, 8, 11)
    events = [movement(n, start + n * (86400 / 113.0)) for n in range(113)]
    tab, _pane, index = build_with_events(qtbot, tmp_path, FakeEvents(events))
    index.add(stream="thermal", path=str(tmp_path / "a.mp4"), start=start,
              end=start + 86400, size_bytes=1)
    tab.show_day(2026, 8, 11, stream="thermal")

    # Half way between two marks: about six minutes from either.
    between = start + 1.5 * (86400 / 113.0)
    tab.click_at((between - start) / day_span(), width=1000)
    assert abs(tab.playhead_time - between) < 1.0


# ------------------------------------------------------------------ the seek
#
# The offset used to be computed, stored, and never used: `VideoPane.show` took
# a URL and nothing else, so a click on 14:32 opened the file containing 14:32
# and played it from the beginning - up to a whole segment away from the moment
# the operator asked about. For a system whose purpose is "something happened,
# show me", that is not playback.


def test_a_click_opens_the_file_at_the_moment_it_asked_for(
    qtbot, tmp_path: Path
) -> None:
    start, end = day_bounds(2026, 8, 11)
    tab, pane, index = build(qtbot, tmp_path)
    try:
        # Five-minute segments, the length the recorder actually writes.
        for offset in range(0, 3600, 300):
            index.add(
                "thermal", str(tmp_path / f"{offset}.mp4"),
                start + offset, start + offset + 300, 1000,
            )
        tab.show_day(2026, 8, 11, stream="thermal")

        # Twelve minutes into the day: two segments in, and two minutes into
        # the third.
        tab.click_at((12 * 60) / (end - start))
        assert pane.url is not None and pane.url.endswith("600.mp4")
        assert pane.at_seconds == pytest.approx(120.0, abs=1.0)
    finally:
        index.close()


def test_a_movement_mark_plays_from_before_the_movement(
    qtbot, tmp_path: Path
) -> None:
    """An event that begins on the first frame you see is one you have already
    missed: the approach is the part worth watching."""
    start, end = day_bounds(2026, 8, 11)
    when = start + 3600 + 100  # a hundred seconds into the hour's segment
    events = FakeEvents([movement(1, when)])
    tab, pane, index = build_with_events(qtbot, tmp_path, events)
    try:
        index.add("thermal", str(tmp_path / "hour.mp4"), start + 3600, start + 3900, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.click_at((when - start) / (end - start))
        assert pane.url is not None and pane.url.endswith("hour.mp4")
        assert pane.at_seconds == pytest.approx(95.0, abs=0.5)
        assert "before the movement" in tab.status_text
    finally:
        index.close()


def test_a_movement_at_the_very_start_of_a_file_is_clamped_not_lost(
    qtbot, tmp_path: Path
) -> None:
    """Five seconds before a movement two seconds into a segment is a moment
    in the previous file, or in a gap. The answer to "show me this movement"
    can never be "there is nothing there"."""
    start, end = day_bounds(2026, 8, 11)
    when = start + 3600 + 2  # two seconds into the segment
    events = FakeEvents([movement(1, when)])
    tab, pane, index = build_with_events(qtbot, tmp_path, events)
    try:
        # One segment, with a gap in front of it: nothing covers when - 5 s.
        index.add("thermal", str(tmp_path / "hour.mp4"), start + 3600, start + 3900, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.click_at((when - start) / (end - start))
        assert pane.url is not None and pane.url.endswith("hour.mp4")
        assert pane.at_seconds == pytest.approx(0.0, abs=0.01)
        assert "no recording" not in tab.status_text
        # And the sentence says the lead it really got, not the one it wanted.
        assert "2s before the movement" in tab.status_text
    finally:
        index.close()


def test_a_movement_whose_footage_is_gone_still_says_so(qtbot, tmp_path: Path) -> None:
    """Clamping the lead may not turn "retention reclaimed this" into a seek
    into some other file."""
    start, end = day_bounds(2026, 8, 11)
    when = start + 3600
    events = FakeEvents([movement(1, when)])
    tab, pane, index = build_with_events(qtbot, tmp_path, events)
    try:
        index.add("thermal", str(tmp_path / "later.mp4"), start + 7200, start + 7500, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.click_at((when - start) / (end - start))
        assert pane.url is None
        assert "no longer on disk" in tab.status_text
    finally:
        index.close()


def test_the_position_never_goes_backwards_past_the_start_of_a_file(
    qtbot, tmp_path: Path
) -> None:
    """The floor under everything: whatever the arithmetic above did, libVLC is
    never asked to open a file at a negative second."""
    start, end = day_bounds(2026, 8, 11)
    tab, pane, index = build(qtbot, tmp_path)
    try:
        index.add("thermal", str(tmp_path / "a.mp4"), start, start + 300, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.click_at(0.0)
        assert pane.at_seconds >= 0.0
    finally:
        index.close()


# --------------------------------------------------- being taken to a movement
#
# The alarm strip and the movement list both end here: "show me that" is one
# call, and it is the same call the timeline's own marks already make.


def moved_at(when: float, stream: str = "thermal", event_id: int = 1) -> Event:
    return Event(
        id=event_id,
        stream=stream,
        started=when,
        ended=when + 3.0,
        box=(10, 20, 13, 30),
        travelled_px=44.0,
        label="",
        confidence=0.0,
    )


def test_showing_a_movement_opens_its_day_and_plays_from_before_it(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _end = day_bounds(2026, 8, 11)
        path = tmp_path / "a.mp4"
        index.add("thermal", str(path), start + 3600, start + 5400, 1000)

        assert tab.show_event(moved_at(start + 3660)) is True

        assert tab.date_selector.date().toString("yyyy-MM-dd") == "2026-08-11"
        assert tab.stream_selector.currentText() == "thermal"
        assert path.name in (pane.url or "")
        # 60 s into the file, less the five-second lead.
        assert tab.seek_offset == pytest.approx(60.0 - EVENT_LEAD_SECONDS)
    finally:
        index.close()


def test_showing_a_movement_whose_footage_is_gone_says_so_and_plays_nothing(
    qtbot, tmp_path: Path
) -> None:
    """Retention reclaimed it, or it happened before recording started, or it
    is on a stream nothing was ever recording. He must not be left on an empty
    day working out which."""
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _end = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "a.mp4"), start + 3600, start + 5400, 1000)

        assert tab.show_event(moved_at(start + 7200, stream="visible")) is False

        assert pane.url is None
        assert "no recording" in tab.status_text.lower(), tab.status_text
        assert "visible" in tab.status_text, tab.status_text
    finally:
        index.close()


def test_showing_a_movement_the_index_cannot_answer_about_says_so(
    qtbot, tmp_path: Path
) -> None:
    """A catalogue that will not open is a sentence, not a traceback in front of
    an operator who has just been told something moved."""
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _end = day_bounds(2026, 8, 11)

        class Unreadable:
            def all(self, stream=None):
                raise sqlite3.DatabaseError("database disk image is malformed")

        tab._index = Unreadable()
        assert tab.show_event(moved_at(start + 3660)) is False
        assert pane.url is None
        assert tab.status_text, "nothing was said at all"
    finally:
        index.close()


# =============================================================== the transport
#
# There was no way to pause. The controls row was Day, Stream and nothing else,
# and re-watching the same ten seconds cost a fresh click on a bar where one
# pixel is over a minute of the day.
#
# The requirement is the buttons: "i rather buttons, space and arrows are nice
# but i need also buttons". Every test below presses something on the screen;
# the keys are tested separately, as an addition.


def a_recorded_day(qtbot, tmp_path: Path, minutes: int = 60):
    """A day with five-minute recordings from 12:00, the way it comes off the
    recorder, and the tab pointed at it."""
    tab, pane, index = build(qtbot, tmp_path)
    start, _end = day_bounds(2026, 8, 11)
    noon = start + 12 * 3600
    for offset in range(0, minutes * 60, 300):
        index.add(
            "thermal", str(tmp_path / f"{offset}.mp4"),
            noon + offset, noon + offset + 300, 1000,
        )
    tab.show_day(2026, 8, 11, stream="thermal")
    return tab, pane, index, noon


def test_pressing_play_holds_the_picture_and_lets_it_go_again(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 60)
        assert pane.paused is False

        qtbot.mouseClick(tab.transport.play_button, Qt.MouseButton.LeftButton)
        assert pane.paused is True

        qtbot.mouseClick(tab.transport.play_button, Qt.MouseButton.LeftButton)
        assert pane.paused is False
    finally:
        index.close()


def test_skipping_back_ten_seconds_moves_ten_seconds_back(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 120)
        qtbot.mouseClick(tab.transport.back_ten, Qt.MouseButton.LeftButton)
        assert tab.playhead_time == pytest.approx(noon + 110, abs=0.01)
    finally:
        index.close()


def test_skipping_forward_a_minute_crosses_into_the_next_recording(
    qtbot, tmp_path: Path
) -> None:
    """The recorder writes five-minute files. A skip that lands in the next one
    has to open the next one, or the picture stays where it was while the clock
    under it says otherwise."""
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 280)  # twenty seconds from the end of the first
        assert pane.url.endswith("0.mp4")
        qtbot.mouseClick(tab.transport.forward_minute, Qt.MouseButton.LeftButton)
        assert tab.playhead_time == pytest.approx(noon + 340, abs=0.01)
        assert pane.url.endswith("300.mp4")
        assert tab.seek_offset == pytest.approx(40.0, abs=0.01)
    finally:
        index.close()


def test_a_skip_within_the_same_file_does_not_open_it_again(
    qtbot, tmp_path: Path
) -> None:
    """Reopening a file to move ten seconds is a black frame and a wait for
    something the player can already do."""
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 120)
        before = pane.restarts
        qtbot.mouseClick(tab.transport.forward_ten, Qt.MouseButton.LeftButton)
        assert pane.restarts == before
        assert pane.position_seconds() == pytest.approx(130.0, abs=0.01)
    finally:
        index.close()


def test_a_skip_into_a_gap_says_so_and_leaves_the_picture_alone(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path, minutes=5)
    try:
        tab.play_at_time(noon + 280)
        showing = pane.url
        qtbot.mouseClick(tab.transport.forward_minute, Qt.MouseButton.LeftButton)
        assert pane.url == showing, "a gap must not change the picture"
        assert "no recording at" in tab.status_text.lower(), tab.status_text
    finally:
        index.close()


def test_a_skip_before_the_first_recording_stops_at_the_day(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 5)
        qtbot.mouseClick(tab.transport.back_minute, Qt.MouseButton.LeftButton)
        assert tab.playhead_time >= tab.day_start
    finally:
        index.close()


def test_choosing_a_speed_asks_the_player_for_it(qtbot, tmp_path: Path) -> None:
    from vmd.desktop.transport import SPEEDS

    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 60)
        tab.transport.speed_selector.setCurrentIndex(list(SPEEDS).index(4.0))
        assert pane.rate == 4.0
    finally:
        index.close()


def test_the_transport_is_off_while_there_is_nothing_to_play(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        tab.show_day(2026, 8, 11, stream="thermal")
        assert not tab.transport.play_button.isEnabled()
    finally:
        index.close()


# ------------------------------------------------------------------- the keys
#
# An addition, never a substitute. Each does exactly what the button beside it
# does, through the same call.


def test_the_space_bar_is_the_play_button(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 60)
        qtbot.keyClick(tab, Qt.Key.Key_Space)
        assert pane.paused is True
        qtbot.keyClick(tab, Qt.Key.Key_Space)
        assert pane.paused is False
    finally:
        index.close()


def test_the_arrow_keys_are_the_ten_second_skips(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 120)
        qtbot.keyClick(tab, Qt.Key.Key_Left)
        assert tab.playhead_time == pytest.approx(noon + 110, abs=0.01)
        qtbot.keyClick(tab, Qt.Key.Key_Right)
        assert tab.playhead_time == pytest.approx(noon + 120, abs=0.01)
    finally:
        index.close()


# ================================================================ day and time
#
# A real calendar, because that is what he asked for, and a readout big enough
# to read from where he sits.


def test_the_day_can_be_stepped_back_and_forward(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        tab.show_day(2026, 8, 11, stream="thermal")
        qtbot.mouseClick(tab.previous_day, Qt.MouseButton.LeftButton)
        assert tab.date_selector.date().toString("yyyy-MM-dd") == "2026-08-10"
        qtbot.mouseClick(tab.next_day, Qt.MouseButton.LeftButton)
        qtbot.mouseClick(tab.next_day, Qt.MouseButton.LeftButton)
        assert tab.date_selector.date().toString("yyyy-MM-dd") == "2026-08-12"
    finally:
        index.close()


def test_the_day_is_chosen_from_a_calendar(qtbot, tmp_path: Path) -> None:
    """Not a spin box. He said calendar, and a month laid out is how anyone
    finds last Tuesday."""
    from PySide6.QtWidgets import QCalendarWidget

    tab, pane, index = build(qtbot, tmp_path)
    try:
        assert isinstance(tab.date_selector.calendar(), QCalendarWidget)
        tab.date_selector.calendar().setSelectedDate(QDate(2026, 8, 9))
        assert tab.date_selector.date().toString("yyyy-MM-dd") == "2026-08-09"
    finally:
        index.close()


def test_a_day_with_footage_is_drawn_differently_from_an_empty_one(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _end = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "a.mp4"), start + 3600, start + 7200, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")

        recorded = tab.date_selector.calendar().dateTextFormat(QDate(2026, 8, 11))
        empty = tab.date_selector.calendar().dateTextFormat(QDate(2026, 8, 12))
        assert recorded != empty
        assert QDate(2026, 8, 11) in tab.days_with_footage
        assert QDate(2026, 8, 12) not in tab.days_with_footage
    finally:
        index.close()


def test_the_moment_being_watched_is_written_out_in_full(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 125)
        said = tab.readout_text
        assert "12:02:05" in said, said
        assert "2026" in said and "August" in said, said
    finally:
        index.close()


def test_the_readout_is_bigger_than_the_body_text(qtbot, tmp_path: Path) -> None:
    from vmd.desktop.style import SIZE_BODY

    tab, pane, index = build(qtbot, tmp_path)
    try:
        assert tab.readout.font().pixelSize() > SIZE_BODY
    finally:
        index.close()


# ==================================================================== zooming
#
# At whole day one pixel is 72 seconds and there is no moment anybody can land
# on. Buttons, because there must be buttons; the wheel as well, because it is
# what a hand reaches for.


def test_the_three_zooms_are_offered_as_buttons(qtbot, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QAbstractButton

    tab, pane, index = build(qtbot, tmp_path)
    try:
        assert len(tab.zoom_buttons) == 3
        for button in tab.zoom_buttons.values():
            assert isinstance(button, QAbstractButton)
            assert button.text().strip()
    finally:
        index.close()


def test_zooming_in_narrows_the_window_to_the_hour(qtbot, tmp_path: Path) -> None:
    from vmd.desktop.timeline import ONE_HOUR

    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 60)
        qtbot.mouseClick(tab.zoom_buttons[ONE_HOUR], Qt.MouseButton.LeftButton)
        assert tab.view_end - tab.view_start == pytest.approx(3600.0)
    finally:
        index.close()


def test_the_playhead_is_still_on_the_same_moment_after_zooming(
    qtbot, tmp_path: Path
) -> None:
    from vmd.desktop.timeline import ONE_HOUR, TEN_MINUTES

    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 137)
        for zoom in (ONE_HOUR, TEN_MINUTES):
            qtbot.mouseClick(tab.zoom_buttons[zoom], Qt.MouseButton.LeftButton)
            assert tab.playhead_time == pytest.approx(noon + 137, abs=0.01)
            assert tab.view_start <= tab.playhead_time <= tab.view_end
    finally:
        index.close()


def test_a_zoomed_bar_can_be_moved_along_the_day(qtbot, tmp_path: Path) -> None:
    from vmd.desktop.timeline import ONE_HOUR

    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 60)
        qtbot.mouseClick(tab.zoom_buttons[ONE_HOUR], Qt.MouseButton.LeftButton)
        was = tab.view_start
        qtbot.mouseClick(tab.pan_later, Qt.MouseButton.LeftButton)
        assert tab.view_start > was
        qtbot.mouseClick(tab.pan_earlier, Qt.MouseButton.LeftButton)
        assert tab.view_start == pytest.approx(was)
    finally:
        index.close()


def test_a_click_on_a_zoomed_bar_means_a_time_inside_the_window(
    qtbot, tmp_path: Path
) -> None:
    """The whole point of zooming. Half way along an hour-wide bar is half an
    hour into that hour, not half way through the day."""
    from vmd.desktop.timeline import ONE_HOUR

    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path, minutes=120)
    try:
        tab.play_at_time(noon + 60)
        qtbot.mouseClick(tab.zoom_buttons[ONE_HOUR], Qt.MouseButton.LeftButton)
        tab.click_at(0.5, width=1000)
        assert tab.playhead_time == pytest.approx(
            (tab.view_start + tab.view_end) / 2, abs=1.0
        )
    finally:
        index.close()


def test_the_whole_day_button_puts_the_whole_day_back(qtbot, tmp_path: Path) -> None:
    from vmd.desktop.timeline import ONE_HOUR, WHOLE_DAY

    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 60)
        qtbot.mouseClick(tab.zoom_buttons[ONE_HOUR], Qt.MouseButton.LeftButton)
        qtbot.mouseClick(tab.zoom_buttons[WHOLE_DAY], Qt.MouseButton.LeftButton)
        assert (tab.view_start, tab.view_end) == (tab.day_start, tab.day_end)
    finally:
        index.close()


def test_the_wheel_zooms_on_the_moment_under_the_pointer(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        was = tab.view_end - tab.view_start
        tab.zoom_towards(0.5, closer=True)
        assert tab.view_end - tab.view_start < was
        tab.zoom_towards(0.5, closer=False)
        assert tab.view_end - tab.view_start == pytest.approx(was)
    finally:
        index.close()


# ================================================================ the dragging


def test_dragging_the_playhead_shows_the_time_it_is_over(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path, minutes=1440)
    try:
        tab.begin_drag(0.25)
        assert tab.dragging is True
        tab.drag_to(0.75)
        moment = time_at(0.75, tab.view_start, tab.view_end)
        clock = datetime.datetime.fromtimestamp(moment).strftime("%H:%M:%S")
        assert clock in tab.hover_text, tab.hover_text
    finally:
        index.close()


def test_letting_go_of_the_playhead_seeks_to_where_it_was_dropped(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path, minutes=1440)
    try:
        tab.begin_drag(0.25)
        tab.drag_to(0.6)
        tab.end_drag(0.6)
        assert tab.dragging is False
        assert tab.playhead_time == pytest.approx(
            time_at(0.6, tab.view_start, tab.view_end), abs=2.0
        )
    finally:
        index.close()


def test_dragging_does_not_reopen_a_file_for_every_pixel(qtbot, tmp_path: Path) -> None:
    """A drag across an hour is hundreds of mouse moves. Seeking on each of
    them is a player asked to open a file five hundred times."""
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path, minutes=1440)
    try:
        tab.begin_drag(0.5)
        before = pane.restarts
        for step in range(50):
            tab.drag_to(0.5 + step / 500.0)
        assert pane.restarts == before
    finally:
        index.close()


def test_moving_over_the_bar_without_pressing_says_the_time_under_the_pointer(
    qtbot, tmp_path: Path
) -> None:
    """One pixel is over a minute at whole-day. He is aiming blind without it."""
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.hover_at(0.5)
        assert tab.hover_text.strip(), "nothing said under the pointer"
    finally:
        index.close()


def test_the_pointer_leaving_the_bar_takes_the_hovered_time_with_it(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.hover_at(0.5)
        tab.hover_at(None)
        assert tab.hover_text == ""
    finally:
        index.close()


# ======================================================= thermal, visible, both
#
# "add the option to choose between thermal\vis\both". Both means the two
# streams locked to one timeline at the same moment: thermal spots the thing and
# visible identifies it, and they are worth nothing unless they are showing the
# same second.


def two_cameras(qtbot, tmp_path: Path, visible_seconds: int = 300):
    tab, pane, index = build(qtbot, tmp_path)
    start, _end = day_bounds(2026, 8, 11)
    index.add("thermal", str(tmp_path / "t.mp4"), start, start + 600, 1000)
    index.add("visible", str(tmp_path / "v.mp4"), start, start + visible_seconds, 1000)
    tab.show_day(2026, 8, 11, stream=BOTH)
    return tab, pane, index, start


def test_both_cameras_can_be_chosen_when_there_are_two(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _end = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "t.mp4"), start, start + 300, 1000)
        index.add("visible", str(tmp_path / "v.mp4"), start, start + 300, 1000)
        tab.refresh_streams()
        assert tab.stream_selector.findText(BOTH) >= 0
        assert tab.stream_names() == ["thermal", "visible"]
    finally:
        index.close()


def test_one_camera_alone_is_not_offered_as_both(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _end = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "t.mp4"), start, start + 300, 1000)
        tab.refresh_streams()
        assert tab.stream_selector.findText(BOTH) < 0
    finally:
        index.close()


def test_both_cameras_are_opened_at_the_same_moment(qtbot, tmp_path: Path) -> None:
    tab, pane, index, start = two_cameras(qtbot, tmp_path)
    try:
        tab.play_at_time(start + 120)
        assert pane.url.endswith("t.mp4")
        assert tab.second_pane.url.endswith("v.mp4")
        assert pane.at_seconds == pytest.approx(120.0)
        assert tab.second_pane.at_seconds == pytest.approx(120.0)
    finally:
        index.close()


def test_pausing_holds_both_pictures(qtbot, tmp_path: Path) -> None:
    tab, pane, index, start = two_cameras(qtbot, tmp_path)
    try:
        tab.play_at_time(start + 120)
        qtbot.mouseClick(tab.transport.play_button, Qt.MouseButton.LeftButton)
        assert pane.paused is True and tab.second_pane.paused is True
    finally:
        index.close()


def test_the_speed_is_given_to_both(qtbot, tmp_path: Path) -> None:
    from vmd.desktop.transport import SPEEDS

    tab, pane, index, start = two_cameras(qtbot, tmp_path)
    try:
        tab.play_at_time(start + 120)
        tab.transport.speed_selector.setCurrentIndex(list(SPEEDS).index(2.0))
        assert pane.rate == 2.0 and tab.second_pane.rate == 2.0
    finally:
        index.close()


def test_a_skip_moves_both(qtbot, tmp_path: Path) -> None:
    tab, pane, index, start = two_cameras(qtbot, tmp_path)
    try:
        tab.play_at_time(start + 120)
        qtbot.mouseClick(tab.transport.forward_ten, Qt.MouseButton.LeftButton)
        assert pane.position_seconds() == pytest.approx(130.0, abs=0.01)
        assert tab.second_pane.position_seconds() == pytest.approx(130.0, abs=0.01)
    finally:
        index.close()


def test_a_moment_only_one_camera_recorded_says_which_one_is_missing(
    qtbot, tmp_path: Path
) -> None:
    """The honest case, and the one that matters: thermal has it, visible does
    not. Leaving a still of the wrong minute beside a live picture would be the
    console inventing footage."""
    tab, pane, index, start = two_cameras(qtbot, tmp_path, visible_seconds=300)
    try:
        tab.play_at_time(start + 450)
        assert pane.url.endswith("t.mp4")
        assert tab.second_pane.url is None
        assert "visible" in tab.status_text, tab.status_text
        assert "nothing" in tab.status_text.lower(), tab.status_text
    finally:
        index.close()


def test_how_far_apart_the_two_pictures_are_can_be_read(qtbot, tmp_path: Path) -> None:
    """Two players opened at the same moment do not stay locked to each other,
    and this console does not pretend they do: the difference is measured and it
    is on the screen."""
    tab, pane, index, start = two_cameras(qtbot, tmp_path)
    try:
        tab.play_at_time(start + 120)
        assert tab.drift_seconds() == pytest.approx(0.0, abs=0.01)

        tab.second_pane.seek_seconds(123.5)
        assert tab.drift_seconds() == pytest.approx(3.5, abs=0.01)
    finally:
        index.close()


def test_one_camera_alone_has_no_drift_to_report(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 60)
        assert tab.drift_seconds() is None
    finally:
        index.close()


def test_the_second_picture_is_put_away_when_one_camera_is_chosen(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index, start = two_cameras(qtbot, tmp_path)
    try:
        tab.play_at_time(start + 120)
        assert tab.second_pane.url is not None
        tab.stream_selector.setCurrentText("thermal")
        assert tab.second_pane.url is None
    finally:
        index.close()


# =============================================================== why it is blank


def test_a_gap_says_why_rather_than_showing_nothing(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon - 3600)
        said = tab.status_text.lower()
        assert "nothing" in said
        assert "thermal" in said
        for guess in ("disk was full", "out of space", "crashed"):
            assert guess not in said, tab.status_text
    finally:
        index.close()


def test_before_the_archive_starts_says_how_far_back_it_goes(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon - 7200)
        assert "12:00" in tab.status_text, tab.status_text
    finally:
        index.close()


def test_a_hole_between_two_recordings_names_both_of_its_ends(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path, minutes=5)
    try:
        index.add("thermal", str(tmp_path / "later.mp4"), noon + 3600, noon + 3900, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.play_at_time(noon + 1800)
        assert "12:05" in tab.status_text and "13:00" in tab.status_text, tab.status_text
    finally:
        index.close()


def test_a_time_that_has_not_happened_yet_is_not_called_a_gap(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab._now = lambda: noon + 3600
        tab.play_at_time(noon + 20 * 3600)
        assert "has not happened" in tab.status_text.lower(), tab.status_text
    finally:
        index.close()


# ================================================================= saving a clip


def test_marking_a_start_and_an_end_makes_a_range(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 60)
        qtbot.mouseClick(tab.mark_start, Qt.MouseButton.LeftButton)
        tab.play_at_time(noon + 180)
        qtbot.mouseClick(tab.mark_end, Qt.MouseButton.LeftButton)
        assert tab.clip_from == pytest.approx(noon + 60)
        assert tab.clip_to == pytest.approx(noon + 180)
    finally:
        index.close()


def test_nothing_can_be_saved_before_a_range_is_marked(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        assert not tab.save_clip.isEnabled()
        tab.play_at_time(noon + 60)
        qtbot.mouseClick(tab.mark_start, Qt.MouseButton.LeftButton)
        assert not tab.save_clip.isEnabled()
        tab.play_at_time(noon + 180)
        qtbot.mouseClick(tab.mark_end, Qt.MouseButton.LeftButton)
        assert tab.save_clip.isEnabled()
    finally:
        index.close()


def test_the_marks_can_be_cleared(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 60)
        qtbot.mouseClick(tab.mark_start, Qt.MouseButton.LeftButton)
        qtbot.mouseClick(tab.clear_marks, Qt.MouseButton.LeftButton)
        assert tab.clip_from is None and tab.clip_to is None
    finally:
        index.close()


def test_saving_a_clip_asks_where_and_writes_there(qtbot, tmp_path: Path) -> None:
    """He chooses the folder: "yeah its nice, although its on the laptop add
    the option to save"."""
    folder = tmp_path / "keep"
    folder.mkdir()
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        asked: list[str] = []

        def choose() -> str:
            asked.append("asked")
            return str(folder)

        tab.ask_for_folder = choose
        tab.run_ffmpeg = _pretend_ffmpeg
        tab.play_at_time(noon + 60)
        tab.mark_start.click()
        tab.play_at_time(noon + 180)
        tab.mark_end.click()

        outcome = tab.save_clip_now(wait=True)
        assert asked == ["asked"]
        assert outcome.ok, outcome.message
        assert outcome.path.parent == folder
        assert str(folder) in tab.status_text
    finally:
        index.close()


def _pretend_ffmpeg(command, **kwargs):
    """ffmpeg's part, without ffmpeg. The real run is the integration test."""
    Path(command[-1]).write_bytes(b"a clip")

    class Done:
        returncode = 0
        stderr = b""

    return Done()


def test_a_folder_he_does_not_choose_saves_nothing(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.ask_for_folder = lambda: ""
        tab.run_ffmpeg = _pretend_ffmpeg
        tab.play_at_time(noon + 60)
        tab.mark_start.click()
        tab.play_at_time(noon + 180)
        tab.mark_end.click()
        assert tab.save_clip_now(wait=True) is None
    finally:
        index.close()


def test_a_range_with_no_footage_in_it_is_refused_in_words(
    qtbot, tmp_path: Path
) -> None:
    folder = tmp_path / "keep"
    folder.mkdir()
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path, minutes=5)
    try:
        tab.ask_for_folder = lambda: str(folder)
        tab.run_ffmpeg = _pretend_ffmpeg
        tab.clip_from = noon + 3600
        tab.clip_to = noon + 3700
        outcome = tab.save_clip_now(wait=True)
        assert not outcome.ok
        assert "no recording" in outcome.message.lower(), outcome.message
        assert list(folder.iterdir()) == []
    finally:
        index.close()


def test_a_range_crossing_a_gap_says_the_clip_is_shorter(qtbot, tmp_path: Path) -> None:
    folder = tmp_path / "keep"
    folder.mkdir()
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path, minutes=5)
    try:
        index.add("thermal", str(tmp_path / "later.mp4"), noon + 900, noon + 1200, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.ask_for_folder = lambda: str(folder)
        tab.run_ffmpeg = _pretend_ffmpeg
        tab.clip_from = noon + 100
        tab.clip_to = noon + 1000
        outcome = tab.save_clip_now(wait=True)
        assert outcome.ok, outcome.message
        assert "shorter" in outcome.message.lower(), outcome.message
    finally:
        index.close()


def test_a_clip_spanning_several_recordings_carries_all_of_them(
    qtbot, tmp_path: Path
) -> None:
    folder = tmp_path / "keep"
    folder.mkdir()
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        seen: list[list[str]] = []

        def note(command, **kwargs):
            listed = Path(command[command.index("-i") + 1]).read_text(encoding="utf-8")
            seen.append([line for line in listed.splitlines() if line.startswith("file ")])
            return _pretend_ffmpeg(command, **kwargs)

        tab.ask_for_folder = lambda: str(folder)
        tab.run_ffmpeg = note
        tab.clip_from = noon + 100
        tab.clip_to = noon + 800  # across three five-minute recordings
        outcome = tab.save_clip_now(wait=True)
        assert outcome.ok, outcome.message
        assert len(seen[0]) == 3, seen
    finally:
        index.close()


def test_a_folder_that_cannot_be_written_to_is_a_sentence(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.ask_for_folder = lambda: str(tmp_path / "gone")

        def refuse(command, **kwargs):
            raise OSError(28, "There is not enough space on the disk")

        tab.run_ffmpeg = refuse
        tab.clip_from = noon + 100
        tab.clip_to = noon + 200
        outcome = tab.save_clip_now(wait=True)
        assert not outcome.ok
        assert outcome.message.strip()
        assert outcome.message in tab.status_text
    finally:
        index.close()


def test_saving_a_clip_never_runs_on_the_thread_that_draws_the_window(
    qtbot, tmp_path: Path
) -> None:
    """A clip of a night is minutes of copying on a laptop that is also
    recording two streams. Run inline it is a frozen console."""
    import threading

    folder = tmp_path / "keep"
    folder.mkdir()
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        threads: list[int] = []

        def note(command, **kwargs):
            threads.append(threading.get_ident())
            return _pretend_ffmpeg(command, **kwargs)

        tab.run_ffmpeg = note
        tab.ask_for_folder = lambda: str(folder)
        tab.clip_from = noon + 60
        tab.clip_to = noon + 120
        tab.save_clip_now(wait=True)
        assert threads and threads[0] != threading.get_ident()
    finally:
        index.close()


def test_a_second_save_is_not_started_while_one_is_running(
    qtbot, tmp_path: Path
) -> None:
    """He presses it twice because nothing happened yet. Two ffmpegs writing
    the same name is a file that is neither of them."""
    folder = tmp_path / "keep"
    folder.mkdir()
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.ask_for_folder = lambda: str(folder)
        tab.run_ffmpeg = _pretend_ffmpeg
        tab.clip_from = noon + 60
        tab.clip_to = noon + 120
        tab._saving = True
        assert tab.save_clip_now(wait=True) is None
        assert not tab.save_clip.isEnabled()
    finally:
        index.close()


# ================================================================= the wording


def test_nothing_on_this_tab_names_the_machinery(qtbot, tmp_path: Path) -> None:
    """The same ban the Settings tab has been held to, on the surface the
    review found leaking: he does not have a word for a segment and does not
    need one."""
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel

    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 60)
        said = [w.text() for w in tab.findChildren(QLabel) if w.text()]
        said += [b.text() for b in tab.findChildren(QAbstractButton)]
        said += [b.toolTip() for b in tab.findChildren(QAbstractButton)]
        for box in tab.findChildren(QComboBox):
            said += [box.itemText(i) for i in range(box.count())]
        said.append(tab.status_text)
        banned = (
            "yolo", "cnn", "classifier", "inference", "model", "sensor",
            "segment", "codec", "ffmpeg", "vlc", "rtsp", "sqlite", "index",
        )
        for text in said:
            for word in banned:
                assert word not in text.lower(), text
    finally:
        index.close()
