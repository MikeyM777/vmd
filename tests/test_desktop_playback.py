"""Playback, against a fake pane and a real index."""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

import pytest

from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

from vmd.desktop.playback import BOTH, EVENT_LEAD_SECONDS, NOTHING_RECORDED, PlaybackTab
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


def test_recordings_that_meet_are_drawn_meeting(qtbot, tmp_path: Path) -> None:
    """A day of five-minute files is one unbroken day, and has to look like it.

    Rounding each bar's WIDTH rather than both its edges left a black pixel
    between every pair, so a camera that never stopped came out as a comb of 288
    hairline gaps - a bar claiming a dropout every five minutes. On the tab
    whose whole job is that a real gap is visible, that is the worst kind of
    wrong: it makes the true gaps unfindable among the false ones.
    """
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, end = day_bounds(2026, 8, 11)
        for offset in range(0, 86400, 300):
            index.add(
                "thermal", str(tmp_path / f"{offset}.mp4"),
                start + offset, start + offset + 300, 1000,
            )
        tab.show_day(2026, 8, 11, stream="thermal")

        # At several widths, because whether the rounding bites depends on how
        # many pixels a recording gets: at 1100 px each of the 288 files is
        # 3.82 px and rounding up hides it, and at 1262 - which is what the bar
        # gets on his own 1280 px screen - each is 4.38 px, rounding down, and
        # every second boundary was a black line.
        for width in (1100, 1262, 1366, 1900, 2540):
            tab.bar.setFixedWidth(width)
            image = tab.bar.grab().toImage()
            blank = [
                x
                for x in range(image.width())
                if image.pixelColor(x, 12) == QColor(PALETTE["well"])
            ]
            assert blank == [], (
                f"at {width} px, {len(blank)} pixels of a day that was recorded "
                f"end to end were drawn as a gap"
            )
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


def test_a_camera_that_started_recording_after_the_tab_opened_is_offered(
    qtbot, tmp_path: Path
) -> None:
    """The state every first morning is in, and the console could not leave it.

    Segments only enter the catalogue when ffmpeg closes them, which is five
    minutes after the recorder starts. An operator who installs VMD and opens
    Playback inside those five minutes gets an empty camera list - correctly -
    and then the list was never asked again for the life of the process.
    Changing the day did not help, so the tab said "Nothing has been recorded
    yet" over an archive that had been filling up all afternoon, and the only
    cure was restarting the console.

    The same rule covers a second camera added later: "Both together" was never
    offered until somebody restarted something.
    """
    tab, pane, index = build(qtbot, tmp_path)
    try:
        assert tab.stream_names() == []
        assert tab.status_text == NOTHING_RECORDED

        start, _end = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "a.mp4"), start + 3600, start + 3900, 1000)

        # The operator picks the day. Nothing else - no restart, no alarm.
        tab.date_selector.setDate(QDate(2026, 8, 11))

        assert tab.stream_names() == ["thermal"]
        assert "nothing has been recorded" not in tab.status_text.lower()
        assert len(tab.coverage) == 1
    finally:
        index.close()


def test_a_camera_asked_for_by_name_survives_the_list_being_asked_again(
    qtbot, tmp_path: Path
) -> None:
    """"Show me" names a camera the catalogue may have nothing for.

    `show_day` offers that camera anyway - "and let the empty day say so" -
    because an alarm on a stream whose footage retention has already reclaimed
    still has to be answerable. Re-asking the catalogue rebuilds the list from
    what is on disk, and a camera that is not on disk was dropped out of it: the
    tab came off the camera it had been told to show and drew nothing at all,
    including the movement marks, which is the one answer this tab may never
    give by accident.
    """
    tab, pane, index = build(qtbot, tmp_path)
    try:
        tab.show_day(2026, 8, 11, stream="thermal")
        assert tab.shown_streams() == ["thermal"]
    finally:
        index.close()


def test_coming_back_to_an_empty_playback_tab_asks_the_catalogue_again(
    qtbot, tmp_path: Path
) -> None:
    """The same first morning, by the other route: he just opens the tab.

    Switching to Playback is what he does, not changing the day - so the empty
    list has to be re-asked on the way in as well. Only when it was empty: a tab
    already showing a day must never be reloaded underneath the picture
    somebody is watching.
    """
    from PySide6.QtGui import QShowEvent

    tab, pane, index = build(qtbot, tmp_path)
    try:
        assert tab.stream_names() == []
        start, _end = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "a.mp4"), start + 3600, start + 3900, 1000)

        tab.showEvent(QShowEvent())

        assert tab.stream_names() == ["thermal"]
    finally:
        index.close()


# -------------------------------------------------------- following the picture
#
# The 250 ms timer that walks the line along under the picture. Nothing in this
# suite had ever called it: `_follow_while_playing` refuses to start on a widget
# nobody has shown, and `qtbot.addWidget` does not show one - so every branch of
# it was reachable only in the field, on the tab the operator uses at the moment
# something has already happened.


