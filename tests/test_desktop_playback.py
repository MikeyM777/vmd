"""Playback, against a fake pane and a real index."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtGui import QColor

from vmd.desktop.playback import EVENT_LEAD_SECONDS, PlaybackTab
from vmd.desktop.style import PALETTE
from vmd.desktop.timeline import day_bounds
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
        assert image.pixelColor(50, 12) == QColor(PALETTE["ok"])
        assert image.pixelColor(150, 12) == QColor(PALETTE["well"])
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
    assert "index" in tab.status_text.lower()


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
