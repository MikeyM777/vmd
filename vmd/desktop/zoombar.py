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

    def __init__(self, name: str, absolute: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._absolute = absolute
        self._position: float | None = None
        # Set while the slider is being moved by code rather than by a person.
        # Without it, drawing the camera's answer would look exactly like a new
        # command and the two would chase each other round the link.
        self._echoing = False

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
        self._slider.valueChanged.connect(self._slid)

        self._caption = QLabel(UNKNOWN_CAPTION)
        self._caption.setStyleSheet(
            f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;"
            f" font-family: {MONO};"
        )
        # Wide enough for the longest thing it ever says, so the slider beside
        # it does not change length every time the lens moves.
        self._caption.setMinimumWidth(
            self._caption.fontMetrics().horizontalAdvance(UNKNOWN_CAPTION)
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

    def _slid(self, value: int) -> None:
        if self._echoing:
            return
        self.go_to.emit(self._name, value / STEPS)

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
        self._echoing = True
        try:
            self._slider.setValue(0 if not known else round(self._position * STEPS))
        finally:
            self._echoing = False
        if not known:
            self._caption.setText(UNKNOWN_CAPTION)
            self._caption.setToolTip(
                "This camera does not report where its zoom is. The buttons still "
                "work; the slider cannot show a position that was never sent."
            )
            return
        percent = self._position * 100.0
        edge = WIDE_WORDS if percent <= 1 else (TIGHT_WORDS if percent >= 99 else "")
        self._caption.setText(f"{percent:3.0f}% {edge}".rstrip())
        self._caption.setToolTip("")
        self._caption.setStyleSheet(
            f"color: {PALETTE['ink'] if edge else PALETTE['muted']};"
            f" font-size: {SIZE_SMALL}px; font-family: {MONO};"
        )

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