def test_the_line_stops_following_once_the_footage_has_run_out(
    qtbot, tmp_path: Path
) -> None:
    """The end of the newest recording, which is where watching normally ends.

    The follow timer notices the file has finished and asks for the moment after
    it. There is nothing there, so `_play_at` says so - and left the picture,
    the playhead and the timer exactly as they were, so a quarter of a second
    later the timer asked the same dead question again. Every one of those runs
    a SQLite query for the sentence explaining the gap, redraws the readout and
    repaints, on the thread that draws the window, four times a second, until
    the operator clicks something else. On this console that thread is also the
    one that would show the next alarm.
    """
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path, minutes=5)
    try:
        tab.play_at_time(noon + 10)
        asked: list[str] = []
        real = index.bounds

        def counted(stream: str):
            asked.append(stream)
            return real(stream)

        index.bounds = counted  # type: ignore[method-assign]
        # The player has reached the last moment of the only recording there is.
        pane.seek_seconds(299.99)

        for _ in range(8):
            tab._follow_the_picture()

        assert len(asked) <= 1, f"the catalogue was asked {len(asked)} times for one gap"
        # And the clock stays at the moment the footage ran out rather than
        # being dragged back onto the file that has finished.
        assert tab.playhead_time == pytest.approx(noon + 300.05, abs=0.1)
    finally:
        index.close()


# ------------------------------------------------- the line under the bar
#
# It read `Playing thermal from 14:46:55 - 53100.mp4, 1m 55s in`.
#
# `53100.mp4` is a file on a disk he has no way of opening and no reason to; it
# was put there so a recording could be copied by hand, which means a terminal,
# which he does not have. `1m 55s in` is a distance into something the sentence
# never names - into the file, a five-minute box the recorder happened to close
# at that moment, which is not a thing in his world at all. And nothing in it
# said whether the picture was moving: he pressed Pause and every word stayed.


def test_the_line_under_the_bar_says_what_and_from_when(qtbot, tmp_path: Path) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 125)
        said = tab.status_text
        assert "thermal" in said, said
        assert "12:02:05" in said, said
        assert said.lower().startswith("playing"), said
    finally:
        index.close()


def test_the_line_under_the_bar_never_names_a_file(qtbot, tmp_path: Path) -> None:
    """Not on the ordinary sentence and not on the one about a movement.

    The name is in the log, which is where the person who wants a filename is
    already looking, and it is written there on the same seek.
    """
    start, _end = day_bounds(2026, 8, 11)
    when = start + 3600 + 100
    events = FakeEvents([movement(1, when)])
    tab, pane, index = build_with_events(qtbot, tmp_path, events)
    try:
        index.add("thermal", str(tmp_path / "53100.mp4"), start + 3600, start + 3900, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")

        tab.play_at_time(start + 3700)
        assert ".mp4" not in tab.status_text, tab.status_text
        assert "53100" not in tab.status_text, tab.status_text

        tab.click_at((when - start) / day_span())  # the movement mark
        assert "before the movement" in tab.status_text, tab.status_text
        assert ".mp4" not in tab.status_text, tab.status_text
    finally:
        index.close()


def test_the_line_under_the_bar_never_measures_into_the_file(
    qtbot, tmp_path: Path
) -> None:
    """"1m 55s in" - into what? Into a five-minute box the recorder happened to
    close there, which is not a thing he has ever been told about."""
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        # Two minutes into the day's third five-minute recording.
        tab.play_at_time(noon + 720)
        assert " in" not in tab.status_text, tab.status_text
        assert "2m 00s" not in tab.status_text, tab.status_text
    finally:
        index.close()


def test_the_line_under_the_bar_says_whether_the_picture_is_running(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 125)
        running = tab.status_text
        assert "playing" in running.lower(), running

        tab.transport.play_button.click()
        held = tab.status_text
        assert held != running, "pausing changed nothing under the bar"
        assert "playing" not in held.lower(), held
        assert "12:02:05" in held, held

        tab.transport.play_button.click()
        assert "playing" in tab.status_text.lower(), tab.status_text
    finally:
        index.close()


def test_pausing_does_not_wipe_the_sentence_explaining_a_gap(
    qtbot, tmp_path: Path
) -> None:
    """The gap explanation is somebody else's sentence and has to survive a
    press of the space bar - it is the answer to the question he asked."""
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path, minutes=60)
    try:
        tab.play_at_time(noon + 60)
        tab.play_at_time(noon + 7200)
        assert "no recording" in tab.status_text.lower()
        tab.set_paused(True)
        assert "no recording" in tab.status_text.lower(), tab.status_text
    finally:
        index.close()


def test_both_cameras_are_both_named_under_the_bar(qtbot, tmp_path: Path) -> None:
    """With two pictures up, "Playing thermal" would name one of them."""
    tab, pane, index, start = two_cameras(qtbot, tmp_path)
    try:
        tab.play_at_time(start + 120)
        said = tab.status_text
        assert "thermal" in said and "visible" in said, said
    finally:
        index.close()


def test_a_click_into_a_gap_is_not_undone_a_quarter_of_a_second_later(
    qtbot, tmp_path: Path
) -> None:
    """The readout and the sentence under it must not describe two different hours.

    Clicking an empty part of the bar deliberately leaves the picture alone -
    playing the nearest file instead would show footage from another moment
    under a clock claiming otherwise. But the follow timer went on reading that
    same untouched picture, so it put its file's moment back into the playhead:
    the big readout said 12:01 while the line under it said there was no
    recording at 14:00, and Mark start took the moment the operator was not
    looking at.
    """
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path, minutes=60)
    try:
        tab.play_at_time(noon + 60)
        gap = noon + 7200
        tab.play_at_time(gap)
        assert "no recording" in tab.status_text.lower()

        tab._follow_the_picture()

        assert tab.playhead_time == pytest.approx(gap)
        assert "no recording" in tab.status_text.lower()
    finally:
        index.close()


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
    """To the second, and not rounded to the minute: a clip is marked off this
    readout and a minute is a long time to be wrong by.

    The DAY is no longer part of it. It used to be, and the day is still written
    out in full - in the picker above, which is where it is chosen and the only
    place it can be changed from. See the test below.
    """
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 125)
        said = tab.readout_text
        assert "12:02:05" in said, said
        assert tab.date_selector.button.text().strip(), "the day has to be somewhere"
    finally:
        index.close()


