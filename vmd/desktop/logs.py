"""The last few hundred things the system said, where they can be read.

The operator has this window and nothing else. Whatever the console, the
streaming server and the recorder report has to be reachable here, because
asking someone to open a log file on a machine bolted to a desk is not a plan.
"""

from __future__ import annotations

import datetime
import logging
import re
import threading
from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.style import (
    PALETTE,
    SIZE_BODY,
    SIZE_HEADING,
    SPACE_SNUG,
    SPACE_STEP,
    WEIGHT_VALUE,
)

LOG_LINES = 500

# How wide the three narrow columns are. Sized once rather than shared out
# evenly: the time is eight characters, the level is at most eight ("CRITICAL"),
# the source rarely more than ten, and the message - the part anybody reads -
# takes everything that is left. Evenly shared, a quarter of the window went to
# a column holding "INFO".
#
# The numbers include the cell padding and the monospace face these columns are
# drawn in. They are wide enough that no value in them is ever cut: a clock
# reading "12:27:..." is worse than useless, because the line it belongs to
# cannot be placed against any other line in the file.
TIME_WIDTH = 96
LEVEL_WIDTH = 88
SOURCE_WIDTH = 124

# What the table says before anything has been logged. Never blank: a black
# rectangle is indistinguishable from a tab that failed to load, and this one is
# the only place on the machine where the operator can read what went wrong.
NOTHING_LOGGED = "Nothing has been logged yet."
SEVERE = {"WARNING", "ERROR", "CRITICAL"}

# How close to the bottom the scrollbar has to be to count as "already there".
# Not always exactly at the maximum: row heights round, and a refresh that
# lands one pixel short of the old maximum must still count as following.
FOLLOW_SLACK_PX = 4

# A password inside a URL, as every process on this machine writes one. RTSP
# carries credentials in the address, so the URL the console hands go2rtc, and
# the one go2rtc names in `[streams] retry=N to url=...`, both carry the
# camera's password - and that line would land on the screen the operator is
# watching, and in any photograph of it.
#
# The username is kept. Which account was refused is half the diagnosis of a
# "401 Unauthorized", and it is not the secret. The `@` must be the one that
# ends the userinfo, so neither part may contain a slash or another `@`, which
# is also what stops an ordinary "user@host" in a sentence from matching.
#
# Every part is length-bounded, and the whole thing is skipped unless the line
# could possibly contain a URL. This runs on every record from every process,
# and one of those is a child writing half a megabyte without a newline: an
# unbounded `[a-z0-9+.-]*` before the `://` backtracks across the whole line at
# every position in it, which turned that flood into minutes of regex.
_CREDENTIALS = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]{0,15}://[^\s/@:]{1,256}):([^\s/@]{0,256})@")


def without_passwords(text: str) -> str:
    """The same line, with any password inside a URL taken out of it."""
    if "://" not in text or "@" not in text:
        return text
    return _CREDENTIALS.sub(r"\1:****@", text)


