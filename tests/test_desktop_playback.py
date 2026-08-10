"""Playback, against a fake pane and a real index."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtGui import QColor

from vmd.desktop.playback import PlaybackTab
from vmd.desktop.style import PALETTE
from vmd.desktop.timeline import day_bounds
from vmd.desktop.video import FakeVideoPane
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
