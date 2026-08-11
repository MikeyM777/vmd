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
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.live import WrappedNote
from vmd.desktop.style import (
    MONO,
    PALETTE,
    SIZE_HEADING,
    SPACE_SNUG,
    SPACE_STEP,
)
from vmd.desktop.timeline import coverage_bars, day_bounds, seek_target, time_at
from vmd.desktop.video import VideoPane
from vmd.storage.index import Segment, SegmentIndex

logger = logging.getLogger(__name__)

# How tall the day is drawn. Taller than it was, because it is the one control
# on this tab and it is aimed at with a mouse: a 34 px strip at the bottom of
# the window read as a divider rather than as the thing you click.
BAR_HEIGHT = 54
PLAYHEAD_WIDTH = 3

# Where the hour rules go, and how much of the bar they cross. A day drawn as
# an unbroken strip is a strip: nothing on it says which end is morning, so a
# gap in the coverage cannot be turned into a time without counting pixels.
HOUR_STEP = 3
TICK_HEIGHT = 8

# What the tab says before anything has been recorded at all.
NOTHING_RECORDED = "Nothing has been recorded yet."

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
MARK_WIDTH = 3
MARK_CLICK_PIXELS = MARK_WIDTH / 2 + 0.5
MARK_TOLERANCE_SECONDS = 30.0

# How far before the movement playback starts. An event that begins on the
# first frame you see is one you have already missed: the approach is the part
# worth watching.
EVENT_LEAD_SECONDS = 5.0


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
        self._marks: list[float] = []
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

    def set_marks(self, marks: list[float]) -> None:
        self._marks = list(marks)
        self.update()

    def set_playhead(self, playhead: float | None) -> None:
        self._playhead = playhead
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        width = self.width()
        height = self.height()
        painter.fillRect(0, 0, width, height, QColor(PALETTE["well"]))
        # The hours, under everything. Without them a day is an unmarked strip
        # and a gap in it cannot be read as a time without counting pixels.
        rules = QColor(PALETTE["line"])
        painter.setPen(rules)
        font = painter.font()
        font.setPixelSize(SIZE_HEADING)
        painter.setFont(font)
        for hour in range(0, 24, HOUR_STEP):
            x = int(round(hour / 24.0 * width))
            painter.fillRect(x, height - TICK_HEIGHT, 1, TICK_HEIGHT, rules)
            if hour:
                painter.drawText(x + 3, height - TICK_HEIGHT - 2, f"{hour:02d}")
        recorded = QColor(PALETTE["ok"])
        # Above the hour marks, so the labels stay readable over a full day.
        top, span_height = 0, height - TICK_HEIGHT - SIZE_HEADING - 4
        for left, span in self._bars:
            x = int(round(left * width))
            # At least one pixel: a segment shorter than a pixel of the day is
            # still a segment, and drawing nothing would claim it is a gap.
            w = max(1, int(round(span * width)))
            painter.fillRect(x, top, min(w, width - x), span_height, recorded)
        # Over the coverage, under the playhead: a mark says something happened
        # there, and the playhead says where the operator is looking now.
        movement = QColor(PALETTE["alarm"])
        for fraction in self._marks:
            x = int(round(fraction * width)) - MARK_WIDTH // 2
            x = min(max(x, 0), max(width - MARK_WIDTH, 0))
            painter.fillRect(x, 0, MARK_WIDTH, height, movement)
        if self._playhead is not None:
            x = int(round(self._playhead * width)) - PLAYHEAD_WIDTH // 2
            x = min(max(x, 0), max(width - PLAYHEAD_WIDTH, 0))
            painter.fillRect(x, 0, PLAYHEAD_WIDTH, height, QColor(PALETTE["accent"]))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt's name
        width = max(self.width(), 1)
        # The width goes with the fraction because the tolerance around a
        # movement mark is in pixels, and only this widget knows how many
        # pixels a day is drawn in.
        self._tab.click_at(event.position().x() / width, width=width)
        event.accept()


