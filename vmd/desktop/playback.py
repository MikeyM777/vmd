"""Looking back through what was recorded.

Everything drawn here is something the segment index can prove: each bar is one
file, at the start and end recorded when that file was closed. The browser
version drew an unbroken day whether or not anything had been written, so a
gap - the one thing an operator most needs to see - looked exactly like an hour
of footage. Nothing is drawn here that is not in the index, and a day with no
recordings says so in words.

The other thing the browser version got wrong was the click. It measured the
pointer against the wrong element, so the time it scrubbed to was not the time
under the pointer. Here the bar measures the click against its own width and
nothing else, and that mapping is what the tests pin down.
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.style import PALETTE
from vmd.desktop.timeline import coverage_bars, day_bounds, seek_target, time_at
from vmd.desktop.video import VideoPane
from vmd.storage.index import Segment, SegmentIndex

logger = logging.getLogger(__name__)

BAR_HEIGHT = 34
PLAYHEAD_WIDTH = 3


class TimelineBar(QWidget):
    """One day, drawn: recorded spans filled, gaps left as the dark well.

    A click is turned into a fraction of this widget's own width. That is the
    whole contract, and it is measured against `self.width()` rather than any
    parent or event offset - the browser version used an offset relative to
    another element and scrubbed to a time nobody asked for.
    """

    def __init__(self, tab: "PlaybackTab", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tab = tab
        self._bars: list[tuple[float, float]] = []
        self._playhead: float | None = None
        self.setMinimumHeight(BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_bars(
        self, bars: list[tuple[float, float]], playhead: float | None = None
    ) -> None:
        self._bars = list(bars)
        self._playhead = playhead
        self.update()

    def set_playhead(self, playhead: float | None) -> None:
        self._playhead = playhead
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        width = self.width()
        height = self.height()
        painter.fillRect(0, 0, width, height, QColor(PALETTE["well"]))
        recorded = QColor(PALETTE["ok"])
        for left, span in self._bars:
            x = int(round(left * width))
            # At least one pixel: a segment shorter than a pixel of the day is
            # still a segment, and drawing nothing would claim it is a gap.
            w = max(1, int(round(span * width)))
            painter.fillRect(x, 0, min(w, width - x), height, recorded)
        if self._playhead is not None:
            x = int(round(self._playhead * width)) - PLAYHEAD_WIDTH // 2
            x = min(max(x, 0), max(width - PLAYHEAD_WIDTH, 0))
            painter.fillRect(x, 0, PLAYHEAD_WIDTH, height, QColor(PALETTE["accent"]))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt's name
        width = max(self.width(), 1)
        self._tab.click_at(event.position().x() / width)
        event.accept()


class PlaybackTab(QWidget):
    """A day of recordings, and a player pointed into it."""

    def __init__(
        self, index: SegmentIndex, pane: VideoPane, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._pane = pane
        today = datetime.date.today()
        self._day_start, self._day_end = day_bounds(today.year, today.month, today.day)
        self._segments: list[Segment] = []
        self.coverage: list[tuple[float, float]] = []
        self.status_text = ""
        # The offset is recorded here and applied by the player: VideoPane.show
        # takes a URL and nothing else, so the "start this many seconds in" half
        # of a seek is carried in this attribute rather than in the URL. A later
        # task hands it to libVLC. Until then the file opens at its beginning,
        # and this says by how much that is wrong.
        self.seek_offset = 0.0
        self.playhead_time: float | None = None
        # Guards the controls while they are being set from code, so that
        # populating the stream list does not reload the day underneath itself.
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Day"))
        self.date_selector = QDateEdit()
        self.date_selector.setCalendarPopup(True)
        self.date_selector.setDisplayFormat("yyyy-MM-dd")
        self.date_selector.setDate(QDate(today.year, today.month, today.day))
        self.date_selector.dateChanged.connect(self._controls_changed)
        controls.addWidget(self.date_selector)
        controls.addWidget(QLabel("Stream"))
        self.stream_selector = QComboBox()
        self.stream_selector.currentTextChanged.connect(self._controls_changed)
        controls.addWidget(self.stream_selector)
        controls.addStretch(1)
        layout.addLayout(controls)

        if isinstance(pane, QWidget):
            layout.addWidget(pane, 1)

        self.bar = TimelineBar(self)
        layout.addWidget(self.bar)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self.refresh_streams()

    # ------------------------------------------------------------- the streams

    def refresh_streams(self) -> list[str]:
        """The streams the index has recordings for, sorted.

        Not the streams in settings: a camera taken out of the configuration
        still has hours on disk, and the question this tab answers is what is on
        disk, not what is currently being recorded.
        """
        try:
            names = sorted({segment.stream for segment in self._index.all()})
        except sqlite3.Error as error:
            self._report_unreadable(error)
            return []
        chosen = self.stream_selector.currentText()
        self._loading = True
        try:
            self.stream_selector.clear()
            self.stream_selector.addItems(names)
            if chosen in names:
                self.stream_selector.setCurrentText(chosen)
        finally:
            self._loading = False
        return names

    def stream_names(self) -> list[str]:
        return [self.stream_selector.itemText(i) for i in range(self.stream_selector.count())]

    # ----------------------------------------------------------------- the day

    def show_day(self, year: int, month: int, day: int, stream: str) -> None:
        """Draw this local day for this stream."""
        # Re-read the list first: recording has been going on since the tab was
        # built, and a stream that started since then is on disk now.
        self.refresh_streams()
        self._loading = True
        try:
            self.date_selector.setDate(QDate(year, month, day))
            if self.stream_selector.findText(stream) < 0:
                # Asked for a stream the index has never heard of - offer it
                # anyway, and let the empty day say so.
                self.stream_selector.addItem(stream)
            self.stream_selector.setCurrentText(stream)
        finally:
            self._loading = False
        self._reload()

    def _controls_changed(self, *_args) -> None:
        if self._loading:
            return
        self._reload()

    def _reload(self) -> None:
        date = self.date_selector.date()
        self._day_start, self._day_end = day_bounds(date.year(), date.month(), date.day())
        stream = self.stream_selector.currentText()
        self.playhead_time = None

        try:
            segments = self._index.all(stream)
        except sqlite3.Error as error:
            self._segments = []
            self.coverage = []
            self.bar.set_bars([], None)
            self._report_unreadable(error)
            return

        self._segments = [
            s for s in segments if s.end > self._day_start and s.start < self._day_end
        ]
        self.coverage = coverage_bars(self._segments, self._day_start, self._day_end)
        self.bar.set_bars(self.coverage, None)

        day = date.toString("yyyy-MM-dd")
        if not self._segments:
            self._set_status(f"nothing was recorded on {stream} on {day}")
            return
        recorded = sum(
            min(s.end, self._day_end) - max(s.start, self._day_start)
            for s in self._segments
        )
        self._set_status(
            f"{day}: {len(self._segments)} segments, "
            f"{_duration(recorded)} recorded. Click the bar to play from a time."
        )

    # ---------------------------------------------------------------- the click

    def click_at(self, fraction: float) -> None:
        """Play whatever covers this fraction of the day, or say what does not."""
        when = time_at(fraction, self._day_start, self._day_end)
        self.playhead_time = when
        clock = datetime.datetime.fromtimestamp(when).strftime("%H:%M:%S")

        target = seek_target(self._segments, when)
        if target is None:
            # Say the time that was asked about, and leave the picture alone.
            # Playing the nearest file instead would show the operator footage
            # from a different moment while the clock claims otherwise.
            self.bar.set_playhead(None)
            self._set_status(f"no recording at {clock}")
            return

        self.bar.set_playhead(min(max(fraction, 0.0), 1.0))
        self.seek_offset = target.offset_seconds
        path = Path(target.path)
        try:
            url = path.as_uri()
        except ValueError:
            # as_uri refuses a relative path; the index should never hold one,
            # but a playable guess beats an exception in front of an operator.
            url = path.resolve().as_uri()
        self._pane.show(url)
        logger.info("playing %s from %.1f s in", path.name, target.offset_seconds)
        self._set_status(
            f"{clock} - {path.name}, {_duration(target.offset_seconds)} in"
        )

    # --------------------------------------------------------------- the words

    def _set_status(self, text: str) -> None:
        self.status_text = text
        self._status.setText(text)

    def _report_unreadable(self, error: Exception) -> None:
        """A catalogue that cannot be read is a message, not a traceback.

        The console goes on running: the recorder and the live pictures do not
        depend on this file, and an operator who loses playback must not lose
        the camera with it.
        """
        logger.error("the segment index could not be read: %s", error)
        self._set_status(f"the segment index could not be read: {error}")


def _duration(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
