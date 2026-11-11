"""'Restart the camera on a schedule': the box on the Settings tab, and the
dialog that adds a rule to it.

Nothing here is typed. The people who set this are not computer people, so
every choice is a button: how often, how many, what hour, what minutes. A time
that does not exist cannot be entered, so there is nothing to refuse on Save.
Before a rule is added the dialog says it as a sentence and shows the next
four times it will happen, with "… and so on" - so that "every 5 days" cannot
be read as "for 5 days and then stop".

A rule cannot be edited, only deleted and added again. That keeps `since` (the
moment it was added, which "every N hours" counts from) honest without any
reconciling. What a rule means is `vmd/ptz/restart_schedule.py`; this file
only asks and shows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.style import PALETTE, SIZE_BAND, SIZE_SMALL, SPACE_SNUG, SPACE_STEP
from vmd.ptz.restart_schedule import (
    day_words,
    describe,
    next_in_words,
    next_restart,
    upcoming_slots,
)
from vmd.settings import RestartRule

KINDS: list[tuple[str, str, str]] = [
    ("every_hours", "Every few hours", "e.g. every 2 hours, repeating"),
    ("daily", "Once a day", "e.g. every day at 16:00"),
    ("every_days", "Every few days", "e.g. every 2 days at 19:00, repeating"),
]
HOUR_CHOICES = [1, 2, 3, 4, 6, 8, 12]
DAY_CHOICES = [2, 3, 4, 5, 7]
MINUTE_CHOICES = [0, 15, 30, 45]
REPEATS = "🔁 It keeps repeating, forever, until you delete it."
NOTHING = "No scheduled restarts. The camera only restarts when someone presses the button."
FOREVER = {
    "every_hours": " - again and again, forever",
    "daily": " - every day, forever",
    "every_days": " - again and again, forever",
}


def sentence(rule: RestartRule) -> str:
    """'Restart every 5 days at 19:00 - again and again, forever'."""
    return f"Restart {describe(rule)}{FOREVER[rule.kind]}"


def times_line(rule: RestartRule, now: datetime) -> str:
    """'Next: today 19:00 → Mon 16.11 19:00 → … and so on'."""
    slots = upcoming_slots(rule, now, 4)
    shown = "  →  ".join(f"{day_words(when, now)} {when:%H:%M}" for when in slots)
    return f"Next: {shown}  →  … and so on"


def _choice(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setCheckable(True)
    button.setMinimumHeight(36)
    return button


class RestartDialog(QDialog):
    """Three steps, all buttons. `rule()` is None until every step is chosen."""

    def __init__(self, now: Callable[[], datetime] = datetime.now,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New restart time")
        self._now = now
        self.kind: str | None = None
        self.number: int | None = None
        self.hour: int | None = None
        self.minute = 0

        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_STEP)

        outer.addWidget(self._question("1. How often should the camera restart?"))
        kinds = QHBoxLayout()
        self._kind_group = QButtonGroup(self)
        self.kind_buttons: dict[str, QPushButton] = {}
        for kind, title, example in KINDS:
            button = _choice(f"{title}\n{example}")
            button.setMinimumHeight(60)
            button.clicked.connect(lambda _=False, k=kind: self.choose_kind(k))
            self._kind_group.addButton(button)
            self.kind_buttons[kind] = button
            kinds.addWidget(button)
        outer.addLayout(kinds)

        self._hours_step, self.hour_count_buttons = self._row(
            "2. Every how many hours?  " + REPEATS,
            [(n, "1 hour" if n == 1 else f"{n} hours") for n in HOUR_CHOICES],
            self.choose_number,
        )
        outer.addWidget(self._hours_step)
        self._days_step, self.day_count_buttons = self._row(
            "2. Every how many days?  " + REPEATS,
            [(n, f"{n} days") for n in DAY_CHOICES],
            self.choose_number,
        )
        outer.addWidget(self._days_step)

        self._time_step = QWidget()
        time_layout = QVBoxLayout(self._time_step)
        time_layout.setContentsMargins(0, 0, 0, 0)
        self._time_question = self._question("3. At what time?")
        time_layout.addWidget(self._time_question)
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
        time_layout.addLayout(grid)
        time_layout.addWidget(self._question("Minutes"))
        minutes = QHBoxLayout()
        self._minute_group = QButtonGroup(self)
        self.minute_buttons: dict[int, QPushButton] = {}
        for minute in MINUTE_CHOICES:
            button = _choice(f":{minute:02d}")
            button.clicked.connect(lambda _=False, m=minute: self.choose_minute(m))
            self._minute_group.addButton(button)
            self.minute_buttons[minute] = button
            minutes.addWidget(button)
        minutes.addStretch(1)
        self.minute_buttons[0].setChecked(True)
        time_layout.addLayout(minutes)
        outer.addWidget(self._time_step)

        self._sentence = QLabel("")
        self._sentence.setWordWrap(True)
        self._sentence.setStyleSheet(
            f"border-left: 4px solid {PALETTE['accent']}; padding: {SPACE_SNUG}px; "
            f"font-size: {SIZE_BAND}px;"
        )
        outer.addWidget(self._sentence)
        self._times = QLabel("")
        self._times.setWordWrap(True)
        self._times.setStyleSheet(f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;")
        outer.addWidget(self._times)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.add_button = QPushButton("Add this restart")
        self.add_button.setDefault(True)
        self.add_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.add_button)
        outer.addLayout(buttons)
        self._update()

    @staticmethod
    def _question(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _row(self, question: str, choices: list[tuple[int, str]],
             choose: Callable[[int], None]) -> tuple[QWidget, dict[int, QPushButton]]:
        step = QWidget()
        layout = QVBoxLayout(step)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._question(question))
        line = QHBoxLayout()
        group = QButtonGroup(step)
        found: dict[int, QPushButton] = {}
        for value, text in choices:
            button = _choice(text)
            button.clicked.connect(lambda _=False, v=value: choose(v))
            group.addButton(button)
            found[value] = button
            line.addWidget(button)
        line.addStretch(1)
        layout.addLayout(line)
        return step, found

    # ------------------------------------------------------------- choosing

    def choose_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.number = None
            for buttons in (self.hour_count_buttons, self.day_count_buttons):
                for button in buttons.values():
                    # An exclusive group will not let its checked button go,
                    # so it is told not to be exclusive for a moment.
                    group = button.group()
                    group.setExclusive(False)
                    button.setChecked(False)
                    group.setExclusive(True)
        self.kind = kind
        self.kind_buttons[kind].setChecked(True)
        self._update()

    def choose_number(self, number: int) -> None:
        self.number = number
        buttons = self.hour_count_buttons if self.kind == "every_hours" else self.day_count_buttons
        if number in buttons:
            buttons[number].setChecked(True)
        self._update()

    def choose_hour(self, hour: int) -> None:
        self.hour = hour
        self.hour_buttons[hour].setChecked(True)
        self._update()

    def choose_minute(self, minute: int) -> None:
        self.minute = minute
        self.minute_buttons[minute].setChecked(True)
        self._update()

    def rule(self) -> RestartRule | None:
        """The rule as chosen, counting from now, or None if a step is missing."""
        if self.kind is None:
            return None
        if self.kind in ("every_hours", "every_days") and self.number is None:
            return None
        if self.kind in ("daily", "every_days") and self.hour is None:
            return None
        fields: dict = {"kind": self.kind, "since": self._now().replace(microsecond=0)}
        if self.kind == "every_hours":
            fields["hours"] = self.number
        if self.kind == "every_days":
            fields["days"] = self.number
        if self.kind != "every_hours":
            fields["at"] = f"{self.hour:02d}:{self.minute:02d}"
        return RestartRule(**fields)

    def sentence_text(self) -> str:
        return self._sentence.text()

    def times_text(self) -> str:
        return self._times.text()

    def _update(self) -> None:
        self._hours_step.setVisible(self.kind == "every_hours")
        self._days_step.setVisible(self.kind == "every_days")
        self._time_step.setVisible(self.kind in ("daily", "every_days"))
        self._time_question.setText(
            ("2." if self.kind == "daily" else "3.") + " At what time?"
        )
        rule = self.rule()
        self.add_button.setEnabled(rule is not None)
        self._sentence.setVisible(rule is not None)
        self._times.setVisible(rule is not None)
        if rule is not None:
            self._sentence.setText(sentence(rule) + ".")
            self._times.setText(
                times_line(rule, self._now()) + "\n(no picture for about 2 minutes each time)"
            )


class RuleRow(QWidget):
    """One rule on the box: its sentence, its next times, and Delete."""

    def __init__(self, rule: RestartRule, now: datetime) -> None:
        super().__init__()
        self.rule = rule
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        words = QVBoxLayout()
        self.sentence = QLabel(sentence(rule))
        self.times = QLabel(times_line(rule, now))
        self.times.setStyleSheet(f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;")
        words.addWidget(self.sentence)
        words.addWidget(self.times)
        line.addLayout(words, 1)
        self.delete = QPushButton("Delete")
        self.delete.setStyleSheet(f"color: {PALETTE['alarm']};")
        self.delete.setToolTip("Take this restart off the list")
        line.addWidget(self.delete, 0, Qt.AlignmentFlag.AlignVCenter)


class RestartBox(QGroupBox):
    """The rules, one sentence each, + Add, and when the next restart is."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Restart the camera on a schedule", parent)
        self.rows: list[RuleRow] = []
        self._now: Callable[[], datetime] = datetime.now
        # Replaced by tests, so that no modal dialog ever opens in the suite.
        self.ask_for_rule: Callable[[], RestartRule | None] = self._ask
        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_SNUG)
        self._empty = QLabel(NOTHING)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(f"color: {PALETTE['muted']};")
        outer.addWidget(self._empty)
        self._list = QVBoxLayout()
        outer.addLayout(self._list)
        add_line = QHBoxLayout()
        self.add_button = QPushButton("+ Add a restart time")
        self.add_button.setMinimumHeight(36)
        self.add_button.clicked.connect(self._add_clicked)
        add_line.addWidget(self.add_button)
        add_line.addStretch(1)
        outer.addLayout(add_line)
        self._next = QLabel("")
        self._next.setStyleSheet(f"color: {PALETTE['ok']}; font-size: {SIZE_BAND}px;")
        outer.addWidget(self._next)
        # The "Next restart" line counts down while the tab is open: a line
        # that said "in 4 min" for four minutes read as nothing happening.
        self._ticker = QTimer(self)
        self._ticker.timeout.connect(self._refresh)
        self._ticker.start(1000)

    def load(self, rules: list[RestartRule], now: Callable[[], datetime] = datetime.now) -> None:
        self._now = now
        for row in list(self.rows):
            self._drop(row)
        for rule in rules:
            self.add_rule(rule)
        self._refresh()

    def add_rule(self, rule: RestartRule) -> None:
        row = RuleRow(rule, self._now())
        row.delete.clicked.connect(lambda _=False, r=row: self._drop(r))
        self.rows.append(row)
        self._list.addWidget(row)
        self._refresh()

    def rules(self) -> list[RestartRule]:
        return [row.rule for row in self.rows]

    def values(self) -> list[dict]:
        """What goes into settings.json."""
        return [rule.model_dump() for rule in self.rules()]

    def next_line(self) -> str:
        return self._next.text()

    def _ask(self) -> RestartRule | None:
        dialog = RestartDialog(self._now, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.rule()
        return None

    def _add_clicked(self) -> None:
        rule = self.ask_for_rule()
        if rule is not None:
            self.add_rule(rule)

    def _drop(self, row: RuleRow) -> None:
        if row in self.rows:
            self.rows.remove(row)
        self._list.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        self._refresh()

    def _refresh(self) -> None:
        now = self._now()
        self._empty.setVisible(not self.rows)
        for row in self.rows:
            # "today" becomes "tomorrow" at midnight, and a time that has passed
            # drops off the front - so each row's times move on too.
            text = times_line(row.rule, now)
            if row.times.text() != text:
                row.times.setText(text)
        found = next_restart(self.rules(), now)
        text = "" if found is None else f"Next restart: {next_in_words(found[0], now)}"
        if self._next.text() != text:
            self._next.setText(text)
