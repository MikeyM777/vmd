"""The zoom, under each picture, where it can be seen as well as commanded.

Two things were wrong and they are one thing. The console had a single zoom -
the up and down of one control, sent to whatever profile the camera happened to
hand back first - and this camera is two cameras: a thermal sensor and a visible
one behind their own lenses on a shared gimbal. Zooming "the camera" zoomed one
of them, and which one was not a decision anybody had made.

And there was no way to see where the zoom was. His words: "I want a slide that
shows the zoom length so I know when I'm fully zoomed, something more visual."
That is not a nicety on a camera watching a perimeter 700 m away. A lens that is
already at its limit and a lens that is not moving because the command was lost
look exactly the same through a picture that is not changing - and one of those
is a fault to chase and the other is the equipment doing its job.

So: one of these under each picture, always visible, in the normal view and in
fullscreen.

**What the slider shows is what the camera says, or nothing.** This is the
parser's rule and the link panel's, and it costs more here than it does there,
because a zoom slider that shows a position is trivially easy to fake - count
how long the button was held, call it a percentage, draw it. That number would
be right until the first command that did not arrive, and it would go on looking
right for ever afterwards, which is the exact failure this control exists to
make visible. A camera that does not report its zoom gets a slider that says so
and buttons that still work.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QWidget,
)

from vmd.desktop.style import (
    MONO,
    PALETTE,
    SIZE_HEADING,
    SIZE_SMALL,
    SPACE_SNUG,
    SPACE_TIGHT,
)

logger = logging.getLogger(__name__)

# The slider's scale. Not the camera's: ONVIF's zoom runs 0.0 to 1.0 and a
# hundred steps is finer than any lens on this link can be commanded to.
STEPS = 100

# How far a press of + or - asks the lens to travel, as a share of the whole
# range, when the camera can be told to go somewhere absolutely. Twenty steps of
# a 30x lens is about the smallest move worth making from a button; smaller than
# that and the operator is pressing it six times.
NUDGE = 5

# What a button held down means when the camera cannot be told where to go: keep
# zooming at this speed until the button comes up. ONVIF speeds are -1.0 to 1.0
# and this is deliberately slow - the picture is 700 m away over a radio link,
# and the round trip on the last measurement was two seconds. A fast zoom on a
# two-second feedback loop overshoots every time.
CREEP = 0.35

WIDE_WORDS = "wide"
TIGHT_WORDS = "tele"
UNKNOWN_CAPTION = "zoom not reported"

# How long after the operator last asked for a zoom the handle is left where he
# put it, whatever the camera says.
#
# The lens is still travelling for most of this. Readings that arrive while it
# is are where the lens WAS, and writing them into the handle drags it back out
# from under him - so it stays put until it has had a fair chance of being true.
# Longer than the settling window `vmd/ptz/lenses.py` reads over, so the last
# reading of a journey is the first one allowed to move the handle.
HOLD_AFTER_ASKING = 7.0

# What a reading is called. The readout used to be `42%` and nothing else - a
# per cent of what, under a picture, beside a slider and two buttons - and the
# word "zoom" appeared on this control in exactly one state, the one where it
# had failed. So it named itself only while it was not working, and the reading
# he might have to say out loud over a radio was a bare number. It is on every
# reading now, which also makes the three states one vocabulary rather than two.
ZOOM_WORD = "zoom"

# What the bar says between the console starting and the camera answering.
#
# These two are not the same state and must not read as the same state. Lens
# discovery happens on the worker thread, so for the first heartbeat or two of
# every morning there is genuinely no answer yet - and a bar that spends those
# two seconds saying "zoom not reported" has told the operator his camera is
# broken, every single day, before it works. The distinction costs one string
# and removes a fault he would have learned to ignore, which is worse than
# either state on its own.
CHECKING_CAPTION = "checking the lens"


def _reading(percent: float, edge: str = "") -> str:
    """One reading of the lens, named. `zoom  42%`, `zoom 100% tele`.

    The per cent is fixed at three columns and the caption is drawn in the mono
    face, so the number does not shuffle sideways between 9% and 10% under a
    picture somebody is watching.
    """
    return f"{ZOOM_WORD} {percent:3.0f}% {edge}".rstrip()


# Every string this caption can hold, for the one measurement that matters: the
# slider beside it must not change length as the lens moves. Measured across all
# of them rather than across the two fault captions, because naming the readings
# is exactly the change that could make a READING the longest of them.
CAPTIONS = (UNKNOWN_CAPTION, CHECKING_CAPTION, _reading(100, TIGHT_WORDS), _reading(0, WIDE_WORDS))


class ZoomBar(QWidget):
    """One camera's zoom: minus, a slider, plus, and where the lens actually is.

    Nothing here talks to a camera. It emits what was asked for and draws what
    it is told the answer was, which is what lets the whole control be tested
    without a lens - and what stops a lost command from being drawn as a
    successful one.
    """

    #: The operator asked for a specific zoom, as 0.0 (wide) to 1.0 (tele).
    go_to = Signal(str, float)
    #: The operator is holding a button: keep going at this speed, or stop at 0.
    creep = Signal(str, float)

    def __init__(
        self,
        name: str,
        absolute: bool = True,
        parent: QWidget | None = None,
        clock=time.monotonic,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._clock = clock
        self._absolute = absolute
        self._position: float | None = None
        # Set while the slider is being moved by code rather than by a person.
        # Without it, drawing the camera's answer would look exactly like a new
        # command and the two would chase each other round the link.
        self._echoing = False
        # When he last asked for a zoom, so a reading of where the lens used to
        # be cannot pull the handle out from under him. See HOLD_AFTER_ASKING.
        self._asked_at: float | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_SNUG)

        self._out = self._button("−", "Zoom out (wider view)")
        self._in = self._button("+", "Zoom in (closer view)")

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, STEPS)
        self._slider.setPageStep(NUDGE)
        self._slider.setSingleStep(1)
        self._slider.setToolTip("Where the lens is. Drag to send it somewhere.")
        self._slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._slider.setStyleSheet(_SLIDER_STYLE)
        # Two connections, and the split is the whole of why dragging this used
        # to feel wrong.
        #
        # `valueChanged` fires on every intermediate value. Dragging the handle
        # across the bar therefore asked the lens for sixty different zooms in a
        # second, at a camera whose replies were last measured at two seconds -
        # so the lens spent the drag chasing positions the operator had already
        # left, and arrived somewhere he had passed through rather than where he
        # let go. It is kept only for the ways of moving a slider that produce
        # one value and mean it: the arrow keys, the wheel, a click on the
        # groove. While the handle is held, it says nothing.
        #
        # `sliderReleased` is the drag: one command, at the value he stopped on.
        self._slider.valueChanged.connect(self._slid)
        self._slider.sliderReleased.connect(self._let_go)

        self._unknown = UNKNOWN_CAPTION
        self._unknown_tip = (
            "This camera does not report where its zoom is. The buttons still "
            "work; the slider cannot show a position that was never sent."
        )
        self._caption = QLabel(UNKNOWN_CAPTION)
        self._caption.setStyleSheet(
            f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;"
            f" font-family: {MONO};"
        )
        self._caption.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        row.addWidget(self._out)
        row.addWidget(self._slider, 1)
        row.addWidget(self._in)
        row.addWidget(self._caption)

        self.set_absolute(absolute)
        self.set_position(None)

    # ------------------------------------------------------------- the buttons

    def _button(self, text: str, tip: str) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tip)
        button.setFixedWidth(26)
        button.setAutoRepeat(False)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setStyleSheet(
            f"QPushButton {{ padding: {SPACE_TIGHT}px 0; font-size: {SIZE_HEADING}px; }}"
        )
        button.pressed.connect(lambda: self._pressed(1 if text == "+" else -1))
        button.released.connect(self._released)
        return button

    def _pressed(self, direction: int) -> None:
        if self._absolute and self._position is not None:
            target = _clamp(self._position + direction * NUDGE / STEPS)
            self.go_to.emit(self._name, target)
            return
        # No position to step from, so the only honest thing a button can do is
        # keep the lens moving for as long as it is held - which is what the
        # arrow keys already do for pan and tilt.
        self.creep.emit(self._name, direction * CREEP)

    def _released(self) -> None:
        if not (self._absolute and self._position is not None):
            self.creep.emit(self._name, 0.0)

    def _recently_asked(self) -> bool:
        """Whether he has touched this slider recently enough to still own it."""
        if self._asked_at is None:
            return False
        return (self._clock() - self._asked_at) < HOLD_AFTER_ASKING

    def _slid(self, value: int) -> None:
        if self._echoing:
            return
        if self._slider.isSliderDown():
            # Mid-drag. The handle follows the mouse, and the lens is told once,
            # when he lets go. See the two connections above.
            return
        self._asked_at = self._clock()
        self.go_to.emit(self._name, value / STEPS)

    def _let_go(self) -> None:
        """The end of a drag: one command, at the value he stopped on."""
        self._asked_at = self._clock()
        self.go_to.emit(self._name, self._slider.value() / STEPS)

    # -------------------------------------------------------------- the answer

    def set_position(self, position: float | None) -> None:
        """Draw where the camera says the lens is. None means it did not say.

        Never inferred from what was commanded. A lens that did not move because
        the command was lost has to look different from one that arrived, and
        the only thing that knows the difference is the camera.
        """
        self._position = None if position is None else _clamp(position)
        known = self._position is not None
        self._slider.setEnabled(known)
        # The handle is only moved when he is not the one moving it, and not for
        # a moment after he was.
        #
        # The lens takes seconds to travel and the console reads it back while
        # it does, so the readings arriving during and just after a drag are
        # where the lens WAS. Writing those into the handle pulled it backwards
        # out from under the mouse, and again a second after he let go - which
        # is a control that argues with the person using it, and is most of what
        # "make sure the slider is working accurately" is about.
        #
        # The caption underneath is not held back: that IS the camera's answer,
        # and watching it climb towards where he let go is the feedback that the
        # lens is on its way.
        if not self._slider.isSliderDown() and not self._recently_asked():
            self._echoing = True
            try:
                self._slider.setValue(0 if not known else round(self._position * STEPS))
            finally:
                self._echoing = False
        if not known:
            self._say(self._unknown)
            self._caption.setToolTip(self._unknown_tip)
            self._caption.setStyleSheet(
                f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;"
                f" font-family: {MONO};"
            )
            return
        percent = self._position * 100.0
        edge = WIDE_WORDS if percent <= 1 else (TIGHT_WORDS if percent >= 99 else "")
        self._say(_reading(percent, edge))
        self._caption.setToolTip("")
        self._caption.setStyleSheet(
            f"color: {PALETTE['ink'] if edge else PALETTE['muted']};"
            f" font-size: {SIZE_SMALL}px; font-family: {MONO};"
        )

    def _say(self, words: str) -> None:
        """Put a caption up, and keep the room it takes the same either way.

        The width is worked out here rather than once at construction, and it
        has to be: the caption's face and size come from a stylesheet, and a
        stylesheet is resolved when the widget is polished - which is after the
        constructor has run. Measured there, every string came out at the
        default face's width and the guarantee this exists for was not kept:
        with the readings named, the slider moved by twenty pixels between
        "zoom not reported" and "zoom  42%", under a picture somebody is
        watching, every time the camera answered.
        """
        self._caption.setText(words)
        self._caption.setMinimumWidth(
            max(
                self._caption.fontMetrics().horizontalAdvance(other)
                for other in CAPTIONS
            )
        )

    def set_checking(self, checking: bool) -> None:
        """Whether the camera has simply not answered yet.

        Two states that look identical on a bar with no position in it, and are
        not the same thing at all: nobody has asked the camera yet, and the
        camera was asked and said it has no zoom to report. The first lasts a
        heartbeat or two after every start-up and is not a fault; drawn as the
        second, it is a fault the operator sees every morning and learns to
        ignore - and a warning somebody has learned to ignore is worse than no
        warning, because the day it is real it looks the same.
        """
        self._unknown = CHECKING_CAPTION if checking else UNKNOWN_CAPTION
        self._unknown_tip = (
            "Waiting for the camera to say what its zoom can do."
            if checking
            else "This camera does not report where its zoom is. The buttons still "
            "work; the slider cannot show a position that was never sent."
        )
        if self._position is None:
            self.set_position(None)

    def set_absolute(self, absolute: bool) -> None:
        """Whether this camera can be told to go to a zoom, or only to move."""
        self._absolute = bool(absolute)
        self._slider.setToolTip(
            "Where the lens is. Drag to send it somewhere."
            if self._absolute
            else "Where the lens is. This camera can only be zoomed with the buttons."
        )

    # ------------------------------------------------------------- for testing

    def name(self) -> str:
        return self._name

    def position(self) -> float | None:
        return self._position

    def caption(self) -> str:
        return self._caption.text()

    def slider(self) -> QSlider:
        return self._slider

    def buttons(self) -> tuple[QPushButton, QPushButton]:
        """Zoom out, zoom in."""
        return self._out, self._in


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


# Slim, and the handle is the only thing on it with any weight: this sits under
# a picture and must not compete with it. Square, like everything else here.
_SLIDER_STYLE = f"""
QSlider::groove:horizontal {{
    height: 4px;
    background: {PALETTE["bg"]};
    border: 1px solid {PALETTE["line"]};
}}
QSlider::sub-page:horizontal {{
    background: {PALETTE["line_strong"]};
    border: 1px solid {PALETTE["line"]};
}}
QSlider::handle:horizontal {{
    background: {PALETTE["ink"]};
    border: 0;
    width: 8px;
    margin: -5px 0;
}}
QSlider::handle:horizontal:hover {{ background: {PALETTE["accent"]}; }}
QSlider::groove:horizontal:disabled {{ border-color: {PALETTE["surface"]}; }}
QSlider::handle:horizontal:disabled {{ background: {PALETTE["line"]}; }}
"""