def dressed(qtbot, tmp_path: Path):
    """A tab wearing the application's own appearance, laid out and painted.

    The only measurement that means anything about type on this tab. A
    stylesheet beats setFont, the application stylesheet puts a font-size on
    QWidget, and a readout given its size only by setFont REPORTS the size it
    was asked for while DRAWING at the size of the smallest note on the screen -
    which is exactly what once happened here, and what a test reading `font()`
    cannot see. `fontInfo` is what the widget will actually paint with, and it
    is only true once the widget has been polished.
    """
    from PySide6.QtWidgets import QApplication

    from vmd.desktop.style import stylesheet

    was = QApplication.instance().styleSheet()
    QApplication.instance().setStyleSheet(stylesheet())
    tab, pane, index = build(qtbot, tmp_path)
    tab.resize(1366, 768)
    tab.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    tab.show()
    qtbot.wait(1)

    def undress() -> None:
        index.close()
        QApplication.instance().setStyleSheet(was)

    return tab, pane, index, undress


def test_the_clock_is_the_biggest_thing_on_the_tab(qtbot, tmp_path: Path) -> None:
    """"the running clock at the top is not visible enough".

    It was drawn at the top of the type scale, which is the size the state band
    is read at from across the room - and the band is a WORD, recognised by its
    shape before it is read, while this is eight digits that all look alike and
    three of which change every second. He marks a clip off it. At 16 logical px
    on a panel scaled to 150% that is 24 real pixels, at two metres, at night.

    Measured against the tab's own body text rather than a number, so it stays
    true if the scale moves.
    """
    from vmd.desktop.style import SIZE_BAND, WEIGHT_VALUE

    tab, _pane, index, undress = dressed(qtbot, tmp_path)
    try:
        clock = tab.readout.fontInfo().pixelSize()
        assert clock > SIZE_BAND, f"the clock draws at {clock} px, the top of the scale"
        assert clock >= 2 * tab._status.fontInfo().pixelSize(), (
            f"the clock is {clock} px and the sentence under the bar is "
            f"{tab._status.fontInfo().pixelSize()} px"
        )
        assert tab.readout.fontInfo().weight() >= WEIGHT_VALUE, (
            f"the clock is drawn at weight {tab.readout.fontInfo().weight()}"
        )
    finally:
        undress()


def test_the_clock_is_drawn_in_the_brightest_ink_there_is(qtbot, tmp_path: Path) -> None:
    """Read off the painted pixels, not off the stylesheet that asked for them.

    A colour named in a stylesheet somebody else's rule overrides is a colour
    nobody sees, and this label has had exactly that happen to its size.
    """
    tab, _pane, index, undress = dressed(qtbot, tmp_path)
    try:
        tab.play_at_time(day_bounds(2026, 8, 11)[0])
        image = tab.readout.grab().toImage()
        ink = QColor(PALETTE["ink"])
        painted = {
            image.pixelColor(x, y).name()
            for x in range(image.width())
            for y in range(image.height())
        }
        assert ink.name() in painted, (
            f"the clock is not drawn in {ink.name()}; it is drawn in {sorted(painted)}"
        )
    finally:
        undress()


def test_the_pointer_time_and_the_drift_do_not_ride_on_the_clock(
    qtbot, tmp_path: Path
) -> None:
    """They are worth saying and they are not what this readout is for.

    At the clock's size they are also 45 more characters on a row with five
    buttons to fit at 1280 px, which is how a clock big enough to read ends up
    pushing the zooms off the screen.
    """
    tab, _pane, index, undress = dressed(qtbot, tmp_path)
    try:
        start, _end = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "a.mp4"), start, start + 600, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.play_at_time(start + 120)
        tab.hover_at(0.5)

        assert tab.readout.text() == tab.readout_text, tab.readout.text()
        assert tab.hover_text and tab.hover_text in tab.readout_note.text()
        assert (
            tab.readout_note.fontInfo().pixelSize() < tab.readout.fontInfo().pixelSize()
        )
    finally:
        undress()


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


def test_which_zoom_is_on_can_be_seen(qtbot, tmp_path: Path) -> None:
    """Three identical buttons say nothing about which one is in force, and the
    operator would have to read it off the bar - which is the thing he is trying
    to understand in the first place.

    Measured with the application's own appearance on. Qt's default style draws
    a checked button sunken all by itself, so without the stylesheet this test
    would pass whatever the console actually looks like - and the console does
    not use Qt's default style.
    """
    from PySide6.QtWidgets import QApplication

    from vmd.desktop.style import stylesheet
    from vmd.desktop.timeline import ONE_HOUR, WHOLE_DAY

    was = QApplication.instance().styleSheet()
    QApplication.instance().setStyleSheet(stylesheet())
    try:
        tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
        try:
            tab.play_at_time(noon + 60)
            on = [name for name, b in tab.zoom_buttons.items() if b.isChecked()]
            assert on == [WHOLE_DAY], on

            button = tab.zoom_buttons[ONE_HOUR]
            before = button.grab().toImage()
            qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
            after = button.grab().toImage()
            assert [n for n, b in tab.zoom_buttons.items() if b.isChecked()] == [ONE_HOUR]
            assert before != after, "the chosen zoom is drawn exactly like the other two"
        finally:
            index.close()
    finally:
        QApplication.instance().setStyleSheet(was)


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