class PlaybackTab(QWidget):
    """A day of recordings, and a player pointed into it."""

    def __init__(
        self,
        index: SegmentIndex,
        pane: VideoPane,
        events=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._pane = pane
        # Anything with `between(start, end, stream)` - in the console an
        # EventStore over the same events.db the Live tab reads. None means no
        # detection to draw, which is a day with no marks and nothing else.
        self._events = events
        today = datetime.date.today()
        self._day_start, self._day_end = day_bounds(today.year, today.month, today.day)
        self._segments: list[Segment] = []
        self.coverage: list[tuple[float, float]] = []
        # (fraction of the day, the event) for every mark on the bar.
        self.event_marks: list[tuple[float, object]] = []
        self.status_text = ""
        # How far into the file the last seek asked to start. It is handed to
        # the player now rather than only recorded: `VideoPane.show` takes the
        # position, and until it did, an operator who clicked 14:32 was given
        # the file containing 14:32 played from its beginning - up to five
        # minutes from the moment they asked about. For a system whose whole
        # purpose is "something happened, show me", that is not playback.
        self.seek_offset = 0.0
        self.playhead_time: float | None = None
        # Guards the controls while they are being set from code, so that
        # populating the stream list does not reload the day underneath itself.
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_STEP, SPACE_STEP, SPACE_STEP, SPACE_STEP)
        layout.setSpacing(SPACE_STEP)

        controls = QHBoxLayout()
        controls.setSpacing(SPACE_SNUG)
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
            # The same frame the Live tab gives its pictures, so footage looks
            # like footage on both tabs rather than like a hole on one of them.
            well = QFrame()
            well.setObjectName("videoFrame")
            well.setStyleSheet(
                f"QFrame#videoFrame {{ border: 1px solid {PALETTE['line_strong']}; "
                f"background: {PALETTE['well']}; }}"
            )
            inside = QVBoxLayout(well)
            inside.setContentsMargins(0, 0, 0, 0)
            inside.addWidget(pane)
            layout.addWidget(well, 1)

        self.bar = TimelineBar(self)
        layout.addWidget(self.bar)

        # A WrappedNote, not a word-wrapped QLabel: this line carries "no
        # recording at 14:32" and "the movement there is no longer on disk",
        # which are the two answers an operator most needs to read whole.
        self._status = WrappedNote("")
        self._status.setStyleSheet(
            f"color: {PALETTE['muted']}; font-family: {MONO};"
        )
        layout.addWidget(self._status)

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
            self.event_marks = []
            self.bar.set_bars([], None)
            self.bar.set_marks([])
            self._report_unreadable(error)
            return

        self._segments = [
            s for s in segments if s.end > self._day_start and s.start < self._day_end
        ]
        self.coverage = coverage_bars(self._segments, self._day_start, self._day_end)
        self.bar.set_bars(self.coverage, None)
        self._load_marks(stream)

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

    # -------------------------------------------------------- what moved, drawn

    def _load_marks(self, stream: str) -> None:
        """The movement events inside the day being shown, as fractions of it.

        Filtered against the day here as well as in the query. A mark drawn
        outside the bar is clamped to its edge, and a mark at the edge of the
        bar would claim movement at midnight that happened the day before.

        A store that cannot be read costs the marks and nothing else: the
        coverage comes from the segment index, and an operator who loses the
        movement marks must not lose the footage they were drawn over.
        """
        self.event_marks = []
        if self._events is not None:
            span = self._day_end - self._day_start
            try:
                events = self._events.between(self._day_start, self._day_end, stream)
            except Exception as error:  # noqa: BLE001 - the footage is not downstream of this
                logger.warning("the movement events could not be read: %s", error)
                events = []
            for event in events:
                if not self._day_start <= event.started < self._day_end:
                    continue
                self.event_marks.append(((event.started - self._day_start) / span, event))
        self.bar.set_marks([fraction for fraction, _ in self.event_marks])

    def mark_tolerance_seconds(self, width: int) -> float:
        """How far from a mark a click still means that mark, on a bar this wide.

        A duration, because the day is a duration - but one that knows how the
        bar is drawn, because what the operator aims at is the red he can see
        and that is measured in pixels. See `MARK_CLICK_PIXELS`.
        """
        seconds_per_pixel = (self._day_end - self._day_start) / max(width, 1)
        return max(MARK_TOLERANCE_SECONDS, seconds_per_pixel * MARK_CLICK_PIXELS)

    def _mark_near(self, fraction: float, width: int) -> object | None:
        """The movement mark this click meant, if it meant one.

        Nearest wins, so two events a minute apart stay separately clickable.
        """
        if not self.event_marks:
            return None
        when = time_at(fraction, self._day_start, self._day_end)
        nearest = min(self.event_marks, key=lambda mark: abs(mark[1].started - when))
        if abs(nearest[1].started - when) > self.mark_tolerance_seconds(width):
            return None
        return nearest[1]

    # ---------------------------------------------------------------- the click

    def click_at(self, fraction: float, width: int | None = None) -> None:
        """Play whatever covers this fraction of the day, or say what does not.

        A click on - or within a pixel of - a movement mark means the mark, and
        plays from five seconds before it. The alternative, the exact time under
        the pointer, is a time nobody can aim at: one pixel of the bar is over a
        minute of the day. See `MARK_CLICK_PIXELS` for why the tolerance has to
        know how wide the bar is.
        """
        width = self.bar.width() if width is None else width
        event = self._mark_near(fraction, width)
        if event is not None:
            self._play_at(event.started, event=event)
            return
        self._play_at(time_at(fraction, self._day_start, self._day_end))

    def show_event(self, event) -> bool:
        """Show me that movement: its day, its stream, and the moment itself.

        The one call behind both `Show me` on the alarm strip and a double click
        in the movement list. It is deliberately the same path the timeline's own
        marks already take - `_play_at` with the event - so that being taken to a
        movement and clicking its mark cannot ever mean two different things,
        including the five-second lead and the answer when there is no footage.

        Returns whether there was anything to play. False is not a failure: an
        event can predate recording, can be on a stream nothing was recording,
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
            self._set_status(f"that movement could not be opened: {error}")
            return False

    def _play_at(self, when: float, event=None) -> bool:
        """Open the file covering this moment, at this moment inside it.

        Answers whether anything is playing, for the caller that has just taken
        the operator to another tab to see it. `click_at` ignores it: he is
        already looking at the bar he clicked, and the line under it has the
        answer either way.

        For a movement mark the lead is taken off HERE rather than off the time
        that was asked for, and that is the difference between a mark that
        plays and one that says there is no recording. An event two seconds
        into a segment is five seconds after a moment that belongs to the
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
        self.playhead_time = when
        clock = datetime.datetime.fromtimestamp(when).strftime("%H:%M:%S")
        span = self._day_end - self._day_start
        fraction = min(max((when - self._day_start) / span, 0.0), 1.0)

        target = seek_target(self._segments, when)
        if target is None:
            # Say the time that was asked about, and leave the picture alone.
            # Playing the nearest file instead would show the operator footage
            # from a different moment while the clock claims otherwise. A mark
            # whose footage retention has already reclaimed is answered the same
            # way: the movement was real, and there is nothing left to show.
            self.bar.set_playhead(None)
            note = f"no recording at {clock}"
            if event is not None:
                note += f" - the movement on {event.stream} there is no longer on disk"
            self._set_status(note)
            return False

        self.bar.set_playhead(fraction)
        self.seek_offset = target.offset_seconds
        path = Path(target.path)
        try:
            url = path.as_uri()
        except ValueError:
            # as_uri refuses a relative path; the index should never hold one,
            # but a playable guess beats an exception in front of an operator.
            url = path.resolve().as_uri()
        self._pane.show(url, at_seconds=self.seek_offset)
        logger.info("playing %s from %.1f s in", path.name, target.offset_seconds)
        note = f"{clock} - {path.name}, {_duration(target.offset_seconds)} in"
        if event is not None:
            # The lead it really got, not the one it asked for. A movement two
            # seconds into a file is played from that file's first frame, and
            # saying "5s before" about it would be the console rounding a
            # measurement up in front of somebody making a decision from it.
            note = (
                f"{clock} - {_duration(lead)} before the movement on "
                f"{event.stream}, {path.name}"
            )
        self._set_status(note)
        return True

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
