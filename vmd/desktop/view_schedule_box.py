"""'Switch the view on a schedule': the box on the Settings tab, and the small
dialog that picks a start time.

The console watches one view at a time - thermal at night, visible by day - and
the operator used to press the view button on the Live tab twice a day. This box
sets the two times once. It only changes what the console shows; nothing is
sent to the camera and recording is untouched.

As with the restart box, nothing here is typed: the views are a list to pick
from and a time is hour buttons plus quarter-hour buttons, with a 0-59 spin box
for any other minute. Every change is saved at once, without Save - the people
who set this tick it and walk away. What the schedule means is
`vmd/desktop/view_schedule.py`; this file only asks and shows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop import view_schedule
from vmd.desktop.restart_box import MINUTE_CHOICES, _choice
from vmd.desktop.style import PALETTE, SIZE_BAND, SIZE_SMALL, SPACE_SNUG, SPACE_STEP
from vmd.settings import ViewSchedule

SAME_TIME = "day and night cannot start at the same time"
TOO_FEW = "needs two views - add a second stream"
HAND_NOTE = (
    "Only what this console shows changes, exactly as if the view button on the "
    "Live tab were pressed. A view picked by hand stays until the next switch time."
)


def default_views(streams: list[tuple[str, bool]]) -> tuple[str, str]:
    """(day, night) when none were picked yet: night is the first thermal
    stream, day the first one that is not - or, failing that, simply the
    first two streams in some order. Empty strings when there are none."""
    names = [name for name, _thermal in streams]
    night = next((name for name, thermal in streams if thermal), "")
    day = next((name for name, thermal in streams if not thermal), "")
    if not night:
        night = next((name for name in names if name != day), "")
    if not day:
        day = next((name for name in names if name != night), "")
    return day, night


class TimeDialog(QDialog):
    """An hour and a minute, all buttons, like the restart dialog's last step.

    `forbidden` is the other period's start time: day and night cannot start at
    the same minute, so that one time cannot be chosen (OK greys out and says why).
    """

    def __init__(self, initial: str, forbidden: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Start time")
        self._forbidden = forbidden
        hours, minutes = initial.split(":")
        self.hour = int(hours)
        self.minute = int(minutes)

        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_STEP)
        outer.addWidget(QLabel("From what time?"))
        grid = QGridLayout()
        grid.setSpacing(SPACE_SNUG)
        self._hour_group = QButtonGroup(self)
        self.hour_buttons: dict[int, QPushButton] = {}
        for hour in range(24):
            button = _choice(f"{hour:02d}:00")
            button.clicked.connect(lambda _=False, h=hour: self.choose_hour(h))
            self._hour_group.addButton(button)
            self.hour_buttons[hour] = button
            grid.addWidget(button, hour // 8, hour % 8)
        outer.addLayout(grid)

        outer.addWidget(QLabel("Minutes"))
        line = QHBoxLayout()
        self._minute_group = QButtonGroup(self)
        self.minute_buttons: dict[int, QPushButton] = {}
        for minute in MINUTE_CHOICES:
            button = _choice(f":{minute:02d}")
            button.clicked.connect(lambda _=False, m=minute: self.choose_minute(m))
            self._minute_group.addButton(button)
            self.minute_buttons[minute] = button
            line.addWidget(button)
        # Any other minute, as in the restart dialog: a 0-59 spin box, so a
        # minute that does not exist cannot be entered.
        line.addSpacing(SPACE_STEP)
        line.addWidget(QLabel("or any minute:"))
        self.minute_box = QSpinBox()
        self.minute_box.setRange(0, 59)
        self.minute_box.setPrefix(":")
        self.minute_box.setMinimumHeight(36)
        self.minute_box.setMinimumWidth(70)
        self.minute_box.setToolTip("Type any minute from 0 to 59, for example 20 for 18:20")
        self.minute_box.valueChanged.connect(self.choose_minute)
        line.addWidget(self.minute_box)
        line.addStretch(1)
        outer.addLayout(line)

        self._note = QLabel("")
        self._note.setStyleSheet(f"color: {PALETTE['alarm']}; font-size: {SIZE_SMALL}px;")
        outer.addWidget(self._note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok_button = QPushButton("OK")
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok_button)
        outer.addLayout(buttons)

        self.choose_hour(self.hour)
        self.choose_minute(self.minute)

    def choose_hour(self, hour: int) -> None:
        self.hour = hour
        self.hour_buttons[hour].setChecked(True)
        self._update()

    def choose_minute(self, minute: int) -> None:
        """From a :00/:15/:30/:45 button or from the box - they stay in step."""
        self.minute = minute
        if self.minute_box.value() != minute:
            self.minute_box.setValue(minute)  # re-enters here once, harmlessly
        # An exclusive group will not let its checked button go, so it is told
        # not to be exclusive while a minute off the quarter hours is shown.
        self._minute_group.setExclusive(False)
        for value, button in self.minute_buttons.items():
            button.setChecked(value == minute)
        self._minute_group.setExclusive(True)
        self._update()

    def value(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    def note_text(self) -> str:
        return self._note.text()

    def _update(self) -> None:
        refused = self.value() == self._forbidden
        self.ok_button.setEnabled(not refused)
        self._note.setText(SAME_TIME if refused else "")


class ViewScheduleBox(QGroupBox):
    """A tick, a day row and a night row, and when the next switch is."""

    #: The operator changed something (not `load`). The Settings tab saves on
    #: it at once - there is no Save for this box.
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Switch the view on a schedule", parent)
        self._now: Callable[[], datetime] = datetime.now
        self._streams: list[str] = []
        self._loading = False
        # Replaced by tests, so that no modal dialog ever opens in the suite.
        # Given (this period's time, the other period's time); None = cancelled.
        self.ask_for_time: Callable[[str, str], str | None] = self._ask

        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_SNUG)
        self.enabled_box = QCheckBox("Switch the view by itself")
        self.enabled_box.toggled.connect(self._edited)
        outer.addWidget(self.enabled_box)

        self.day_combo, self.day_time = self._period(outer, "By day show")
        self.night_combo, self.night_time = self._period(outer, "At night show")
        self.day_time.clicked.connect(lambda: self._pick_time(self.day_time, self.night_time))
        self.night_time.clicked.connect(lambda: self._pick_time(self.night_time, self.day_time))

        note = QLabel(HAND_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;")
        outer.addWidget(note)
        self._next = QLabel("")
        self._next.setWordWrap(True)
        self._next.setStyleSheet(f"color: {PALETTE['ok']}; font-size: {SIZE_BAND}px;")
        outer.addWidget(self._next)
        # Every second, like the "Next restart" line: under an hour the
        # countdown is in seconds, and a line that stood still read as broken.
        self._ticker = QTimer(self)
        self._ticker.timeout.connect(self._refresh)
        self._ticker.start(1000)

    def _period(self, outer: QVBoxLayout, words: str) -> tuple[QComboBox, QPushButton]:
        line = QHBoxLayout()
        label = QLabel(words)
        label.setMinimumWidth(110)
        line.addWidget(label)
        combo = QComboBox()
        combo.setMinimumHeight(36)
        combo.setMinimumWidth(140)
        combo.currentTextChanged.connect(self._edited)
        line.addWidget(combo)
        line.addWidget(QLabel("from"))
        button = QPushButton("00:00")
        button.setMinimumHeight(36)
        button.setMinimumWidth(80)
        button.setToolTip("Press to choose the time this view comes on, every day")
        line.addWidget(button)
        line.addStretch(1)
        outer.addLayout(line)
        return combo, button

    # ------------------------------------------------------------------ load

    def load(self, sched: ViewSchedule, streams: list[tuple[str, bool]],
             now: Callable[[], datetime] = datetime.now) -> None:
        """Show `sched` for these streams, (name, thermal) each. Emits nothing."""
        self._now = now
        self._streams = [name for name, _thermal in streams]
        day_default, night_default = default_views(streams)
        self._loading = True
        try:
            self.enabled_box.setChecked(sched.enabled)
            self._fill(self.day_combo, sched.day_view or day_default)
            self._fill(self.night_combo, sched.night_view or night_default)
            self.day_time.setText(sched.day_from)
            self.night_time.setText(sched.night_from)
        finally:
            self._loading = False
        # One view cannot be switched to another: the whole box waits, greyed,
        # with the reason on its last line.
        usable = len(self._streams) >= 2
        for widget in (self.enabled_box, self.day_combo, self.night_combo,
                       self.day_time, self.night_time):
            widget.setEnabled(usable)
        self._refresh()

    def _fill(self, combo: QComboBox, chosen: str) -> None:
        """The stream names - and a chosen name that is no longer one of them,
        kept rather than silently swapped, so the line below can name it."""
        combo.clear()
        combo.addItems(self._streams)
        if chosen and chosen not in self._streams:
            combo.addItem(chosen)
        combo.setCurrentText(chosen)

    # ---------------------------------------------------------------- reading

    def schedule(self) -> ViewSchedule:
        return ViewSchedule(
            enabled=self.enabled_box.isChecked(),
            day_view=self.day_combo.currentText(),
            day_from=self.day_time.text(),
            night_view=self.night_combo.currentText(),
            night_from=self.night_time.text(),
        )

    def next_line(self) -> str:
        return self._next.text()

    # ---------------------------------------------------------------- editing

    def _ask(self, initial: str, other: str) -> str | None:
        dialog = TimeDialog(initial, other, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.value()
        return None

    def _pick_time(self, button: QPushButton, other: QPushButton) -> None:
        picked = self.ask_for_time(button.text(), other.text())
        if picked is None or picked == button.text() or picked == other.text():
            return
        button.setText(picked)
        self._edited()

    def _edited(self, *_args) -> None:
        if self._loading:
            return
        self._refresh()
        self.changed.emit()

    def _refresh(self) -> None:
        if len(self._streams) < 2:
            text = TOO_FEW
        else:
            sched = self.schedule()
            reason = view_schedule.problem(sched, self._streams)
            if not sched.enabled:
                text = "Off"
            elif reason is not None:
                text = reason
            else:
                now = self._now()
                when, view = view_schedule.next_switch(sched, now)
                text = f"Next switch: {view_schedule.next_in_words(when, now)} → {view}"
        if self._next.text() != text:
            self._next.setText(text)
