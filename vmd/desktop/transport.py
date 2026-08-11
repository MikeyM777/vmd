"""The row of buttons under the picture on the Playback tab.

There was no transport at all. The controls row was `Day`, `Stream`, and
nothing else: no play, no pause, no step, no way back ten seconds. Re-watching
the same ten seconds is the single most common thing anyone does with security
footage, and it cost a fresh click on a bar where one pixel is over a minute of
the day.

**Buttons, and then keys.** The operator's words were *"i rather buttons, space
and arrows are nice but i need also buttons"*. So the buttons are the control
and the keys are an addition: nothing on this tab can only be reached from the
keyboard. Every one of them is at least 32 px tall and 44 px wide, because this
is used under pressure by somebody who is not a mouse athlete, and a row of
seven small controls is a row of seven controls he misses.

**Words on every one.** No bare glyphs. An arrow alone does not say how far it
goes, and "back ten seconds" and "back a minute" look identical as arrows. The
glyph is there as well, because it is what the eye finds first, but the number
is what makes the choice.

This is a widget and nothing else: it emits what was pressed and knows nothing
about players, files or times. What "ten seconds back" means at the edge of a
segment belongs to the tab, which is the only thing that knows where the
recordings are.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from vmd.desktop.style import SPACE_SNUG, SPACE_STEP

# The six he asked for. A quarter and a half for reading a person's movement
# frame by frame; two, four and eight for crossing a quiet night.
SPEEDS: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)

# What each one is called. 1x is not a number anybody thinks in, so the one that
# means "as it happened" says so.
SPEED_WORDS: dict[float, str] = {
    0.25: "Quarter speed",
    0.5: "Half speed",
    1.0: "Normal speed",
    2.0: "2x faster",
    4.0: "4x faster",
    8.0: "8x faster",
}

# Big enough to hit without aiming, on a 1280x720 logical screen - which is what
# a 1080p laptop panel reports at Windows' factory 150% scaling, and the
# tightest case this console has to survive.
BUTTON_HEIGHT = 34
BUTTON_WIDTH = 62
PLAY_WIDTH = 96


class TransportBar(QWidget):
    """Play, pause, step, skip and speed - as things on the screen.

    `play_pause` is one signal rather than two, because the console is the only
    thing that knows whether anything is playing and the button must never be
    the thing that decides. It is told, by `set_playing`.
    """

    play_pause = Signal()
    skipped = Signal(float)
    speed_chosen = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Set from code while the speed list is being pointed at what the player
        # is really doing, so that following the player does not ask the player
        # to change.
        self._loading = False

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_SNUG)

        self.back_minute = self._skip("◀◀  1 min", -60.0, "Back one minute")
        self.back_ten = self._skip("◀  10 sec", -10.0, "Back ten seconds")
        self.step_back = self._skip("◀  1 sec", -1.0, "Back one second")

        self.play_button = QPushButton("▶  Play")
        self.play_button.setMinimumHeight(BUTTON_HEIGHT)
        self.play_button.setMinimumWidth(PLAY_WIDTH)
        self.play_button.setProperty("primary", "true")
        self.play_button.setToolTip("Play or hold the picture still (space bar)")
        self.play_button.clicked.connect(self.play_pause.emit)

        self.step_forward = self._skip("1 sec  ▶", 1.0, "Forward one second")
        self.forward_ten = self._skip("10 sec  ▶", 10.0, "Forward ten seconds")
        self.forward_minute = self._skip("1 min  ▶▶", 60.0, "Forward one minute")

        for widget in (
            self.back_minute,
            self.back_ten,
            self.step_back,
            self.play_button,
            self.step_forward,
            self.forward_ten,
            self.forward_minute,
        ):
            row.addWidget(widget)

        self.speed_selector = QComboBox()
        self.speed_selector.setMinimumHeight(BUTTON_HEIGHT)
        self.speed_selector.setToolTip("How fast the footage runs")
        for speed in SPEEDS:
            self.speed_selector.addItem(SPEED_WORDS[speed], speed)
        self.speed_selector.setCurrentIndex(SPEEDS.index(1.0))
        self.speed_selector.currentIndexChanged.connect(self._speed_changed)
        row.addSpacing(SPACE_STEP)
        row.addWidget(self.speed_selector)
        row.addStretch(1)

    # ------------------------------------------------------------- the buttons

    def _skip(self, words: str, seconds: float, told: str) -> QPushButton:
        button = QPushButton(words)
        button.setMinimumHeight(BUTTON_HEIGHT)
        button.setMinimumWidth(BUTTON_WIDTH)
        button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        button.setToolTip(told)
        button.clicked.connect(lambda _checked=False, s=seconds: self.skipped.emit(s))
        return button

    def buttons(self) -> list[QPushButton]:
        return [
            self.back_minute,
            self.back_ten,
            self.step_back,
            self.play_button,
            self.step_forward,
            self.forward_ten,
            self.forward_minute,
        ]

    # --------------------------------------------------------------- the state

    def set_playing(self, playing: bool) -> None:
        """What the button offers next, which is the opposite of what is happening."""
        self.play_button.setText("⏸  Pause" if playing else "▶  Play")

    def set_usable(self, usable: bool) -> None:
        """Off while there is nothing to play.

        A transport over an empty day invites a press that does nothing, and a
        control that does nothing reads as a console that has stopped answering.
        """
        for button in self.buttons():
            button.setEnabled(usable)
        self.speed_selector.setEnabled(usable)

    def speed(self) -> float:
        chosen = self.speed_selector.currentData()
        return float(chosen) if chosen is not None else 1.0

    def set_speed(self, speed: float) -> None:
        """Point the list at what the player is really doing, and ask for nothing."""
        try:
            index = SPEEDS.index(float(speed))
        except ValueError:
            return
        self._loading = True
        try:
            self.speed_selector.setCurrentIndex(index)
        finally:
            self._loading = False

    def _speed_changed(self, _index: int) -> None:
        if self._loading:
            return
        self.speed_chosen.emit(self.speed())