class LogBuffer(logging.Handler):
    """A ring of recent log records, safe to read from the UI thread.

    Written from whatever thread is logging - go2rtc's output pump, the
    supervisor, the recorder - and read from the UI thread on a timer. The
    lock only ever guards a deque append or a list() copy, both fast, so
    readers never block writers for long and vice versa.
    """

    def __init__(self, capacity: int = LOG_LINES) -> None:
        super().__init__()
        self.records: deque[dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._next_seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.exc_info:
                text += "\n" + logging.Formatter().formatException(record.exc_info)
        except Exception:  # noqa: BLE001 - logging must never raise into the caller
            text = "<unformattable log record>"
        # Here rather than at each source, because this is where every process
        # on the machine converges - the console, go2rtc, the recorder and the
        # detector - and the tab that shows it is on screen all day.
        text = without_passwords(text)
        with self._lock:
            self._next_seq += 1
            self.records.append(
                {
                    "seq": self._next_seq,
                    "time": record.created,
                    "level": record.levelname,
                    "source": record.name,
                    "text": text,
                }
            )

    def snapshot(self) -> list[dict]:
        """A safe copy for the UI thread to read. Never a live reference."""
        with self._lock:
            return list(self.records)


def attach(buffer: LogBuffer) -> LogBuffer:
    """Put the buffer on the root logger. Idempotent."""
    root = logging.getLogger()
    if buffer not in root.handlers:
        buffer.setLevel(logging.INFO)
        root.addHandler(buffer)
    return buffer


def _level_colour(level: str) -> str:
    if level in ("ERROR", "CRITICAL"):
        return PALETTE["alarm"]
    if level == "WARNING":
        return PALETTE["warn"]
    return PALETTE["muted"]


def _short_source(name: str) -> str:
    """The last part of a logger's name, which is the part that identifies it.

    "vmd.desktop.services" in a narrow column pushes the message off the screen,
    and every line on this machine starts with "vmd.". The children log under
    bare names of their own - "go2rtc", "recorder", "detector" - which come
    through untouched, and those are the ones that matter most.
    """
    return (name or "").rsplit(".", 1)[-1]


class LogsTab(QWidget):
    """A table of the buffer, newest last.

    Colour alone is never the only signal - the level column still says
    "ERROR" in words - but its absence is a real loss on a console read
    under glare, so the level cell is also tinted from the same PALETTE
    the rest of the window uses.
    """

    def __init__(self, buffer: LogBuffer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buffer = buffer
        self._filter = "ALL"
        self._last_signature: tuple | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_STEP, SPACE_STEP, SPACE_STEP, SPACE_STEP)
        layout.setSpacing(SPACE_STEP)

        controls = QHBoxLayout()
        controls.setSpacing(SPACE_SNUG)
        # Two choices, one of which is always on: a segmented control, drawn the
        # way this design draws one. They used to be two plain buttons, neither
        # holding its state and neither marked - the tab showing everything and
        # the tab showing only faults were the same picture down to the pixel.
        # That is worse than an unlabelled control: a quiet table means both
        # "nothing has gone wrong" and "you are only being shown what did", and
        # on the tab he opens when something is already wrong there was nothing
        # on screen to say which of the two he was looking at.
        self.all_button = QPushButton("All")
        self.warnings_button = QPushButton("Warnings and errors")
        self._filter_buttons = QButtonGroup(self)
        self._filter_buttons.setExclusive(True)
        for button in (self.all_button, self.warnings_button):
            button.setCheckable(True)
            self._filter_buttons.addButton(button)
        self.all_button.setChecked(True)
        # It said "Follow", which does not say what is being followed or what
        # happens if it is not. It is a tick box, so it is a state and not an
        # instruction: what it holds true is that the table stays at the newest
        # line as lines arrive, rather than staying where he scrolled to.
        self.follow_checkbox = QCheckBox("Keep showing the newest lines")
        self.follow_checkbox.setToolTip(
            "With this ticked the table follows the newest line as it arrives. "
            "Untick it to stay where you have scrolled to while you read."
        )
        self.follow_checkbox.setChecked(True)
        self.all_button.clicked.connect(self._show_all)
        self.warnings_button.clicked.connect(self._show_warnings_and_errors)
        # Asked for by the operator after a day of reading lines off this table
        # and typing them into a chat by hand. This machine has no terminal and
        # this tab is where the console, go2rtc, the recorder and the detector
        # all converge, so getting what is in it to somebody who is not standing
        # in front of it is part of what the tab is for.
        self.copy_button = QPushButton("Copy")
        self.copy_button.setToolTip(
            "Copy the lines shown here, so they can be pasted somewhere else"
        )
        self.copy_button.clicked.connect(self._copy)
        # What the button did. A button that changes nothing on screen, on a
        # console with no other feedback, reads as one that is broken - and it
        # is pressed again.
        self.copy_note = QLabel("")
        self.copy_note.setStyleSheet(f"color: {PALETTE['muted']};")
        controls.addWidget(self.all_button)
        controls.addWidget(self.warnings_button)
        controls.addWidget(self.copy_button)
        controls.addWidget(self.copy_note)
        controls.addStretch(1)
        controls.addWidget(self.follow_checkbox)
        layout.addLayout(controls)

        # "from" as well as the message. The buffer has recorded which logger
        # each line came from since it was written and the table never showed
        # it: the children were only distinguishable because they happen to
        # prefix their own messages, and the console's own modules were not
        # distinguishable at all. On a machine where this tab is the only thing
        # the operator can read, "who said this" is half the diagnosis.
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["time", "level", "from", "message"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        # The three narrow columns are fixed, so the message column does not
        # jump sideways every time a line arrives from a logger with a longer
        # name than the last one. Nothing on a console anyone is watching should
        # move because a value changed.
        for column, width in (
            (0, TIME_WIDTH), (1, LEVEL_WIDTH), (2, SOURCE_WIDTH)
        ):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.table.setColumnWidth(column, width)
        header.setHighlightSections(False)
        # Left-aligned, over left-aligned cells: a centred heading over a column
        # of monospace values does not read as belonging to them.
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        # In the table's place while there is nothing in it.
        self.empty = QLabel(NOTHING_LOGGED)
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(
            f"color: {PALETTE['muted']}; font-size: {SIZE_HEADING}px;"
        )
        layout.addWidget(self.empty, 1)
        self._show_table_or_not()
        self._draw_filters()

    def _show_table_or_not(self) -> None:
        """Whichever of the table and the empty state has something to say."""
        empty = self.table.rowCount() == 0
        self.table.setVisible(not empty)
        self.empty.setVisible(empty)

    def _show_all(self) -> None:
        self.set_level_filter("ALL")
        self.refresh()

    def _show_warnings_and_errors(self) -> None:
        self.set_level_filter("WARNING")
        self.refresh()

    def set_level_filter(self, level: str) -> None:
        self._filter = level
        # Whichever way the filter was set - a button, a test, anything later -
        # the buttons say what it is. A control that only tells the truth when
        # it was the thing that was pressed is the fault this is fixing.
        self.all_button.setChecked(level == "ALL")
        self.warnings_button.setChecked(level != "ALL")
        self._draw_filters()

    def filter_mark(self, button: QPushButton) -> str:
        """How this filter button is drawn, so a test can read it off the tab.

        Returned rather than compared here on purpose: what has to be true is
        that the one that is on does not look like the one that is off, and that
        is a question about what is drawn, not about a particular colour.
        """
        return button.styleSheet()

    def _draw_filters(self) -> None:
        """Mark the filter that is on.

        Drawn per button rather than left to a `:checked` rule, for the same
        reason the view chooser on the Live tab is: the application stylesheet
        has no opinion about a checked button, so both were painted identically
        and which one was on could only be worked out by reading the table. The
        mark is the accent bar the tab bar uses for the page you are on and the
        Playback tab uses for the zoom that is showing - one vocabulary for
        "this is where you are", and the one amber this design allows at rest.
        """
        for button in (self.all_button, self.warnings_button):
            on = button.isChecked()
            button.setStyleSheet(
                f"QPushButton {{ background: "
                f"{PALETTE['raised'] if on else PALETTE['surface']}; "
                f"color: {PALETTE['ink'] if on else PALETTE['muted']}; "
                f"border: 1px solid {PALETTE['line']}; "
                f"border-bottom: 2px solid "
                f"{PALETTE['accent'] if on else PALETTE['line']}; "
                f"font-size: {SIZE_BODY}px; "
                f"font-weight: {WEIGHT_VALUE if on else 400}; }}"
                f"QPushButton:hover {{ color: {PALETTE['ink']}; }}"
            )

    @property
    def row_count(self) -> int:
        return self.table.rowCount()

    def text_at(self, row: int) -> str:
        item = self.table.item(row, 3)
        return item.text() if item else ""

    def source_at(self, row: int) -> str:
        item = self.table.item(row, 2)
        return item.text() if item else ""

    def level_color_at(self, row: int) -> QColor:
        item = self.table.item(row, 1)
        return item.foreground().color() if item else QColor()

    def text_for_copying(self) -> str:
        """Everything on screen, as text, in the order it is on screen.

        What is on screen and not what is in the buffer: the filter is how the
        operator narrows this down to the fault he is chasing, and handing back
        everything anyway would return the haystack he has just removed.

        The same four columns, in the same order, because the person reading it
        at the other end is reading it against this window. Passwords were taken
        out when each line was logged, so nothing here can put one back.
        """
        return "\n".join(
            "  ".join(
                (
                    datetime.datetime.fromtimestamp(line["time"]).strftime("%H:%M:%S"),
                    line["level"],
                    _short_source(line["source"]),
                    line["text"],
                )
            )
            for line in self._filtered_lines()
        )

    def _copy(self) -> None:
        """Put it on the clipboard, and say what happened either way."""
        text = self.text_for_copying()
        count = len(self._filtered_lines())
        if not count:
            self.copy_note.setText("There is nothing here to copy.")
            return
        try:
            QApplication.clipboard().setText(text)
        except Exception:  # noqa: BLE001 - a clipboard is not worth a lost window
            logging.getLogger(__name__).exception("the log could not be copied")
            self.copy_note.setText("The clipboard would not take it.")
            return
        self.copy_note.setText(f"Copied {count} line{'' if count == 1 else 's'}.")

    def _filtered_lines(self) -> list[dict]:
        return [
            line
            for line in self._buffer.snapshot()
            if self._filter == "ALL" or line["level"] in SEVERE
        ]

    def refresh(self) -> None:
        """Redraw from the buffer, but cheaply and without yanking the view.

        Rebuilding every cell every tick is O(capacity) widget allocations
        several times a second - measured, that is too slow to run on a
        timer. Most ticks see no new lines at all, so a cheap signature (the
        oldest and newest sequence number still in view, and the count)
        catches the common no-change case and skips the rebuild entirely.
        Sequence numbers are monotonic and assigned under the same lock as
        the append, so this is exact, not a heuristic.
        """
        lines = self._filtered_lines()
        signature = (
            self._filter,
            len(lines),
            lines[0]["seq"] if lines else None,
            lines[-1]["seq"] if lines else None,
        )
        if signature == self._last_signature:
            return
        self._last_signature = signature

        scrollbar = self.table.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - FOLLOW_SLACK_PX
        should_follow = self.follow_checkbox.isChecked() or at_bottom
        previous_value = scrollbar.value()

        self.table.setRowCount(len(lines))
        for row, line in enumerate(lines):
            stamp = datetime.datetime.fromtimestamp(line["time"]).strftime("%H:%M:%S")
            self.table.setItem(row, 0, QTableWidgetItem(stamp))

            level_item = QTableWidgetItem(line["level"])
            level_item.setForeground(QBrush(QColor(_level_colour(line["level"]))))
            self.table.setItem(row, 1, level_item)

            source = QTableWidgetItem(_short_source(line["source"]))
            source.setForeground(QBrush(QColor(PALETTE["muted"])))
            self.table.setItem(row, 2, source)

            self.table.setItem(row, 3, QTableWidgetItem(line["text"]))

        self._show_table_or_not()
        if should_follow:
            self.table.scrollToBottom()
        else:
            scrollbar.setValue(min(previous_value, scrollbar.maximum()))
