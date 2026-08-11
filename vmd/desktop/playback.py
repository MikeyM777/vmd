"""Looking back through what was recorded.

Everything drawn here is something the catalogue of recordings can prove: each
bar is one file, at the start and end recorded when that file was closed. The
browser version drew an unbroken day whether or not anything had been written,
so a gap - the one thing an operator most needs to see - looked exactly like an
hour of footage. Nothing is drawn here that is not in the catalogue, and a day
with no recordings says so in words.

The other thing the browser version got wrong was the click. It measured the
pointer against the wrong element, so the time it scrubbed to was not the time
under the pointer. Here the bar measures the click against its own width and
nothing else, and that mapping is what the tests pin down.

**What this tab did not have.** For a long time it had a day picker, a stream
picker and click-to-seek, and no way to pause. Re-watching the same ten seconds
- the single most common thing anyone does with security footage - cost a fresh
click on a bar where one pixel is 72 seconds of the day. What is here now, in
the operator's own order:

* a row of buttons: play and pause, a second either way, ten seconds either way,
  a minute either way, and a speed from a quarter to eight times. `space` and
  the arrow keys do the same things, and they are an addition rather than a
  substitute: *"i rather buttons, space and arrows are nice but i need also
  buttons"*;
* a real calendar to pick the day, previous and next day beside it, and the
  moment being watched written out where he can read it from where he sits.
  Days with something on them are drawn differently from days with nothing;
* three zooms - whole day, an hour, ten minutes - with the bar pannable and the
  playhead left on the moment it was already on;
* the playhead can be dragged, with the time under it shown while it moves;
* thermal, visible, or both together on one timeline;
* a piece of the day can be marked and written to a folder he chooses;
* and a gap says why it is a gap, out of what can actually be shown.

**What is deliberately not here** is a list of events down the side. He was
asked and he does not want one; the movement marks on the bar are the list.
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QDate, QObject, QPoint, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QTextCharFormat
from PySide6.QtWidgets import (
    QCalendarWidget,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.export import (
    ExportOutcome,
    export_clip,
    suggested_name,
    unique_path,
)
from vmd.desktop.live import WrappedNote
from vmd.desktop.style import (
    MONO,
    PALETTE,
    SIZE_BAND,
    SIZE_HEADING,
    SPACE_SNUG,
    SPACE_STEP,
    WEIGHT_VALUE,
)
from vmd.desktop.timeline import (
    ZOOM_ORDER,
    ZOOM_SPANS,
    WHOLE_DAY,
    bring_into_view,
    clip_plan,
    coverage_bars,
    day_bounds,
    explain_gap,
    pan_window,
    seek_target,
    time_at,
    zoom_window,
)
from vmd.desktop.transport import TransportBar
from vmd.desktop.video import VideoPane
from vmd.storage.index import Segment, SegmentIndex

logger = logging.getLogger(__name__)

# How tall the day is drawn. Taller than it was, because it is the control this
# tab is aimed at with a mouse: a 34 px strip at the bottom of the window read
# as a divider rather than as the thing you click.
BAR_HEIGHT = 60
PLAYHEAD_WIDTH = 3

# Where the hour rules go, and how much of the bar they cross. A day drawn as
# an unbroken strip is a strip: nothing on it says which end is morning, so a
# gap in the coverage cannot be turned into a time without counting pixels.
#
# How often a rule is drawn depends on how much of the day is on screen: three
# hours apart across a whole day, and a marker a minute across ten minutes of
# it. A bar zoomed into ten minutes with a rule every three hours has no rules
# on it at all, which is the unmarked strip again.
TICK_HEIGHT = 8
TICK_STEPS = (
    # (at least this many seconds in view, seconds between rules)
    (12 * 3600, 3 * 3600),
    (4 * 3600, 3600),
    (3600, 600),
    (600, 120),
    (0, 60),
)

# What the tab says before anything has been recorded at all.
NOTHING_RECORDED = "Nothing has been recorded yet."

# The entry in the camera list that means both at once. Offered only when there
# are exactly two: with three, "both" does not say which two.
BOTH = "Both together"

# A movement mark, and how close a click has to be to mean it rather than the
# time under the pointer.
#
# This has been wrong in both directions, and the honest answer is neither.
#
# It was six PIXELS. At 1000 px wide one pixel is 86.4 s, so six of them
# silently redirected a click to an event up to 518 s - eight and a half
# minutes - away; on a real day with 113 marks there was no clickable moment
# more than 2.8 s from a mark and plain time-seeking became impossible.
#
# The correction made it thirty SECONDS, which went one step too far the other
# way. Thirty seconds on a 1200 px bar is 0.42 of a pixel, so the mark the
# operator can see - drawn three pixels wide - had 0.83 px of it that could
# actually be hit. He aimed at the red line, missed by a pixel, and was silently
# given footage from up to a minute away with a status line that read as though
# it had worked.
#
# The rule that is true at every width: **the target is at least as big as the
# thing drawn.** A mark is `MARK_WIDTH` pixels wide, centred on the moment, so
# the pointer is on red anywhere within half of that plus the pixel it is
# standing in - `MARK_CLICK_PIXELS` either side. Converted to seconds against
# the bar as it is drawn now, that is a constant four pixels of target for three
# pixels of mark at every width, from a laptop panel to a 4K screen.
#
# It does not swallow deliberate seeks, and the ratio is what says so: the
# clickable window is a third wider than the red the operator is aiming at, not
# eight and a half minutes wider. On the 113-mark day at 1200 px, 452 px of the
# bar mean a mark and 748 px mean the time - and 339 of those 452 are drawn red.
#
# The floor stays. On a bar wide enough that one pixel is under thirty seconds
# the drawn width stops being the binding constraint, and below half a minute
# the pointer is asking for a precision the day bar was never offering.
#
# Zooming changes what a pixel is worth and nothing else here: the arithmetic is
# against the window on screen, so at ten minutes across a 1200 px bar a pixel is
# half a second and the floor is what binds.
MARK_WIDTH = 3
MARK_CLICK_PIXELS = MARK_WIDTH / 2 + 0.5
MARK_TOLERANCE_SECONDS = 30.0

# How far before the movement playback starts. An event that begins on the
# first frame you see is one you have already missed: the approach is the part
# worth watching.
EVENT_LEAD_SECONDS = 5.0

# How often the playhead is moved to follow the picture.
#
# This is the ONE timer on this tab, it lives here rather than in the video pane,
# and it does not intervene in anything: it reads where the player is and moves a
# line. The pane's own rule is that it watches and never acts, because every
# disconnection reported from the field traced back to a timer inside it firing
# early, and nothing here is allowed to put one back. What this does do is open
# the next recording when the current one runs out, which is not recovery - it is
# what playing an archive written in five-minute files means.
FOLLOW_MS = 250

# How far apart two pictures have to be before the difference is worth a word.
#
# Two players are opened at the same moment and then run on their own; nothing
# keeps them locked. Under half a second is closer than an operator can see and
# saying it would be noise. Past it, the number is on the screen rather than
# hidden, because the whole value of two cameras is that they are showing the
# same second.
DRIFT_WORTH_SAYING = 0.5

# How far a pan button moves the window: a third of what is on screen, so
# something the operator was looking at is still on the bar afterwards.
PAN_FRACTION = 1 / 3

# The ceiling on `save_clip_now(wait=True)`, which is the path the tests take.
#
# Deliberately shorter than this suite's own 30 s per-test limit, and nothing to
# do with how long a clip may take: the console never waits, so this number can
# only ever be reached by a test whose export has wedged - and a test may fail,
# but a test may not hang. See tests/conftest.py.
WAITED_SAVE_MS = 20_000


class TimelineBar(QWidget):
    """The window on screen, drawn: recorded spans filled, gaps left as the well.

    A click is turned into a fraction of this widget's own width. That is the
    whole contract, and it is measured against `self.width()` rather than any
    parent or event offset - the browser version used an offset relative to
    another element and scrubbed to a time nobody asked for.

    The fraction is of the WINDOW, not of the day. Zooming moves two numbers on
    the tab and this class does not know it happened.
    """

    def __init__(self, tab: "PlaybackTab", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tab = tab
        self._lanes: list[tuple[str, list[tuple[float, float]]]] = []
        self._marks: list[float] = []
        self._playhead: float | None = None
        self._marked: tuple[float, float] | None = None
        self._ticks: list[tuple[float, str]] = []
        self._pressed = False
        self.setMinimumHeight(BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # So the time under the pointer can be shown without a button held down.
        # One pixel of a whole day is over a minute; without this he aims blind.
        self.setMouseTracking(True)

    def set_bars(
        self, bars: list[tuple[float, float]], playhead: float | None = None
    ) -> None:
        self.set_lanes([("", bars)], playhead)

    def set_lanes(
        self,
        lanes: list[tuple[str, list[tuple[float, float]]]],
        playhead: float | None = None,
    ) -> None:
        """One row per camera being shown, so "both" can be read at a glance.

        With two cameras on one timeline the question that matters is which of
        them has this minute, and two stacked rows answer it without a word.
        """
        self._lanes = [(name, list(bars)) for name, bars in lanes]
        self._playhead = playhead
        self.update()

    def set_marks(self, marks: list[float]) -> None:
        self._marks = list(marks)
        self.update()

    def set_playhead(self, playhead: float | None) -> None:
        self._playhead = playhead
        self.update()

    def set_marked_range(self, marked: tuple[float, float] | None) -> None:
        """The piece he has marked to save, as fractions of the window."""
        self._marked = marked
        self.update()

    def set_ticks(self, ticks: list[tuple[float, str]]) -> None:
        """(fraction, label) for every hour rule on the window being drawn."""
        self._ticks = list(ticks)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        width = self.width()
        height = self.height()
        painter.fillRect(0, 0, width, height, QColor(PALETTE["well"]))
        # The rules, under everything. Without them the window is an unmarked
        # strip and a gap in it cannot be read as a time without counting pixels.
        rules = QColor(PALETTE["line"])
        painter.setPen(rules)
        font = painter.font()
        font.setPixelSize(SIZE_HEADING)
        painter.setFont(font)
        for fraction, label in self._ticks:
            x = int(round(fraction * width))
            painter.fillRect(x, height - TICK_HEIGHT, 1, TICK_HEIGHT, rules)
            if label:
                painter.drawText(x + 3, height - TICK_HEIGHT - 2, label)

        top = 0
        room = max(height - TICK_HEIGHT - SIZE_HEADING - 4, 1)
        lanes = self._lanes or [("", [])]
        lane_height = max(room // len(lanes), 1)
        recorded = QColor(PALETTE["ok"])
        for index, (name, bars) in enumerate(lanes):
            lane_top = top + index * lane_height
            for left, span in bars:
                # Both edges rounded to the same grid, so two recordings that
                # meet meet on the bar as well. Rounding the WIDTH instead left
                # a black pixel between them, and a day of five-minute files
                # came out as a comb of 288 hairline gaps - a bar claiming a
                # dropout every five minutes on a camera that never stopped.
                x = int(round(left * width))
                right = int(round((left + span) * width))
                # At least one pixel: a recording shorter than a pixel of the
                # window is still a recording, and drawing nothing would claim
                # it is a gap.
                w = max(1, right - x)
                painter.fillRect(x, lane_top, min(w, width - x), lane_height - 1, recorded)
            if name and len(lanes) > 1:
                painter.setPen(QColor(PALETTE["muted"]))
                painter.drawText(4, lane_top + SIZE_HEADING, name)

        # The piece marked to be saved, under the marks and the playhead: it is
        # a region rather than a moment, and it must not hide either of them.
        if self._marked is not None:
            left, right = self._marked
            x = int(round(left * width))
            w = max(2, int(round((right - left) * width)))
            marked = QColor(PALETTE["accent"])
            marked.setAlpha(70)
            painter.fillRect(x, 0, min(w, width - x), room, marked)

        # Over the coverage, under the playhead: a mark says something happened
        # there, and the playhead says where the operator is looking now. Drawn
        # down to the rules rather than through them, so the hour numerals stay
        # readable on a day with a hundred marks on it.
        movement = QColor(PALETTE["alarm"])
        for fraction in self._marks:
            x = int(round(fraction * width)) - MARK_WIDTH // 2
            x = min(max(x, 0), max(width - MARK_WIDTH, 0))
            painter.fillRect(x, 0, MARK_WIDTH, room, movement)
        if self._playhead is not None:
            x = int(round(self._playhead * width)) - PLAYHEAD_WIDTH // 2
            x = min(max(x, 0), max(width - PLAYHEAD_WIDTH, 0))
            painter.fillRect(x, 0, PLAYHEAD_WIDTH, height, QColor(PALETTE["accent"]))
        painter.end()

    # ------------------------------------------------------------ the pointer

    def _fraction(self, position: QPoint | float) -> float:
        width = max(self.width(), 1)
        x = position if isinstance(position, (int, float)) else position.x()
        return min(max(float(x) / width, 0.0), 1.0)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt's name
        width = max(self.width(), 1)
        fraction = self._fraction(event.position().x())
        self._pressed = True
        # Seeking on the press rather than on the release, so a plain click is
        # answered the instant it happens. The width goes with the fraction
        # because the tolerance around a movement mark is in pixels, and only
        # this widget knows how many pixels the window is drawn in.
        self._tab.click_at(fraction, width=width)
        self._tab.begin_drag(fraction)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt's name
        fraction = self._fraction(event.position().x())
        if self._pressed:
            # Moved, not sought. A drag across an hour is hundreds of moves, and
            # a seek on each of them is a player asked to open a file five
            # hundred times.
            self._tab.drag_to(fraction)
        else:
            self._tab.hover_at(fraction)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt's name
        if self._pressed:
            self._pressed = False
            self._tab.end_drag(self._fraction(event.position().x()))
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self._tab.hover_at(None)
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """The wheel zooms on the moment under the pointer.

        An addition to the buttons and never instead of them - the buttons are
        the requirement. It is here because a hand on a mouse over a timeline
        reaches for the wheel without being told to.
        """
        steps = event.angleDelta().y()
        if steps:
            self._tab.zoom_towards(self._fraction(event.position().x()), closer=steps > 0)
        event.accept()


class DayPicker(QWidget):
    """The day, chosen off a month laid out rather than typed into a box.

    A date spin box is a control you can only use if you already know the date
    you want. A calendar is how anybody finds last Tuesday, and a calendar is
    what was asked for.

    `date`, `setDate` and `dateChanged` keep the shape a date field has, because
    everything that drives this tab from outside - being taken to a movement
    from the alarm strip, most of all - speaks in dates and should not have to
    care which control is showing them.
    """

    dateChanged = Signal(QDate)  # noqa: N815 - the shape of a Qt date field

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._date = QDate.currentDate()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.button = QPushButton()
        self.button.setMinimumHeight(34)
        self.button.setMinimumWidth(210)
        self.button.setToolTip("Pick the day from a calendar")
        self.button.clicked.connect(self.open_calendar)
        row.addWidget(self.button)

        self._calendar = QCalendarWidget()
        self._calendar.setGridVisible(True)
        self._calendar.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        self._calendar.clicked.connect(self._chosen)
        self._calendar.selectionChanged.connect(self._selected)

        self._popup = QDialog(self)
        self._popup.setWindowFlag(Qt.WindowType.Popup)
        inside = QVBoxLayout(self._popup)
        inside.setContentsMargins(0, 0, 0, 0)
        inside.addWidget(self._calendar)

        self._loading = False
        self.setDate(self._date)

    def calendar(self) -> QCalendarWidget:
        return self._calendar

    def date(self) -> QDate:
        return QDate(self._date)

    def setDate(self, date: QDate) -> None:  # noqa: N802 - the shape of a date field
        if not date.isValid() or date == self._date:
            self._draw()
            return
        self._date = QDate(date)
        self._loading = True
        try:
            self._calendar.setSelectedDate(self._date)
        finally:
            self._loading = False
        self._draw()
        self.dateChanged.emit(self.date())

    def open_calendar(self) -> None:
        self._popup.move(self.button.mapToGlobal(QPoint(0, self.button.height())))
        self._popup.show()

    def _draw(self) -> None:
        self.button.setText(self._date.toString("dddd d MMMM yyyy") + "   ▾")

    def _chosen(self, date: QDate) -> None:
        self._popup.hide()
        self.setDate(date)

    def _selected(self) -> None:
        if self._loading:
            return
        self.setDate(self._calendar.selectedDate())


class _ExportSignals(QObject):
    done = Signal(object)


class _ExportJob(QRunnable):
    """One clip, written off the thread that draws the window.

    A clip of a night is minutes of copying on a laptop that is also recording
    two streams and watching for movement; run inline it is a console that has
    stopped answering with no sign of why. What it must not lose in moving is
    the answer, so `done` always fires and always carries an outcome - including
    the case where the export itself threw.
    """

    def __init__(self, work, signals: _ExportSignals) -> None:
        super().__init__()
        self._work = work
        self._signals = signals

        self.outcome = ExportOutcome(
            ok=False, path=None, message="The clip could not be saved."
        )

    def run(self) -> None:
        try:
            outcome = self._work()
        except Exception as error:  # noqa: BLE001 - a clip may not crash a console
            logger.exception("the clip could not be written")
            outcome = ExportOutcome(
                ok=False, path=None, message=f"The clip could not be saved: {error}"
            )
        self.outcome = outcome
        self._signals.done.emit(outcome)


class PlaybackTab(QWidget):
    """A day of recordings, and a player pointed into it."""

    def __init__(
        self,
        index: SegmentIndex,
        pane: VideoPane,
        events=None,
        second_pane: VideoPane | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._pane = pane
        # Anything with `between(start, end, stream)` - in the console an
        # EventStore over the same events.db the Live tab reads. None means no
        # detection to draw, which is a day with no marks and nothing else.
        self._events = events
        # A second picture of the same kind as the one handed over, for showing
        # thermal and visible together. Built from the pane's own class rather
        # than from a factory this tab is not given, so that the console needs no
        # rewiring and a test driving a fake gets a fake. If it cannot be built -
        # which on this machine means video is broken anyway - "both" is simply
        # not offered.
        self._second_pane = second_pane if second_pane is not None else _another(pane)

        today = datetime.date.today()
        self.day_start, self.day_end = day_bounds(today.year, today.month, today.day)
        # What the bar is showing, which is the whole day until it is zoomed.
        self.view_start, self.view_end = self.day_start, self.day_end
        self.zoom = WHOLE_DAY
        self._segments: list[Segment] = []
        self._second_segments: list[Segment] = []
        self.coverage: list[tuple[float, float]] = []
        # (fraction of the window, the event) for every mark on the bar.
        self.event_marks: list[tuple[float, object]] = []
        self.status_text = ""
        self.readout_text = ""
        self.hover_text = ""
        self.dragging = False
        self._drag_from = 0.0
        self.days_with_footage: set[QDate] = set()
        # How far into the file the last seek asked to start. It is handed to
        # the player now rather than only recorded: `VideoPane.show` takes the
        # position, and until it did, an operator who clicked 14:32 was given
        # the file containing 14:32 played from its beginning - up to five
        # minutes from the moment they asked about. For a system whose whole
        # purpose is "something happened, show me", that is not playback.
        self.seek_offset = 0.0
        self.playhead_time: float | None = None
        # Which file each picture has open, so a skip inside it can be a move
        # rather than an expensive reopen.
        self._showing: str | None = None
        self._second_showing: str | None = None
        self.clip_from: float | None = None
        self.clip_to: float | None = None
        self._saving = False
        # Injected so a test can watch what was asked for without a dialogue
        # opening on somebody's screen, and so the real ffmpeg run lives in one
        # place. Both are ordinary attributes on purpose: they are meant to be
        # replaced.
        self.ask_for_folder = self._ask_for_folder
        self.run_ffmpeg = subprocess.run
        self._now = time.time
        # Guards the controls while they are being set from code, so that
        # populating the stream list does not reload the day underneath itself.
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_STEP, SPACE_STEP, SPACE_STEP, SPACE_STEP)
        layout.setSpacing(SPACE_SNUG)

        layout.addLayout(self._build_day_row())
        layout.addLayout(self._build_readout_row())
        layout.addWidget(self._build_pictures(), 1)
        layout.addLayout(self._build_transport_row())
        self.bar = TimelineBar(self)
        layout.addWidget(self.bar)

        # A WrappedNote, not a word-wrapped QLabel: this line carries "nothing
        # at 14:32 - nothing was recorded on thermal between 14:05 and 14:40"
        # and "the movement there is no longer on disk", which are the two
        # answers an operator most needs to read whole.
        self._status = WrappedNote("")
        self._status.setStyleSheet(f"color: {PALETTE['muted']}; font-family: {MONO};")
        layout.addWidget(self._status)

        # The one timer here. See FOLLOW_MS.
        self._follow = QTimer(self)
        self._follow.setInterval(FOLLOW_MS)
        self._follow.timeout.connect(self._follow_the_picture)

        self._exports = QThreadPool(self)
        self._exports.setMaxThreadCount(1)
        self._export_signals: list[_ExportSignals] = []

        # So the space bar and the arrows reach this tab rather than whichever
        # button was last pressed.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        names = self.refresh_streams()
        # Draw the day now rather than waiting for the operator to touch a
        # control. Opening this tab used to show an empty bar over a day that
        # had been recorded all along, because nothing called `_reload` until
        # something changed - and "nothing was recorded" is precisely the
        # answer this tab must never give by accident.
        if names:
            self._reload()
        else:
            self._set_status(NOTHING_RECORDED)
        self._draw_controls()

    # ---------------------------------------------------------- the furniture

    def _build_day_row(self):
        row = QHBoxLayout()
        row.setSpacing(SPACE_SNUG)
        self.previous_day = QPushButton("◀  Day before")
        self.previous_day.setMinimumHeight(34)
        self.previous_day.setToolTip("Show the day before this one")
        self.previous_day.clicked.connect(lambda: self._step_day(-1))
        row.addWidget(self.previous_day)

        self.date_selector = DayPicker()
        self.date_selector.dateChanged.connect(self._controls_changed)
        row.addWidget(self.date_selector)

        self.next_day = QPushButton("Day after  ▶")
        self.next_day.setMinimumHeight(34)
        self.next_day.setToolTip("Show the day after this one")
        self.next_day.clicked.connect(lambda: self._step_day(1))
        row.addWidget(self.next_day)

        row.addSpacing(SPACE_STEP)
        camera = QLabel("Camera")
        row.addWidget(camera)
        self.stream_selector = QComboBox()
        self.stream_selector.setMinimumHeight(34)
        self.stream_selector.setMinimumWidth(150)
        self.stream_selector.setToolTip("Which camera to look back through")
        self.stream_selector.currentTextChanged.connect(self._controls_changed)
        row.addWidget(self.stream_selector)
        row.addStretch(1)
        return row

    def _build_readout_row(self):
        row = QHBoxLayout()
        row.setSpacing(SPACE_SNUG)
        self.readout = QLabel("")
        font = QFont(self.readout.font())
        font.setPixelSize(SIZE_BAND)
        font.setBold(True)
        self.readout.setFont(font)
        # The size is in the widget's own stylesheet as well as in its font,
        # and it has to be: the application stylesheet sets a font-size on
        # QWidget, and a stylesheet beats setFont. Without this line the one
        # thing on this tab that has to be readable from across the room was
        # drawn at the size of the smallest note on it.
        self.readout.setStyleSheet(
            f"color: {PALETTE['ink']}; font-family: {MONO}; "
            f"font-size: {SIZE_BAND}px; font-weight: {WEIGHT_VALUE};"
        )
        row.addWidget(self.readout)
        row.addStretch(1)

        self.zoom_buttons: dict[str, QPushButton] = {}
        for name in ZOOM_ORDER:
            button = QPushButton(name)
            button.setMinimumHeight(34)
            button.setMinimumWidth(78)
            button.setCheckable(True)
            # Which zoom is on has to be visible, and the application
            # stylesheet has no opinion about a checked button - so all three
            # were drawn identically and the one that was on was the one you
            # could work out by looking at the bar. The same fault the Logs
            # tab's filters have. Marked by the accent, which is what this
            # design reserves for the state of an active control.
            button.setStyleSheet(
                f"QPushButton:checked {{ background: {PALETTE['line']}; "
                f"border: 2px solid {PALETTE['accent']}; "
                f"font-weight: {WEIGHT_VALUE}; color: {PALETTE['ink']}; }}"
            )
            button.setToolTip(f"Show {name.lower()} on the bar below")
            button.clicked.connect(lambda _checked=False, z=name: self.set_zoom(z))
            self.zoom_buttons[name] = button
            row.addWidget(button)

        self.pan_earlier = QPushButton("◀")
        self.pan_earlier.setToolTip("Move the bar earlier")
        self.pan_later = QPushButton("▶")
        self.pan_later.setToolTip("Move the bar later")
        for button, direction in ((self.pan_earlier, -1.0), (self.pan_later, 1.0)):
            button.setMinimumHeight(34)
            button.setMinimumWidth(44)
            button.clicked.connect(lambda _checked=False, d=direction: self.pan(d))
            row.addWidget(button)
        return row

    def _build_pictures(self) -> QWidget:
        """The picture, or the two pictures, in the frame the Live tab uses.

        The same frame the Live tab gives its pictures, so footage looks like
        footage on both tabs rather than like a hole on one of them.
        """
        well = QFrame()
        well.setObjectName("videoFrame")
        well.setStyleSheet(
            f"QFrame#videoFrame {{ border: 1px solid {PALETTE['line_strong']}; "
            f"background: {PALETTE['well']}; }}"
        )
        inside = QVBoxLayout(well)
        inside.setContentsMargins(0, 0, 0, 0)
        self._wall = QSplitter(Qt.Orientation.Horizontal)
        self._wall.setChildrenCollapsible(False)
        for candidate in (self._pane, self._second_pane):
            if isinstance(candidate, QWidget):
                self._wall.addWidget(candidate)
        if isinstance(self._second_pane, QWidget):
            self._second_pane.setVisible(False)
        inside.addWidget(self._wall)
        return well

    def _build_transport_row(self):
        row = QHBoxLayout()
        row.setSpacing(SPACE_SNUG)
        self.transport = TransportBar()
        self.transport.play_pause.connect(self.toggle_play)
        self.transport.skipped.connect(self.skip)
        self.transport.speed_chosen.connect(self.set_speed)
        row.addWidget(self.transport)

        self.mark_start = QPushButton("Mark start")
        self.mark_start.setToolTip("Start of the piece to save")
        self.mark_start.clicked.connect(self.mark_the_start)
        self.mark_end = QPushButton("Mark end")
        self.mark_end.setToolTip("End of the piece to save")
        self.mark_end.clicked.connect(self.mark_the_end)
        self.save_clip = QPushButton("Save it…")
        self.save_clip.setToolTip("Write the marked piece to a folder you choose")
        self.save_clip.clicked.connect(lambda: self.save_clip_now())
        self.clear_marks = QPushButton("Clear")
        self.clear_marks.setToolTip("Forget the marks")
        self.clear_marks.clicked.connect(self.forget_the_marks)
        for button in (self.mark_start, self.mark_end, self.save_clip, self.clear_marks):
            button.setMinimumHeight(34)
            button.setMinimumWidth(60)
            row.addWidget(button)
        return row

    # ------------------------------------------------------------- the streams

    def refresh_streams(self) -> list[str]:
        """The cameras the catalogue has recordings for, sorted.

        Not the cameras in settings: one taken out of the configuration still
        has hours on disk, and the question this tab answers is what is on disk,
        not what is currently being recorded.
        """
        try:
            names = self._names_on_disk()
        except sqlite3.Error as error:
            self._report_unreadable(error)
            return []
        chosen = self.stream_selector.currentText()
        self._loading = True
        try:
            self.stream_selector.clear()
            self.stream_selector.addItems(names)
            # Only with exactly two. With three, "both" does not say which two.
            if len(names) == 2 and self._second_pane is not None:
                self.stream_selector.addItem(BOTH)
            if chosen and self.stream_selector.findText(chosen) >= 0:
                self.stream_selector.setCurrentText(chosen)
        finally:
            self._loading = False
        return names

    def _names_on_disk(self) -> list[str]:
        """The camera names, asked for as names rather than counted by hand.

        `streams()` is one DISTINCT out of SQLite. Collecting them from every
        row was seven and a half seconds of the drawing thread on ninety days of
        two cameras, at start-up, before the console had shown anything.
        """
        streams = getattr(self._index, "streams", None)
        if streams is not None:
            return list(streams())
        return sorted({segment.stream for segment in self._index.all()})

    def stream_names(self) -> list[str]:
        """The cameras, without the entry that means more than one of them."""
        return [
            self.stream_selector.itemText(i)
            for i in range(self.stream_selector.count())
            if self.stream_selector.itemText(i) != BOTH
        ]

    def shown_streams(self) -> list[str]:
        """Which cameras the bar and the pictures are answering about."""
        chosen = self.stream_selector.currentText()
        if chosen == BOTH:
            return self.stream_names()[:2]
        return [chosen] if chosen else []

    # ----------------------------------------------------------------- the day

    def show_day(self, year: int, month: int, day: int, stream: str) -> None:
        """Draw this local day for this camera."""
        # Re-read the list first: recording has been going on since the tab was
        # built, and a camera that started since then is on disk now.
        self.refresh_streams()
        self._loading = True
        try:
            self.date_selector.setDate(QDate(year, month, day))
            if self.stream_selector.findText(stream) < 0:
                # Asked for a camera the catalogue has never heard of - offer it
                # anyway, and let the empty day say so.
                self.stream_selector.addItem(stream)
            self.stream_selector.setCurrentText(stream)
        finally:
            self._loading = False
        self._reload()

    def _step_day(self, days: int) -> None:
        self.date_selector.setDate(self.date_selector.date().addDays(days))

    def _controls_changed(self, *_args) -> None:
        if self._loading:
            return
        self._reload()

    def _reload(self) -> None:
        date = self.date_selector.date()
        self.day_start, self.day_end = day_bounds(date.year(), date.month(), date.day())
        self.playhead_time = None
        self._stop_second_picture()
        self._showing = None
        self.forget_the_marks()

        shown = self.shown_streams()
        try:
            self._segments = self._day_of(shown[0]) if shown else []
            self._second_segments = self._day_of(shown[1]) if len(shown) > 1 else []
            self._mark_the_calendar(shown[0] if shown else "")
        except sqlite3.Error as error:
            self._segments = []
            self._second_segments = []
            self.coverage = []
            self.event_marks = []
            self.bar.set_bars([], None)
            self.bar.set_marks([])
            self._report_unreadable(error)
            self._draw_controls()
            return

        # A day is drawn whole when it is opened. Zooming is something the
        # operator does to a day he is already looking at.
        self.zoom = WHOLE_DAY
        self.view_start, self.view_end = self.day_start, self.day_end
        self._redraw_window()
        self._draw_readout()

        day = date.toString("d MMMM yyyy")
        name = " and ".join(shown) if shown else ""
        if not self._segments and not self._second_segments:
            self._set_status(f"Nothing was recorded on {name or 'any camera'} on {day}.")
            self._draw_controls()
            return
        recorded = sum(
            min(s.end, self.day_end) - max(s.start, self.day_start)
            for s in self._segments
        )
        self._set_status(
            f"{_duration(recorded)} recorded on {shown[0]} on {day}. "
            "Click the bar to play from a time, or drag the line along it."
        )
        self._draw_controls()

    def _day_of(self, stream: str) -> list[Segment]:
        """This day's recordings for one camera, asked for as a day.

        `between` rather than everything the camera has: a month of five-minute
        recordings on two cameras is around 17,000 rows, and this runs on the
        thread that draws the window every time the day or the camera changes.
        """
        between = getattr(self._index, "between", None)
        if between is not None:
            return between(stream, self.day_start, self.day_end)
        segments = self._index.all(stream)
        return [s for s in segments if s.end > self.day_start and s.start < self.day_end]

    # ------------------------------------------------------------- the window

    def set_zoom(self, zoom: str) -> None:
        """Show this much of the day, around wherever he is looking now."""
        self.zoom = zoom if zoom in ZOOM_SPANS else WHOLE_DAY
        centre = self.playhead_time
        if centre is None:
            centre = (self.view_start + self.view_end) / 2.0
        self.view_start, self.view_end = zoom_window(
            self.zoom, centre, self.day_start, self.day_end
        )
        self._redraw_window()
        self._draw_controls()

    def zoom_towards(self, fraction: float, closer: bool) -> None:
        """One step in or out, keeping the moment under the pointer.

        The wheel's version of the buttons. Which zoom it lands on is the next
        one along the same three the buttons offer, so the two controls can
        never disagree about what is on screen.
        """
        order = list(ZOOM_ORDER)
        at = order.index(self.zoom) if self.zoom in order else 0
        wanted = at + (1 if closer else -1)
        if not 0 <= wanted < len(order):
            return
        under = time_at(fraction, self.view_start, self.view_end)
        self.zoom = order[wanted]
        self.view_start, self.view_end = zoom_window(
            self.zoom, under, self.day_start, self.day_end
        )
        self._redraw_window()
        self._draw_controls()

    def pan(self, direction: float) -> None:
        """Move the window along the day by a third of what is on screen."""
        step = (self.view_end - self.view_start) * PAN_FRACTION * direction
        self.view_start, self.view_end = pan_window(
            self.view_start, self.view_end, step, self.day_start, self.day_end
        )
        self._redraw_window()

    def _keep_in_view(self, when: float) -> None:
        self.view_start, self.view_end = bring_into_view(
            self.view_start, self.view_end, when, self.day_start, self.day_end
        )

    def _redraw_window(self) -> None:
        """Everything the bar draws, worked out against the window on screen."""
        lanes: list[tuple[str, list[tuple[float, float]]]] = []
        shown = self.shown_streams()
        self.coverage = coverage_bars(self._segments, self.view_start, self.view_end)
        lanes.append((shown[0] if shown else "", self.coverage))
        if len(shown) > 1:
            lanes.append(
                (
                    shown[1],
                    coverage_bars(self._second_segments, self.view_start, self.view_end),
                )
            )
        self.bar.set_lanes(lanes, self._playhead_fraction())
        self.bar.set_ticks(self._ticks())
        self._load_marks(shown[0] if shown else "")
        self._draw_marked_range()

    def _ticks(self) -> list[tuple[float, str]]:
        """The rules on the window, spaced for how much of the day is on it."""
        span = max(self.view_end - self.view_start, 1.0)
        step = next(gap for floor, gap in TICK_STEPS if span >= floor)
        ticks: list[tuple[float, str]] = []
        first = datetime.datetime.fromtimestamp(self.view_start)
        at = datetime.datetime(first.year, first.month, first.day).timestamp()
        while at < self.view_end:
            if at >= self.view_start:
                moment = datetime.datetime.fromtimestamp(at)
                label = moment.strftime("%H:%M" if step < 3600 else "%H")
                ticks.append(((at - self.view_start) / span, label))
            at += step
        return ticks

    def _playhead_fraction(self) -> float | None:
        if self.playhead_time is None:
            return None
        span = max(self.view_end - self.view_start, 1.0)
        fraction = (self.playhead_time - self.view_start) / span
        return fraction if 0.0 <= fraction <= 1.0 else None

    # --------------------------------------------------------- what moved, drawn

    def _load_marks(self, stream: str) -> None:
        """The movement inside the window being shown, as fractions of it.

        Filtered against the window here as well as in the query. A mark drawn
        outside the bar is clamped to its edge, and a mark at the edge of the
        bar would claim movement at a time it did not happen.

        A store that cannot be read costs the marks and nothing else: the
        coverage comes from the catalogue of recordings, and an operator who
        loses the movement marks must not lose the footage they were drawn over.
        """
        self.event_marks = []
        if self._events is not None and stream:
            span = max(self.view_end - self.view_start, 1.0)
            try:
                events = self._events.between(self.view_start, self.view_end, stream)
            except Exception as error:  # noqa: BLE001 - the footage is not downstream of this
                logger.warning("the movement events could not be read: %s", error)
                events = []
            for event in events:
                if not self.view_start <= event.started < self.view_end:
                    continue
                self.event_marks.append(((event.started - self.view_start) / span, event))
        self.bar.set_marks([fraction for fraction, _ in self.event_marks])

    def mark_tolerance_seconds(self, width: int) -> float:
        """How far from a mark a click still means that mark, on a bar this wide.

        A duration, because the window is a duration - but one that knows how
        the bar is drawn, because what the operator aims at is the red he can
        see and that is measured in pixels. See `MARK_CLICK_PIXELS`.
        """
        seconds_per_pixel = (self.view_end - self.view_start) / max(width, 1)
        return max(MARK_TOLERANCE_SECONDS, seconds_per_pixel * MARK_CLICK_PIXELS)

    def _mark_near(self, fraction: float, width: int) -> object | None:
        """The movement mark this click meant, if it meant one.

        Nearest wins, so two events a minute apart stay separately clickable.
        """
        if not self.event_marks:
            return None
        when = time_at(fraction, self.view_start, self.view_end)
        nearest = min(self.event_marks, key=lambda mark: abs(mark[1].started - when))
        if abs(nearest[1].started - when) > self.mark_tolerance_seconds(width):
            return None
        return nearest[1]

    # ---------------------------------------------------------------- the click

    def click_at(self, fraction: float, width: int | None = None) -> None:
        """Play whatever covers this fraction of the window, or say what does not.

        A click on - or within a pixel of - a movement mark means the mark, and
        plays from five seconds before it. The alternative, the exact time under
        the pointer, is a time nobody can aim at at whole-day zoom: one pixel of
        the bar is over a minute. See `MARK_CLICK_PIXELS` for why the tolerance
        has to know how wide the bar is.
        """
        width = self.bar.width() if width is None else width
        event = self._mark_near(fraction, width)
        if event is not None:
            self._play_at(event.started, event=event)
            return
        self._play_at(time_at(fraction, self.view_start, self.view_end))

    # --------------------------------------------------------------- dragging

    def begin_drag(self, fraction: float) -> None:
        self.dragging = True
        self._drag_from = fraction
        self.hover_at(fraction)

    def drag_to(self, fraction: float) -> None:
        """Move the line and say the time, and do not touch the player.

        A drag across an hour is hundreds of moves. Seeking on each of them is a
        player asked to open a file five hundred times, which on this laptop is
        a console that stops answering half way through the gesture.
        """
        if not self.dragging:
            return
        self.bar.set_playhead(min(max(fraction, 0.0), 1.0))
        self.hover_at(fraction)

    def end_drag(self, fraction: float) -> None:
        """Land the playhead where it was let go, if it went anywhere.

        A plain click is a press and a release at the same place, and the press
        has already answered it - including the case where it landed on a
        movement mark and is owed the five seconds before it. Seeking again on
        the release would throw that away and take him to the bare time under
        the pointer instead.
        """
        if not self.dragging:
            return
        self.dragging = False
        moved = abs(fraction - self._drag_from) > 1e-9
        self.hover_at(None)
        if moved:
            self.click_at(fraction, width=self.bar.width())
        else:
            self.bar.set_playhead(self._playhead_fraction())

    def hover_at(self, fraction: float | None) -> None:
        """The time under the pointer, in words. None when it has left the bar."""
        if fraction is None:
            self.hover_text = ""
        else:
            when = time_at(fraction, self.view_start, self.view_end)
            self.hover_text = datetime.datetime.fromtimestamp(when).strftime("%H:%M:%S")
        self._draw_readout()

    # ------------------------------------------------------------- the playing

    def show_event(self, event) -> bool:
        """Show me that movement: its day, its camera, and the moment itself.

        The one call behind both `Show me` on the alarm strip and a double click
        in the movement list. It is deliberately the same path the timeline's own
        marks already take - `_play_at` with the event - so that being taken to a
        movement and clicking its mark cannot ever mean two different things,
        including the five-second lead and the answer when there is no footage.

        Returns whether there was anything to play. False is not a failure: an
        event can predate recording, can be on a camera nothing was recording,
        and can have had its footage reclaimed by retention months ago. Every one
        of those is answered in the line under the bar, because the operator who
        pressed that button is owed a sentence, not an empty day.

        Never raises. It is called from a button press during an alarm, and a
        traceback at that moment costs him the console as well as the footage.
        """
        try:
            moment = datetime.datetime.fromtimestamp(event.started)
            self.show_day(moment.year, moment.month, moment.day, event.stream)
            return self._play_at(event.started, event=event)
        except Exception as error:  # noqa: BLE001 - a button press may not throw
            logger.exception("that movement could not be opened")
            self._set_status(f"That movement could not be opened: {error}")
            return False

    def play_at_time(self, when: float) -> bool:
        """Play this moment. The one door everything that moves the clock uses."""
        return self._play_at(when)

    def _play_at(self, when: float, event=None) -> bool:
        """Open the file covering this moment, at this moment inside it.

        Answers whether anything is playing, for the caller that has just taken
        the operator to another tab to see it. `click_at` ignores it: he is
        already looking at the bar he clicked, and the line under it has the
        answer either way.

        For a movement mark the lead is taken off HERE rather than off the time
        that was asked for, and that is the difference between a mark that
        plays and one that says there is no recording. An event two seconds
        into a recording is five seconds after a moment that belongs to the
        previous file, or to a gap - and the answer to "show me this movement"
        can never be "there is nothing there". So the file is found from the
        event's own time and the lead is taken off inside it, clamped at the
        start of the file, with the sentence saying the lead it really got.
        """
        lead = 0.0
        if event is not None:
            found = seek_target(self._segments, when)
            # However much of the five seconds fits inside this file. Nothing,
            # if the movement began on its first frame.
            lead = min(EVENT_LEAD_SECONDS, found.offset_seconds) if found else 0.0
            when -= lead
        when = min(max(when, self.day_start), self.day_end)
        self.playhead_time = when
        self._keep_in_view(when)
        clock = datetime.datetime.fromtimestamp(when).strftime("%H:%M:%S")

        target = seek_target(self._segments, when)
        if target is None:
            # Say the time that was asked about, say what is knowable about why
            # there is nothing there, and leave the picture alone. Playing the
            # nearest file instead would show the operator footage from a
            # different moment while the clock claims otherwise. A mark whose
            # footage retention has already reclaimed is answered the same way:
            # the movement was real, and there is nothing left to show.
            self.bar.set_playhead(None)
            self._stop_second_picture()
            note = self._why_nothing(when)
            if event is not None:
                note += f" The movement on {event.stream} there is no longer on disk."
            self._set_status(note)
            self._draw_readout()
            self._draw_controls()
            return False

        self.bar.set_playhead(self._playhead_fraction())
        self.seek_offset = target.offset_seconds
        self._point(self._pane, target.path, target.offset_seconds, first=True)
        logger.info("playing %s from %.1f s in", Path(target.path).name, target.offset_seconds)

        # The name of the recording is on the end of both sentences and not in
        # the middle of either: it is the only place on this tab that says which
        # file on disk this is, which is what he needs if he ever wants to take
        # a copy of it by hand - and it is the last thing he cares about while
        # he is watching.
        name = Path(target.path).name
        if event is not None:
            # The lead it really got, not the one it asked for. A movement two
            # seconds into a file is played from that file's first frame, and
            # saying "5s before" about it would be the console rounding a
            # measurement up in front of somebody making a decision from it.
            note = (
                f"{clock} - {_duration(lead)} before the movement on "
                f"{event.stream}, {name}"
            )
        else:
            note = (
                f"Playing {self.shown_streams()[0]} from {clock} - "
                f"{name}, {_duration(target.offset_seconds)} in"
            )
        note += self._point_the_second_picture(when)
        self._set_status(note)
        self._draw_readout()
        self._draw_controls()
        self._follow_while_playing()
        return True

    def _point(self, pane, path: str, offset: float, first: bool) -> None:
        """Put a picture on this file at this moment, reopening only if it must.

        A skip of ten seconds inside a file the player already has open is a
        move, not a reopen: reopening is a black frame and a wait for something
        the player can already do. Reopening is what happens when the file
        changes, or when the player will not move.
        """
        showing = self._showing if first else self._second_showing
        offset = max(offset, 0.0)
        if showing == path:
            move = getattr(pane, "seek_seconds", None)
            if move is not None and move(offset):
                if first:
                    self.seek_offset = offset
                return
        url = _as_url(path)
        pane.show(url, at_seconds=offset)
        if first:
            self._showing = path
            self.seek_offset = offset
        else:
            self._second_showing = path
        # Whatever speed he was watching at survives being taken somewhere else,
        # and a pane that has just been handed new media is running.
        _ask(pane, "set_rate", self.transport.speed())
        _ask(pane, "set_paused", False)

    def _point_the_second_picture(self, when: float) -> str:
        """The other camera at the same moment, and what to say if it has none.

        **How honest this is.** The two pictures are two independent libVLC
        players. Both are opened at the same wall-clock moment and both are
        given the same pause, speed and skip, so they start together and are
        told to do the same things afterwards - but nothing keeps them locked
        frame to frame, and libVLC gives no way to make it. So the difference
        between them is measured and put on the screen (see `drift_seconds`)
        rather than being hidden behind a claim of synchronisation that would
        not survive an hour.
        """
        shown = self.shown_streams()
        if len(shown) < 2 or self._second_pane is None:
            return ""
        target = seek_target(self._second_segments, when)
        if target is None:
            # A still of the wrong minute beside a live picture is the console
            # inventing footage. The picture goes, and the sentence says which
            # camera it was.
            self._stop_second_picture()
            clock = datetime.datetime.fromtimestamp(when).strftime("%H:%M:%S")
            return f" There is nothing recorded on {shown[1]} at {clock}."
        if isinstance(self._second_pane, QWidget):
            self._second_pane.setVisible(True)
        self._point(self._second_pane, target.path, target.offset_seconds, first=False)
        return ""

    def _stop_second_picture(self) -> None:
        if self._second_pane is None:
            return
        self._second_showing = None
        try:
            self._second_pane.stop()
        except Exception:  # noqa: BLE001 - a picture going away may not throw
            logger.debug("the second picture would not stop", exc_info=True)
        if isinstance(self._second_pane, QWidget):
            self._second_pane.setVisible(False)

    @property
    def second_pane(self):
        return self._second_pane

    def drift_seconds(self) -> float | None:
        """How far apart the two pictures are, or None when there is only one.

        Measured rather than assumed. Two players opened at the same moment run
        on their own afterwards, and a console that drew them side by side while
        claiming they were locked would be making the operator's judgement for
        him about the one thing two cameras are for.
        """
        shown = self.shown_streams()
        if len(shown) < 2 or self._second_pane is None:
            return None
        here = _ask(self._pane, "position_seconds")
        there = _ask(self._second_pane, "position_seconds")
        if here is None or there is None:
            return None
        return abs(float(here) - float(there))

    def _why_nothing(self, when: float) -> str:
        """The gap, explained out of what can actually be shown."""
        shown = self.shown_streams()
        stream = shown[0] if shown else ""
        archive = None
        bounds = getattr(self._index, "bounds", None)
        if bounds is not None and stream:
            try:
                archive = bounds(stream)
            except sqlite3.Error:
                logger.debug("the catalogue would not say how far back it goes", exc_info=True)
        said = explain_gap(
            when=when,
            segments=self._segments,
            stream=stream or "this camera",
            archive=archive,
            now=self._now(),
            recorder=None,
        )
        return said[0].upper() + said[1:] + "."

    # ------------------------------------------------------------ the transport

    def toggle_play(self) -> None:
        paused = bool(_ask(self._pane, "paused"))
        self.set_paused(not paused)

    def set_paused(self, paused: bool) -> None:
        for pane in self._pictures():
            _ask(pane, "set_paused", paused)
        self._draw_controls()
        self._follow_while_playing()

    def set_speed(self, speed: float) -> None:
        for pane in self._pictures():
            _ask(pane, "set_rate", speed)

    def skip(self, seconds: float) -> None:
        """Move by this many seconds of the day, not of the file.

        Of the DAY, because that is what the operator means and because five
        seconds forward from four seconds before the end of a recording is in
        the next one. The file is found again from the moment, which is the same
        path a click takes.
        """
        when = self.playhead_time
        if when is None:
            return
        self._play_at(when + float(seconds))

    def _pictures(self) -> list:
        shown = self.shown_streams()
        pictures = [self._pane]
        if len(shown) > 1 and self._second_pane is not None:
            pictures.append(self._second_pane)
        return pictures

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """The keys, which do exactly what the buttons beside them do.

        An addition and never a substitute: every one of these is a control on
        the screen as well, because the operator asked for buttons and because a
        console whose only pause is a keystroke is a console with no pause for
        anybody who was not told about it.
        """
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.toggle_play()
        elif key == Qt.Key.Key_Left:
            self.skip(-10.0)
        elif key == Qt.Key.Key_Right:
            self.skip(10.0)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    # --------------------------------------------------- following the picture

    def _follow_while_playing(self) -> None:
        running = self.playhead_time is not None and not bool(_ask(self._pane, "paused"))
        if running and self.isVisible():
            if not self._follow.isActive():
                self._follow.start()
        elif self._follow.isActive():
            self._follow.stop()

    def _follow_the_picture(self) -> None:
        """Move the line to where the picture actually is, and turn the page.

        Reads and draws. The one thing it changes is which file is open, and
        only when the current one has run out - an archive written in
        five-minute files is not playable at all without that, and it is
        playback rather than recovery.
        """
        if self._showing is None or self.playhead_time is None:
            return
        position = _ask(self._pane, "position_seconds")
        if position is None:
            return
        segment = next((s for s in self._segments if s.path == self._showing), None)
        if segment is None:
            return
        when = segment.start + float(position)
        if when >= segment.end - 0.05:
            # The end of this file. The next moment belongs to the next
            # recording, or to a gap, and _play_at answers both.
            self._play_at(segment.end + 0.05)
            return
        self.playhead_time = when
        self._keep_in_view(when)
        self.bar.set_playhead(self._playhead_fraction())
        self._draw_readout()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Nothing follows a picture behind a tab nobody is looking at."""
        self._follow.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._follow_while_playing()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._follow.stop()
        release = getattr(self._second_pane, "release", None)
        if release is not None:
            try:
                release()
            except Exception:  # noqa: BLE001 - closing must not fail a close
                logger.exception("the second picture would not be let go")
        super().closeEvent(event)

    # ---------------------------------------------------------- saving a clip

    def mark_the_start(self) -> None:
        self.clip_from = self.playhead_time
        self._draw_marked_range()
        self._draw_controls()

    def mark_the_end(self) -> None:
        self.clip_to = self.playhead_time
        self._draw_marked_range()
        self._draw_controls()

    def forget_the_marks(self) -> None:
        self.clip_from = None
        self.clip_to = None
        self._draw_marked_range()
        self._draw_controls()

    def _draw_marked_range(self) -> None:
        if self.clip_from is None or self.clip_to is None:
            self.bar.set_marked_range(None)
            return
        span = max(self.view_end - self.view_start, 1.0)
        first, last = sorted((self.clip_from, self.clip_to))
        left = min(max((first - self.view_start) / span, 0.0), 1.0)
        right = min(max((last - self.view_start) / span, 0.0), 1.0)
        self.bar.set_marked_range((left, right))

    def _ask_for_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, "Where should the clip be saved?")

    def save_clip_now(self, wait: bool = False) -> ExportOutcome | None:
        """Ask where, work out what, and write it off this thread.

        None means nothing was started - he closed the folder chooser, or a save
        is already running. `wait` is for the tests, which have no event loop to
        run the worker to completion in.
        """
        if self._saving or self.clip_from is None or self.clip_to is None:
            return None
        folder = self.ask_for_folder()
        if not folder:
            return None

        first, last = sorted((self.clip_from, self.clip_to))
        stream = (self.shown_streams() or ["camera"])[0]
        plan = clip_plan(self._segments, first, last)
        destination = unique_path(Path(folder), suggested_name(stream, first, last))
        run = self.run_ffmpeg

        def work() -> ExportOutcome:
            return export_clip(
                plan, destination=destination, stream=stream, run=run
            )

        self._saving = True
        self._draw_controls()
        self._set_status(f"Saving to {destination}…")
        signals = _ExportSignals()
        job = _ExportJob(work, signals)
        if wait:
            # The same worker, waited for. There is no event loop in a test to
            # deliver the finished signal, so the outcome is read off the job
            # rather than the path being changed to run it here - a clip that
            # was only ever tested on the calling thread would prove nothing
            # about the one the console actually writes.
            self._exports.start(job)
            if not self._exports.waitForDone(WAITED_SAVE_MS):
                logger.error("the clip was still being written after %s ms", WAITED_SAVE_MS)
            return self._clip_written(job.outcome)
        signals.done.connect(self._clip_written)
        self._export_signals.append(signals)
        self._exports.start(job)
        return None

    def _clip_written(self, outcome: ExportOutcome) -> ExportOutcome:
        self._saving = False
        self._set_status(outcome.message)
        self._draw_controls()
        return outcome

    # --------------------------------------------------------------- the words

    def _mark_the_calendar(self, stream: str) -> None:
        """Which days of the month on screen have something on them.

        A month at a time, because that is what is being looked at and because
        the whole archive is not a question anybody asked. Days with footage are
        drawn in the colour recorded time is drawn in everywhere else on this
        tab, so the calendar and the bar say the same thing in the same way.
        """
        self.days_with_footage = set()
        calendar = self.date_selector.calendar()
        calendar.setDateTextFormat(QDate(), QTextCharFormat())
        if not stream:
            return
        date = self.date_selector.date()
        first = QDate(date.year(), date.month(), 1)
        month_start, _ = day_bounds(first.year(), first.month(), 1)
        after = first.addMonths(1)
        month_end, _ = day_bounds(after.year(), after.month(), 1)
        try:
            segments = self._day_between(stream, month_start, month_end)
        except sqlite3.Error:
            logger.debug("the recordings for the month could not be read", exc_info=True)
            return
        for segment in segments:
            began = datetime.datetime.fromtimestamp(max(segment.start, month_start))
            ended = datetime.datetime.fromtimestamp(min(segment.end, month_end) - 0.001)
            for day in _days_between(began, ended):
                self.days_with_footage.add(QDate(day.year, day.month, day.day))
        marked = QTextCharFormat()
        marked.setForeground(QColor(PALETTE["ok"]))
        marked.setFontWeight(QFont.Weight.Bold)
        for day in self.days_with_footage:
            calendar.setDateTextFormat(day, marked)

    def _day_between(self, stream: str, start: float, end: float) -> list[Segment]:
        between = getattr(self._index, "between", None)
        if between is not None:
            return between(stream, start, end)
        return [s for s in self._index.all(stream) if s.end > start and s.start < end]

    def _draw_controls(self) -> None:
        """Point every control at what is actually true right now."""
        playing = self.playhead_time is not None and self._showing is not None
        self.transport.set_usable(playing)
        self.transport.set_playing(playing and not bool(_ask(self._pane, "paused")))
        for name, button in self.zoom_buttons.items():
            button.setChecked(name == self.zoom)
        whole = self.view_end - self.view_start >= self.day_end - self.day_start
        self.pan_earlier.setEnabled(not whole)
        self.pan_later.setEnabled(not whole)
        self.mark_start.setEnabled(playing)
        self.mark_end.setEnabled(playing)
        self.save_clip.setEnabled(
            not self._saving and self.clip_from is not None and self.clip_to is not None
        )
        self.clear_marks.setEnabled(self.clip_from is not None or self.clip_to is not None)

    def _draw_readout(self) -> None:
        """The moment being watched, big, with the pointer's own time beside it."""
        if self.playhead_time is None:
            when = datetime.datetime.fromtimestamp((self.view_start + self.view_end) / 2)
            self.readout_text = when.strftime("%A %d %B %Y")
        else:
            when = datetime.datetime.fromtimestamp(self.playhead_time)
            self.readout_text = when.strftime("%A %d %B %Y   %H:%M:%S")
        said = self.readout_text
        drift = self.drift_seconds()
        if drift is not None and drift >= DRIFT_WORTH_SAYING:
            said += f"   (the two pictures are {drift:.1f} s apart)"
        if self.hover_text:
            said += f"   →  {self.hover_text}"
        self.readout.setText(said)

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
        self._set_status(f"The list of recordings could not be read: {error}")