def test_the_drift_is_measured_on_the_clock_and_not_inside_the_two_files(
    qtbot, tmp_path: Path
) -> None:
    """The two pictures are two different files, and the files do not begin together.

    Each recorder starts rotating its own five-minute segments when its own
    stream connects, and nothing lines those boundaries up: after any restart of
    one camera the two are offset by however many seconds separated the two
    connections. `position_seconds` is a position INSIDE the open file, so
    subtracting one from the other measures the distance between two arbitrary
    file boundaries and calls it drift.

    Here the visible camera's file began 137 s before the thermal one's. At the
    same wall-clock instant the two players are 137 s apart inside their files
    and 0 s apart on the clock, and it is the clock the operator is watching.
    Reported wrongly, this is the console crying wolf - permanently, four times
    a second - about the one property two cameras exist for.
    """
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _end = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "t.mp4"), start + 3600, start + 4200, 1000)
        index.add("visible", str(tmp_path / "v.mp4"), start + 3463, start + 4063, 1000)
        tab.show_day(2026, 8, 11, stream=BOTH)

        tab.play_at_time(start + 3700)
        # The same instant, reached at two different offsets into two files.
        assert pane.at_seconds == pytest.approx(100.0)
        assert tab.second_pane.at_seconds == pytest.approx(237.0)
        assert tab.drift_seconds() == pytest.approx(0.0, abs=0.01)

        # And a real drift is still a real drift: two seconds behind on the
        # clock is two seconds, whatever the files are doing.
        tab.second_pane.seek_seconds(235.0)
        assert tab.drift_seconds() == pytest.approx(2.0, abs=0.01)
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


# ------------------------------------------------- two pictures on the screen
#
# Everything above this line drives a `FakeVideoPane`, which is not a QWidget -
# so not one of those tests has ever laid a picture out, and "both together"
# reported from the field as *"only one picture"* passed all of them. The pane
# the console really hands this tab is a widget in a splitter, and that is what
# the next two put on a screen and measure.


class WidgetPane(QWidget, FakeVideoPane):
    """The fake pane, as the thing the console actually builds: a widget."""

    def __init__(self, parent: QWidget | None = None) -> None:
        QWidget.__init__(self, parent)
        FakeVideoPane.__init__(self)

    def show(self, url=None, at_seconds: float = 0.0) -> None:  # noqa: A003
        # QWidget.show() and VideoPane.show(url) are the same word for two
        # different things, and the widget's own is the one Qt calls.
        if url is None:
            QWidget.show(self)
            return
        FakeVideoPane.show(self, url, at_seconds)


def two_widget_cameras(qtbot, tmp_path: Path, width: int = 1000):
    tab = PlaybackTab(index=SegmentIndex(tmp_path / "segments.db"), pane=WidgetPane())
    qtbot.addWidget(tab)
    index = tab._index
    start, _end = day_bounds(2026, 8, 11)
    index.add("thermal", str(tmp_path / "t.mp4"), start, start + 600, 1000)
    index.add("visible", str(tmp_path / "v.mp4"), start, start + 600, 1000)
    tab.resize(width, 600)
    # Laid out and painted, and never on anybody's desktop.
    tab.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    tab.show()
    tab.show_day(2026, 8, 11, stream=BOTH)
    return tab, index, start


def test_both_together_puts_two_pictures_on_the_screen(qtbot, tmp_path: Path) -> None:
    """His words: "only one picture".

    Both panes were built, both were in the splitter, the second was told to
    play the right file and told to be visible - and it was nought pixels wide,
    because a QSplitter gives a hidden child no width and does not hand any
    back when it is shown again. Everything except the one thing he was looking
    for was working, which is why no test caught it.

    Measured in pixels on a laid-out tab, because pixels are what he was
    complaining about.
    """
    tab, index, start = two_widget_cameras(qtbot, tmp_path)
    try:
        tab.play_at_time(start + 120)
        first, second = tab._pane, tab.second_pane
        assert second.isVisibleTo(tab), "the second picture was never put up"
        assert second.width() > 0, "the second picture is on screen at no width"
        # Two pictures, not one picture and a sliver: neither of them may be
        # squeezed into a corner of the other.
        assert second.width() > first.width() / 3, (
            f"the two pictures are {first.width()} and {second.width()} px wide"
        )
    finally:
        index.close()


def test_one_camera_gets_the_whole_wall_back(qtbot, tmp_path: Path) -> None:
    """And the room the second picture took is not left behind as a black band."""
    tab, index, start = two_widget_cameras(qtbot, tmp_path)
    try:
        tab.play_at_time(start + 120)
        tab.stream_selector.setCurrentText("thermal")
        tab.play_at_time(start + 120)
        qtbot.wait(1)  # the splitter re-lays out on the event loop, not inline
        assert not tab.second_pane.isVisibleTo(tab)
        assert tab._pane.width() > tab.width() / 2
    finally:
        index.close()


def test_the_wall_the_operator_dragged_is_not_re_shared_on_every_seek(
    qtbot, tmp_path: Path
) -> None:
    """He can move the divider, and a skip forward may not put it back.

    The fix for "only one picture" shares the wall equally as the second
    picture goes up. Doing that on every seek instead would be a divider that
    springs back four times a second.
    """
    tab, index, start = two_widget_cameras(qtbot, tmp_path)
    try:
        tab.play_at_time(start + 120)
        tab._wall.setSizes([700, 300])
        qtbot.wait(1)
        his = tab._wall.sizes()
        tab.play_at_time(start + 240)
        assert tab._wall.sizes() == his, "the divider moved on its own"
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


