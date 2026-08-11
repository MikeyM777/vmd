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

from vmd.desktop.style import PALETTE, SIZE_HEADING, SPACE_SNUG, SPACE_STEP

LOG_LINES = 500

# How wide the three narrow columns are. Sized to their contents once rather
# than shared out evenly: the time is eight characters, the level is at most
# eight, the source rarely more than ten, and the message - the part anybody
# reads - takes everything that is left. Evenly shared, a quarter of the window
# went to a column holding "INFO".
TIME_WIDTH = 78
LEVEL_WIDTH = 78
SOURCE_WIDTH = 110

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
        self.all_button = QPushButton("All")
        self.warnings_button = QPushButton("Warnings and errors")
        self.follow_checkbox = QCheckBox("Follow")
        self.follow_checkbox.setChecked(True)
        self.all_button.clicked.connect(self._show_all)
        self.warnings_button.clicked.connect(self._show_warnings_and_errors)
        controls.addWidget(self.all_button)
        controls.addWidget(self.warnings_button)
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