def _another(pane):
    """A second picture of the same kind as the one this tab was handed.

    The console builds one pane for this tab and does not know it now wants two.
    Rather than rewiring everything that builds a window, the second one is made
    from the first one's own class: in the console that is another libVLC pane,
    and in a test it is another fake. Anything that will not be built that way -
    the stand-in used when video is broken, which takes a message - simply means
    "both" is not offered, which on a machine with no video is the truth anyway.
    """
    try:
        return type(pane)()
    except Exception:  # noqa: BLE001 - one picture beats no tab
        logger.info("a second picture could not be built, so one camera at a time")
        return None


def _ask(pane, question: str, *args):
    """Ask a picture something it may not know how to answer.

    Panes come from three places - the real one, the fake, and the stand-in used
    when libVLC will not load - and the stand-in predates the transport. A
    console that crashed because a broken video pane could not be paused would
    be losing the whole window to the part of it that was already broken.
    """
    act = getattr(pane, question, None)
    if act is None:
        return None
    try:
        return act(*args) if callable(act) else act
    except Exception:  # noqa: BLE001 - a picture may not take the console with it
        logger.debug("the picture would not answer %s", question, exc_info=True)
        return None


def _as_url(path: str) -> str:
    where = Path(path)
    try:
        return where.as_uri()
    except ValueError:
        # as_uri refuses a relative path; the catalogue should never hold one,
        # but a playable guess beats an exception in front of an operator.
        return where.resolve().as_uri()


def _days_between(first: datetime.datetime, last: datetime.datetime):
    day = first.date()
    while day <= last.date():
        yield day
        day += datetime.timedelta(days=1)


def _duration(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