def test_marking_a_start_does_not_move_the_end(qtbot, tmp_path: Path) -> None:
    """The exact sequence, and what it used to save.

    He watches to 12:10 and presses **Mark end**. He goes back, finds the
    moment he actually wants - 12:20 - and presses **Mark start**. `sorted()`
    stood between these two buttons and everything downstream, so the pair was
    re-read as "earliest, latest": the console saved 12:10 to 12:20. Ten
    minutes ENDING at the moment he had just named as the beginning - the
    footage before the thing he was keeping and none of the footage after it -
    with nothing on the screen to say so.

    The end may never be moved to a time he did not name. Here it is dropped,
    because after a later start it is not an end any more.
    """
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 600)
        tab.mark_the_end()
        assert tab.clip_to == pytest.approx(noon + 600)

        tab.play_at_time(noon + 1200)
        tab.mark_the_start()

        assert tab.clip_from == pytest.approx(noon + 1200), "the start he asked for"
        assert tab.clip_to != pytest.approx(noon + 1200), "his start became the end"
        assert tab.clip_to is None, f"the end moved to {tab.clip_to}"
        # And he is told, rather than left with a Save button that has quietly
        # stopped working.
        assert "end" in tab.status_text.lower(), tab.status_text
        assert not tab.save_clip.isEnabled()
    finally:
        index.close()


def test_marking_an_end_before_the_start_does_not_move_the_start(
    qtbot, tmp_path: Path
) -> None:
    """The same rule from the other side, so neither button can reinterpret the
    other one's mark."""
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 1200)
        tab.mark_the_start()
        tab.play_at_time(noon + 600)
        tab.mark_the_end()

        assert tab.clip_to == pytest.approx(noon + 600)
        assert tab.clip_from is None, f"the start moved to {tab.clip_from}"
        assert "start" in tab.status_text.lower(), tab.status_text
    finally:
        index.close()


def test_a_clip_saved_after_the_marks_crossed_is_the_one_he_marked(
    qtbot, tmp_path: Path
) -> None:
    """The whole point of the two above: what ends up on the disk.

    Marking end-then-a-later-start used to write the range BEFORE the start.
    Now there is nothing to write until he marks an end after it, and when he
    does, that is the range that is written.
    """
    folder = tmp_path / "keep"
    folder.mkdir()
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.ask_for_folder = lambda: str(folder)
        tab.run_ffmpeg = _pretend_ffmpeg

        tab.play_at_time(noon + 600)
        tab.mark_the_end()
        tab.play_at_time(noon + 1200)
        tab.mark_the_start()
        assert tab.save_clip_now(wait=True) is None, "it saved the range he cancelled"

        tab.play_at_time(noon + 1500)
        tab.mark_the_end()
        tab.save_clip_now(wait=True)
        assert tab.clip_from == pytest.approx(noon + 1200)
        assert tab.clip_to == pytest.approx(noon + 1500)
    finally:
        index.close()


# --------------------------------------------------- the clip you can see
#
# "the marked range is invisible against the green timeline". It was drawn as a
# FILL: the accent at alpha 70, laid over a bar whose recorded time is `ok`
# green. Amber over green is a slightly different green, and at two metres the
# piece he had marked was a shade nobody could find.
#
# Picking another fill colour is not the answer, because the bar's own colours
# are not fixed - it is green where there is footage, near-black where there is
# not, and red wherever something moved. The standard editing answer is to dim
# everything OUTSIDE the marks, so the clip is not a colour at all: it is the
# part that was left alone. These tests ask that question and never ask for a
# particular colour.


def a_fully_recorded_day(qtbot, tmp_path: Path):
    """One recording covering the whole day, so every pixel of the bar is
    coverage and any difference in it is the marking and nothing else."""
    tab, pane, index = build(qtbot, tmp_path)
    start, end = day_bounds(2026, 8, 11)
    index.add("thermal", str(tmp_path / "all.mp4"), start, end, 1000)
    tab.show_day(2026, 8, 11, stream="thermal")
    return tab, pane, index, start, end - start


def test_the_marked_clip_is_the_bright_part_of_the_bar(qtbot, tmp_path: Path) -> None:
    tab, pane, index, start, span = a_fully_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(start + span * 0.45)
        tab.mark_the_start()
        tab.play_at_time(start + span * 0.55)
        tab.mark_the_end()
        tab.play_at_time(start + span * 0.50)

        tab.bar.resize(600, 60)
        image = tab.bar.grab().toImage()
        width = image.width()
        kept = image.pixelColor(round(width * 0.52), 8)
        before = image.pixelColor(round(width * 0.20), 8)
        after = image.pixelColor(round(width * 0.80), 8)

        # Nothing is laid over the clip: it is the recorded colour, untouched.
        assert kept == QColor(PALETTE["ok"]), kept.name()
        # And both sides of it are plainly darker. Lightness rather than a
        # colour, because the answer must not depend on which colour the
        # coverage happens to be.
        for outside, where in ((before, "before"), (after, "after")):
            assert kept.lightness() - outside.lightness() > 30, (
                f"the day {where} the clip is {outside.name()} against the "
                f"clip's {kept.name()}"
            )
    finally:
        index.close()


