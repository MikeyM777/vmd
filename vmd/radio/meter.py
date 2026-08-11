"""A bar that fills, for the two link figures somebody has to read at a glance.

The link panel was fourteen sentences. Every one of them was true and several of
them were the result of a day's work, and that is exactly the problem: the
person standing in front of this console is not going to read a paragraph to
find out whether the picture is about to break up. His words were "much less
text, make it easier to understand also for no tech guys, make it visual".

A bar answers the question a number cannot. "-66 dBm" means nothing without the
scale it sits on; a bar IS the scale, and how full it is answers "is this good"
before the caption has been read at all. So each of the two figures that decide
whether this link works - how strong the signal is, and how much of the link is
already spent - gets one bar, one word of a name, and the number itself small
and to the right for whoever wants it.

**The fill moves rather than jumps.** A figure that changes by redrawing is
indistinguishable from a figure that was always that, and this panel's whole
history is an operator who - correctly - stopped believing a screen that looked
calm. A bar that slides from where it was to where it is says "this changed"
without a word, and it says it in the direction it changed.

The cost of that is bounded on purpose, because this console runs for months on
a laptop that is also decoding two video streams:

* the animation runs only when the value actually moves, and stops itself when
  it arrives - there is no timer ticking behind a link that is holding steady;
* it repaints one widget about 28 px tall and nothing else;
* a change too small to see (under half a percent) is not animated at all, it is
  simply taken.

Everything is painted rather than assembled out of widgets. Three pieces of text
and a rectangle do not need four QLabels and a layout, and the panel above is
redrawn on a two-second heartbeat.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from vmd.desktop.style import MONO, PALETTE, SIZE_HEADING, SIZE_SMALL, WEIGHT_HEADING

# How long the fill takes to travel, and on what curve.
#
# Fast enough that it is over before it is a thing being watched, slow enough
# that the eye catches the direction. OutCubic and not a bounce: this is
# equipment, and a bar that overshoots the reading and comes back has, for a
# moment, shown a number the radio never said.
TRAVEL_MS = 420
CURVE = QEasingCurve.Type.OutCubic

# Below this the value is taken rather than travelled to. The radio is read
# every four seconds and its figures jitter by a fraction of a percent between
# readings; animating that is a bar that never stops moving, which is noise
# wearing the clothes of information.
STILL_ENOUGH = 0.5

BAR_HEIGHT = 8
TEXT_HEIGHT = 15
GAP = 4
UNKNOWN_CAPTION = "not reported"


class Meter(QWidget):
    """One figure, as a name, a bar and a small number.

    `set_reading` takes a percentage of the bar, the caption to put on the
    right, and a palette state name for the colour. A percentage of None is a
    figure the radio did not report - drawn as an empty track, never as zero,
    which is the same rule the sentences follow.
    """

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._caption = UNKNOWN_CAPTION
        self._target: float | None = None
        self._drawn = 0.0
        self._colour = PALETTE["muted"]
        self._marks: list[float] = []
        self.setFixedHeight(TEXT_HEIGHT + GAP + BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._travel = QVariantAnimation(self)
        self._travel.setDuration(TRAVEL_MS)
        self._travel.setEasingCurve(CURVE)
        self._travel.valueChanged.connect(self._step)
        # Counted so the tests can say the bar animates only when it has
        # somewhere to go. A meter that restarted its animation on every
        # heartbeat would be a timer running for ever behind a steady link.
        self.travels = 0

    def set_marks(self, marks: list[float]) -> None:
        """Where on the track the reading changes what it means.

        This is what makes the bar answer the question on its own. A bar that is
        70% full says nothing without knowing where 70% sits; a bar that is 70%
        full with a hairline at 60 says "past it" to somebody who has never
        heard of airtime, and it says it without a word or a colour - which is
        DESIGN.md's rule about colour never carrying meaning alone, honoured
        here by the shape rather than by another sentence.

        Set once, from the same constants the sentences use. They do not move.
        """
        self._marks = [value for value in marks if 0.0 < value < 100.0]
        self.update()

    def reading(self) -> tuple[float | None, str, str]:
        """The value, caption and colour on screen, for the tests and the panel."""
        return self._target, self._caption, self._colour

    def filled(self) -> float:
        """How full the bar is being drawn right now, 0-100.

        Mid-animation this is not the reading; that is the point of it, and it
        is why the caption beside it is never taken from here.
        """
        return self._drawn

    def set_reading(self, percent: float | None, caption: str, colour: str) -> None:
        caption = caption or UNKNOWN_CAPTION
        if (percent, caption, colour) == (self._target, self._caption, self._colour):
            return
        self._caption = caption
        self._colour = colour
        self._target = percent
        wanted = 0.0 if percent is None else max(0.0, min(100.0, percent))
        if abs(wanted - self._drawn) < STILL_ENOUGH:
            self._travel.stop()
            self._drawn = wanted
        else:
            self._travel.stop()
            self._travel.setStartValue(float(self._drawn))
            self._travel.setEndValue(float(wanted))
            self._travel.start()
            self.travels += 1
        self.update()

    def settle(self) -> None:
        """Finish any travel now. For the tests, and for a window being closed."""
        if self._travel.state() == QVariantAnimation.State.Running:
            self._drawn = float(self._travel.endValue())
            self._travel.stop()
            self.update()

    def _step(self, value) -> None:
        self._drawn = float(value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        width = self.width()

        font = QFont(self.font())
        font.setPixelSize(SIZE_HEADING)
        font.setWeight(QFont.Weight(WEIGHT_HEADING))
        painter.setFont(font)
        painter.setPen(QColor(PALETTE["muted"]))
        top = QRectF(0, 0, width, TEXT_HEIGHT)
        painter.drawText(top, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._name)

        # The number in the mono face the rest of the console uses for figures,
        # so a reading that changes does not shuffle the text beside it.
        caption = QFont(self.font())
        caption.setFamilies([family.strip().strip('"') for family in MONO.split(",")])
        caption.setPixelSize(SIZE_SMALL)
        painter.setFont(caption)
        painter.setPen(QColor(PALETTE["ink"] if self._target is not None else PALETTE["muted"]))
        painter.drawText(top, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         self._caption)

        track = QRectF(0, TEXT_HEIGHT + GAP, width, BAR_HEIGHT)
        painter.fillRect(track, QColor(PALETTE["bg"]))
        painter.setPen(QColor(PALETTE["line"]))
        painter.drawRect(track.adjusted(0, 0, -1, -1))
        if self._target is not None and self._drawn > 0:
            filled = QRectF(track)
            filled.setWidth(max(1.0, track.width() * self._drawn / 100.0))
            painter.fillRect(filled.adjusted(1, 1, -1, -1), QColor(self._colour))

        # The marks go on top of the fill rather than under it, so the one that
        # matters - the one the bar has already passed - is the one that is
        # visible. Drawn in the panel's own line colour and one pixel wide:
        # this is a scale, not a second reading, and a mark loud enough to
        # compete with the fill would be a third thing on the bar to interpret.
        painter.setPen(QColor(PALETTE["line_strong"]))
        for mark in self._marks:
            x = track.left() + track.width() * mark / 100.0
            painter.drawLine(int(x), int(track.top()), int(x), int(track.bottom()) - 1)
        painter.end()