def test_a_movement_mark_outside_the_clip_is_dimmed_with_the_rest_of_the_day(
    qtbot, tmp_path: Path
) -> None:
    """The scrim goes over the movement marks too, and that is deliberate.

    The old tint had to stay out of everything's way. A scrim's whole job is to
    put the excluded day into the background at once - and a red line outside
    the clip drawn as brightly as one inside it would be the loudest thing on
    the half of the bar he has just decided to throw away.
    """
    start, end = day_bounds(2026, 8, 11)
    span = end - start
    events = FakeEvents(
        [movement(1, start + span * 0.50), movement(2, start + span * 0.20)]
    )
    tab, pane, index = build_with_events(qtbot, tmp_path, events)
    try:
        index.add("thermal", str(tmp_path / "all.mp4"), start, end, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.play_at_time(start + span * 0.45)
        tab.mark_the_start()
        tab.play_at_time(start + span * 0.60)
        tab.mark_the_end()

        tab.bar.resize(600, 60)
        image = tab.bar.grab().toImage()
        width = image.width()
        inside = _reddest(image, round(width * 0.50))
        outside = _reddest(image, round(width * 0.20))
        assert inside is not None and outside is not None, "a mark was not drawn"
        assert inside.lightness() - outside.lightness() > 20, (
            f"the movement outside the clip is {outside.name()} and the one "
            f"inside it is {inside.name()}"
        )
    finally:
        index.close()


def _reddest(image, around: int):
    """The most alarm-coloured pixel within a few of this column."""
    best = None
    for x in range(max(around - 4, 0), min(around + 5, image.width())):
        colour = image.pixelColor(x, 8)
        if colour.red() > colour.green() and (best is None or colour.red() > best.red()):
            best = colour
    return best


# ------------------------------------------------- the clip, adjusted by hand
#
# "he wants to adjust the clip visually with the mouse". The brackets are an
# addition to Mark start and Mark end and never a replacement: "i rather
# buttons" is the standing instruction on this tab, and a mark that can only be
# made by dragging is a mark somebody on a trackpad cannot place accurately.


def a_marked_clip(qtbot, tmp_path: Path, first: float = 0.40, last: float = 0.70):
    """A whole day recorded, a clip marked across the middle of it, and the bar
    at a known width so a pixel is a known moment."""
    tab, pane, index, start, span = a_fully_recorded_day(qtbot, tmp_path)
    tab.play_at_time(start + span * first)
    tab.mark_the_start()
    tab.play_at_time(start + span * last)
    tab.mark_the_end()
    tab.bar.resize(1000, 60)
    return tab, pane, index, start, span


def press(qtbot, bar, x: int) -> None:
    qtbot.mousePress(bar, Qt.MouseButton.LeftButton, pos=QPoint(x, 20))


def drag(qtbot, bar, to: int) -> None:
    qtbot.mouseMove(bar, QPoint(to, 20))


def let_go(qtbot, bar, x: int) -> None:
    qtbot.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=QPoint(x, 20))


def test_the_end_bracket_can_be_dragged_and_takes_only_the_end(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index, start, span = a_marked_clip(qtbot, tmp_path)
    try:
        was = tab.clip_from
        press(qtbot, tab.bar, 700)
        drag(qtbot, tab.bar, 600)
        let_go(qtbot, tab.bar, 600)

        assert tab.clip_from == pytest.approx(was), "the start moved with the end"
        assert tab.clip_to == pytest.approx(start + span * 0.60, abs=1.0)
    finally:
        index.close()


def test_the_start_bracket_can_be_dragged_and_takes_only_the_start(
    qtbot, tmp_path: Path
) -> None:
    tab, pane, index, start, span = a_marked_clip(qtbot, tmp_path)
    try:
        was = tab.clip_to
        press(qtbot, tab.bar, 400)
        drag(qtbot, tab.bar, 250)
        let_go(qtbot, tab.bar, 250)

        assert tab.clip_to == pytest.approx(was), "the end moved with the start"
        assert tab.clip_from == pytest.approx(start + span * 0.25, abs=1.0)
    finally:
        index.close()


def test_dragging_the_start_past_the_end_stops_there_rather_than_swapping(
    qtbot, tmp_path: Path
) -> None:
    """Swapping is what Mark start used to do, and it is worse with a mouse.

    Half way through one gesture the thing under his finger would silently
    become the other end of the clip, and then keep following him with the
    range inside out.
    """
    tab, pane, index, start, span = a_marked_clip(qtbot, tmp_path)
    try:
        end_was = tab.clip_to
        press(qtbot, tab.bar, 400)
        drag(qtbot, tab.bar, 950)  # well past the end bracket at 700
        let_go(qtbot, tab.bar, 950)

        assert tab.clip_to == pytest.approx(end_was), "the end was pushed along"
        assert tab.clip_from == pytest.approx(end_was), "the start went past the end"
        assert tab.clip_from <= tab.clip_to
    finally:
        index.close()


# How far from a bracket a hand on a trackpad may land and still mean it.
#
# A number written here rather than read out of the module under test, and that
# is the whole value of it: a test that misses by BRACKET_GRAB_PIXELS moves with
# the constant, so it passes against a grab of two pixels as happily as against
# a generous one. It was written that way first and a mutation walked through
# it. Twelve logical pixels is about three millimetres on his panel.
A_HAND_MISSES_BY = 12


def test_a_bracket_is_caught_by_a_hand_that_misses_it(qtbot, tmp_path: Path) -> None:
    """A trackpad, not a precision tool. The grab is much wider than the
    drawing, which is the opposite of the rule the movement marks follow - a
    movement mark competes with the plain time under the pointer and a bracket
    competes with nothing.
    """
    from vmd.desktop.playback import BRACKET_GRAB_PIXELS, BRACKET_WIDTH

    assert BRACKET_GRAB_PIXELS >= A_HAND_MISSES_BY > BRACKET_WIDTH

    for miss in (-A_HAND_MISSES_BY, A_HAND_MISSES_BY):
        where = tmp_path / f"miss{abs(miss)}{'left' if miss < 0 else 'right'}"
        where.mkdir()
        tab, pane, index, start, span = a_marked_clip(qtbot, where)
        try:
            press(qtbot, tab.bar, 700 + miss)
            drag(qtbot, tab.bar, 600)
            let_go(qtbot, tab.bar, 600)
            assert tab.clip_to == pytest.approx(start + span * 0.60, abs=1.0), (
                f"a press {miss} px from the bracket did not take hold of it"
            )
        finally:
            index.close()


def test_a_press_well_away_from_a_bracket_is_still_a_seek(
    qtbot, tmp_path: Path
) -> None:
    """The bar is a timeline first. Marking a clip may not take the click that
    plays a moment away from the rest of it."""
    tab, pane, index, start, span = a_marked_clip(qtbot, tmp_path)
    try:
        was = (tab.clip_from, tab.clip_to)
        press(qtbot, tab.bar, 550)
        let_go(qtbot, tab.bar, 550)
        assert (tab.clip_from, tab.clip_to) == was
        assert tab.playhead_time == pytest.approx(start + span * 0.55, abs=1.0)
    finally:
        index.close()


def test_taking_hold_of_a_bracket_does_not_move_the_picture(
    qtbot, tmp_path: Path
) -> None:
    """Trimming a clip is not a request to go and watch that second, and a seek
    on every mouse move across the bar is a player asked to open a file five
    hundred times."""
    tab, pane, index, start, span = a_marked_clip(qtbot, tmp_path)
    try:
        # Left somewhere the bracket is NOT, which is the whole test: marking
        # the clip leaves the playhead on the end of it, and a press on the end
        # bracket that seeks would then seek to where the clock already was.
        tab.play_at_time(start + span * 0.20)
        standing = tab.playhead_time
        restarts = pane.restarts
        press(qtbot, tab.bar, 700)
        for x in range(690, 600, -10):
            drag(qtbot, tab.bar, x)
        let_go(qtbot, tab.bar, 600)

        assert tab.playhead_time == pytest.approx(standing), "the clock moved"
        assert pane.restarts == restarts, "the player was asked to reopen"
    finally:
        index.close()


def test_a_mark_off_the_side_of_the_window_has_no_bracket_to_grab(
    qtbot, tmp_path: Path
) -> None:
    """A handle drawn at the edge of the bar for a mark that is really hours
    off the left of it is a control that moves the wrong thing when it is
    taken hold of."""
    tab, pane, index, start, span = a_marked_clip(qtbot, tmp_path, first=0.10)
    try:
        tab.play_at_time(start + span * 0.70)
        tab.set_zoom("1 hour")  # an hour around 16:48; the start is at 02:24
        tab.bar.resize(1000, 60)
        assert tab.bar.bracket_near(0) is None
        assert tab.bar.bracket_near(3) is None

        was = tab.clip_from
        press(qtbot, tab.bar, 2)
        let_go(qtbot, tab.bar, 2)
        assert tab.clip_from == pytest.approx(was), "the edge of the bar moved a mark"
    finally:
        index.close()


def test_the_two_mark_buttons_still_work_beside_the_brackets(
    qtbot, tmp_path: Path
) -> None:
    """The brackets are an addition. "i rather buttons"."""
    tab, pane, index, start, span = a_fully_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(start + 60)
        tab.mark_start.click()
        tab.play_at_time(start + 180)
        tab.mark_end.click()
        assert tab.clip_from == pytest.approx(start + 60)
        assert tab.clip_to == pytest.approx(start + 180)
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


def test_the_two_buttons_about_the_marks_say_that_they_are_about_the_marks(
    qtbot, tmp_path: Path
) -> None:
    """They read **Save it…** and **Clear**, at the far right of a row whose
    other controls are about the footage, the day and the speed. Save what.
    Clear what. Both are about the range between **Mark start** and **Mark
    end**, and neither of them said so.
    """
    tab, _pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        assert tab.save_clip.text() == "Save the marked clip"
        assert tab.clear_marks.text() == "Clear the marks"
        # Both name the marks the other two buttons make, so the four of them
        # read as one operation.
        for said in (tab.save_clip.text(), tab.clear_marks.text()):
            assert "mark" in said.lower(), said
        assert "Mark" in tab.mark_start.text() and "Mark" in tab.mark_end.text()

        # And they still do what they did. Longer words on a disabled button
        # would be a rename that quietly broke the thing it renamed.
        assert not tab.save_clip.isEnabled(), "offered with nothing marked"
        tab.play_at_time(noon + 60)
        tab.mark_start.click()
        tab.play_at_time(noon + 180)
        tab.mark_end.click()
        assert tab.save_clip.isEnabled()
        tab.clear_marks.click()
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


def test_zooming_with_no_playhead_lands_on_footage_not_on_the_middle_of_the_clock(
    qtbot, tmp_path: Path
) -> None:
    """The first morning: recording started at midnight and stopped at 01:35.

    He opens the day and presses "1 hour" without having clicked on the bar, so
    there is no playhead to zoom around. The window used to centre on the middle
    of what was on screen, which for a whole day is noon - so an hour of zoom
    jumped to 11:30-12:25, drew an empty bar, and left the line underneath still
    saying "1h 25m recorded". Everything he had was off the left-hand edge and
    nothing on the screen said which way to go looking for it.

    So with no playhead the window goes to the footage instead of to the clock.
    """
    from vmd.desktop.timeline import ONE_HOUR

    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _end = day_bounds(2026, 8, 11)
        for offset in range(0, 95 * 60, 300):
            index.add(
                "thermal", str(tmp_path / f"{offset}.mp4"),
                start + offset, start + offset + 300, 1000,
            )
        tab.show_day(2026, 8, 11, stream="thermal")
        assert tab.playhead_time is None, "this is the state with nothing clicked"

        qtbot.mouseClick(tab.zoom_buttons[ONE_HOUR], Qt.MouseButton.LeftButton)
        assert tab.view_end - tab.view_start == pytest.approx(3600.0)
        assert tab.coverage, (
            "an hour of zoom landed on an empty bar: "
            f"{tab.view_start - start:.0f}s to {tab.view_end - start:.0f}s "
            "into a day recorded from 0s to 5700s"
        )
    finally:
        index.close()


def test_zooming_with_nothing_recorded_at_all_still_gives_an_hour(
    qtbot, tmp_path: Path
) -> None:
    """There is no footage to aim at, so the middle of the day is as good an
    answer as any - and it must not raise or come back as a whole day."""
    from vmd.desktop.timeline import ONE_HOUR

    tab, pane, index = build(qtbot, tmp_path)
    try:
        tab.show_day(2026, 8, 11, stream="thermal")
        qtbot.mouseClick(tab.zoom_buttons[ONE_HOUR], Qt.MouseButton.LeftButton)
        assert tab.view_end - tab.view_start == pytest.approx(3600.0)
    finally:
        index.close()


def test_the_day_is_named_once_on_the_tab_and_not_twice(qtbot, tmp_path: Path) -> None:
    """"Wednesday 12 August 2026" was drawn in the day-picker button and again
    as the heading forty pixels below it.

    The picker is where the day is chosen and where it belongs. What the big
    readout is for is the moment inside that day - the one thing on this tab
    that changes while he watches - and spending two thirds of it on a date that
    cannot change without the button above it changing first buys nothing and
    reads as a fault: the same words twice, in two different type sizes, for no
    reason the reader can find.
    """
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        tab.play_at_time(noon + 125)
        picked = tab.date_selector.button.text()
        assert "August" in picked and "2026" in picked, picked

        said = tab.readout_text
        assert "12:02:05" in said, said
        for word in ("August", "2026", "Tuesday"):
            assert word not in said, f"the day is drawn twice: {picked!r} and {said!r}"
    finally:
        index.close()


def test_with_nothing_playing_the_readout_says_so_rather_than_the_date_again(
    qtbot, tmp_path: Path
) -> None:
    """The state the tab opens in. There is no moment to show, so showing the
    date was showing the button above it a second time - and it left the biggest
    thing on the tab saying something that was already said and nothing about
    whether anything was playing."""
    tab, pane, index, noon = a_recorded_day(qtbot, tmp_path)
    try:
        assert tab.playhead_time is None
        said = tab.readout_text.lower()
        assert said.strip(), "the readout must never be blank"
        assert "playing" in said, said
        assert "august" not in said, said
    finally:
        index.close()


def test_one_camera_having_a_gap_does_not_blank_the_other(qtbot, tmp_path: Path) -> None:
    """"Both together" showed nothing at all for any moment the FIRST camera
    missed, even when the second had it - so a minute the thermal dropped was
    answered with two black rectangles and a sentence saying there is no
    recording, while the visible camera had the whole minute on disk.

    One camera having a gap is not both cameras having a gap, and this is the
    tab he asked to have "both together" fixed on.
    """
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _end = day_bounds(2026, 8, 11)
        # The thermal stops after five minutes. The visible runs for an hour.
        index.add("thermal", str(tmp_path / "t.mp4"), start, start + 300, 1000)
        index.add("visible", str(tmp_path / "v.mp4"), start, start + 3600, 1000)
        tab.show_day(2026, 8, 11, stream=BOTH)

        playing = tab._play_at(start + 1800)  # half an hour in: thermal has none

        assert playing is True, "nothing was shown, though the visible had it"
        said = tab.status_text.lower()
        assert "thermal" in said and "visible" in said, said
        assert NOTHING_RECORDED.lower() not in said, (
            "told him there is no recording while showing him a recording"
        )
    finally:
        tab.close()


def test_both_cameras_missing_a_moment_still_says_there_is_nothing(
    qtbot, tmp_path: Path
) -> None:
    """The other half. A picture on screen with a sentence saying there is no
    recording reads as a fault in the console - and so does the reverse."""
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _end = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "t.mp4"), start, start + 300, 1000)
        index.add("visible", str(tmp_path / "v.mp4"), start, start + 300, 1000)
        tab.show_day(2026, 8, 11, stream=BOTH)

        assert tab._play_at(start + 1800) is False
        assert "nothing" in tab.status_text.lower(), tab.status_text
    finally:
        tab.close()


def test_the_clock_is_drawn_at_the_size_the_scale_says(qtbot, tmp_path: Path) -> None:
    """It is the one figure on this tab that IS the tab, and it lives in the
    type scale rather than inside this file - a size defined in one tab is a
    size the next tab cannot honour.

    Measured off the widget with the application's own appearance on, because a
    stylesheet beats `setFont` and this exact readout has been drawn at the
    wrong size before for that reason.
    """
    from PySide6.QtWidgets import QApplication

    from vmd.desktop.style import SIZE_BAND, SIZE_CLOCK, stylesheet

    was = QApplication.instance().styleSheet()
    QApplication.instance().setStyleSheet(stylesheet())
    try:
        tab, _pane, _index = build(qtbot, tmp_path)
        try:
            tab.resize(1366, 768)
            tab.show()
            qtbot.waitExposed(tab)
            assert tab.readout.fontInfo().pixelSize() == SIZE_CLOCK
            assert SIZE_CLOCK > SIZE_BAND, "the sixth size is not bigger than the fifth"
        finally:
            tab.close()
    finally:
        QApplication.instance().setStyleSheet(was)
