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

**And it has to answer while he is still moving it.** "There are a lot of delay
in the zoom sliders." Two seconds of that is the radio link and cannot be fixed
here; the rest was this file waiting on purpose - a drag that told the lens
nothing until it was released, a button that stepped once per press, and a
handle frozen for seven seconds after every command whether or not the lens had
already arrived. Those three are gone. What they were protecting against is not:
see DRAG_EVERY_SECONDS, REPEAT_EVERY_SECONDS and CAUGHT_UP_STEPS, each of which
keeps the failure its predecessor was written for.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt, QTimer, Signal
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

# How often a drag is allowed to reach the lens while the handle is still held.
#
# It used to be never: the whole drag said nothing and one command went out on
# release, so the lens did not begin to move until he let go and then the picture
# changed after he had stopped. His words were "there are a lot of delay in the
# zoom sliders", and most of that delay was this control waiting rather than the
# two seconds the radio link costs.
#
# What the old rule was protecting against is real but was never what it looked
# like. `valueChanged` fires on every intermediate value, so a drag across the
# bar produced sixty `go_to` signals a second - but `PtzCommands` (see
# `vmd/ptz/service.py`) is a LATEST-VALUE mailbox with one lane per lens, so
# those sixty were never sixty on the wire: a newer zoom for a lens replaces an
# older one that has not been sent yet, and at most one zoom command per lens is
# ever in flight. The cost was on this side of the mailbox - sixty signals a
# second, and a lens repeatedly redirected towards positions the operator had
# already passed through.
#
# A quarter of a second keeps that property and buys back the dead drag: four or
# five commands across a second-long drag, each one a place he was actually
# pointing at, and the lens sets off while he is still moving. Timed off the
# injected clock and never off `time.monotonic` directly, so the tests that
# drive this with a fake clock stay deterministic.
DRAG_EVERY_SECONDS = 0.25

# How often a held + or - steps again, on a camera that can be told where to go.
#
# Deliberately not `QPushButton.setAutoRepeat`: that rate is a platform setting
# chosen for text cursors, and this is a lens 700 m away whose round trip was
# last measured at two seconds. Getting from wide to tele used to be a dozen
# separate presses because a press stepped exactly once.
REPEAT_EVERY_SECONDS = 0.6

# How close a reading has to be to what was last asked for before the reading is
# believed again. Two steps of a hundred, which is finer than any lens on this
# link settles to. See HOLD_AFTER_ASKING.
CAUGHT_UP_STEPS = 2

WIDE_WORDS = "wide"
TIGHT_WORDS = "tele"
UNKNOWN_CAPTION = "zoom not reported"

# The longest the handle is left where he put it, whatever the camera says.
#
# The lens is still travelling for most of this. Readings that arrive while it
# is are where the lens WAS, and writing them into the handle drags it back out
# from under him - so it stays put until it has had a fair chance of being true.
# Longer than the settling window `vmd/ptz/lenses.py` reads over, so the last
# reading of a journey is the first one allowed to move the handle.
#
# An upper bound and not a fixed wait. The hold ends the moment a reading agrees
# with what was asked for, because at that point the lens has arrived and every
# reading after it is the truth - ignoring those for another five seconds is a
# handle that has stopped listening to a camera that is answering correctly, and
# it made the whole control feel a beat behind the picture. See CAUGHT_UP_STEPS.
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
        # The last place the lens was ASKED to go, which is not the last place
        # it was seen to be. Two things need it: the held button steps from it,
        # and a reading is compared against it to find out whether the lens has
        # arrived and the hold above can end.
        self._target: float | None = None
        # When the last command of a drag went out, and what it carried. The
        # first is the quarter-second throttle; the second is what stops the
        # release repeating a value the throttle has just sent. Both cleared on
        # release, so every drag starts by sending at once.
        self._drag_sent_at: float | None = None
        self._drag_sent: int | None = None
        # Whether the press being held emitted a creep. Read at release instead
        # of asking the camera again, because the camera's answer can change
        # while the button is down and a creep must be ended by a stop whatever
        # it has since said. See `_released`.
        self._crept = False
        # Which way a held button is stepping, and the timer that keeps it
        # stepping. The timer is parented to this widget on purpose: a repeat
        # that outlived the bar would go on commanding a lens through a widget
        # that is no longer on the screen, and a child QTimer is destroyed with
        # its parent.
        self._direction = 0
        self._repeat = QTimer(self)
        self._repeat.setInterval(int(REPEAT_EVERY_SECONDS * 1000))
        self._repeat.timeout.connect(self._step_again)

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
        # Two connections, and the split is the whole of how dragging this
        # feels.
        #
        # `valueChanged` fires on every intermediate value, which for the ways
        # of moving a slider that produce one value and mean it - the arrow
        # keys, the wheel, a click on the groove - is exactly one command. While
        # the handle is held it is throttled instead of silenced: the lens is
        # steered at the value under the mouse, at most once every quarter of a
        # second. See DRAG_EVERY_SECONDS for why sixty a second was wrong and
        # why four or five is right.
        #
        # `sliderReleased` is the end of the drag, and it always sends, so the
        # lens finishes at the value he let go on and not at the last one the
        # throttle happened to let through.
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
            # Held, not tapped. A press used to step exactly once, so getting a
            # 30x lens from wide to tele meant pressing the button a dozen
            # times; it steps now for as long as it is held.
            #
            # The first step is sent immediately rather than after the first
            # interval, because a button that does nothing for six tenths of a
            # second reads as a button that was not pressed.
            self._direction = direction
            # Seeded from the camera's reading, and only here. Every step after
            # this one is measured from the last target instead, which is the
            # crux of it: a reading lags the lens by seconds, so stepping from
            # the reading would ask for very nearly the same place over and over
            # and the zoom would crawl while the button was held down.
            self._target = self._position
            self._crept = False
            # Started before the first step and not after it, so that a step
            # landing on the end of the travel can stop the repeat it is inside.
            self._repeat.start()
            self._step()
            return
        # No position to step from, so the only honest thing a button can do is
        # keep the lens moving for as long as it is held - which is what the
        # arrow keys already do for pan and tilt.
        #
        # Remembered rather than worked out again at release. See `_released`.
        self._crept = True
        self.creep.emit(self._name, direction * CREEP)

    def _released(self) -> None:
        # Stopped first and unconditionally. A repeat that survived the button
        # coming up is a lens that goes on zooming with nothing held, which is
        # the zoom's version of the fault `PtzCommands` guards the head against.
        self._repeat.stop()
        self._direction = 0
        # What the press DID, not what the camera would answer now.
        #
        # These two used to decide independently, each reading the camera's
        # current answer, and the answer changes while the button is down
        # because the lens is polled throughout. A camera that started reporting
        # a position mid-hold therefore got a creep from the press and nothing
        # at all from the release - a lens still travelling with no button held,
        # and nothing coming to stop it, which is the one outcome this file is
        # not allowed to produce. The mirror case sent a stop for a creep that
        # was never started. A press that crept is ended by a stop, always.
        if self._crept:
            self._crept = False
            self.creep.emit(self._name, 0.0)

    def _step(self) -> None:
        """One step of a held button, from the last target and not the reading."""
        base = self._target if self._target is not None else 0.0
        self._ask(base + self._direction * NUDGE / STEPS)
        if self._at_the_stop():
            # There is nowhere further to ask for, so every repeat from here is
            # the same no-op sent again. Harmless on the picture and not on the
            # link: this lens's lane in `PtzCommands` would never be empty for
            # as long as the finger was down, and a stop for the head is only
            # allowed to wait behind ONE zoom already on the wire.
            self._repeat.stop()

    def _at_the_stop(self) -> bool:
        """Whether the target has reached the end of the travel it is heading for."""
        if self._target is None:
            return False
        return (self._direction > 0 and self._target >= 1.0) or (
            self._direction < 0 and self._target <= 0.0
        )

    def _step_again(self) -> None:
        if not (self._absolute and self._position is not None):
            # The camera stopped reporting where the lens is while the button
            # was down. Stepping needs somewhere to step from and there is no
            # longer an honest one, so the repeat ends rather than carrying on
            # from a number nothing has confirmed since.
            self._repeat.stop()
            return
        self._step()

    def _ask(self, where: float) -> None:
        """Ask the lens to go somewhere, and remember that it was asked.

        The single door out of this widget for an absolute zoom, so that the
        target and the hold cannot be updated by one path and not another.
        """
        self._target = _clamp(where)
        self._asked_at = self._clock()
        self.go_to.emit(self._name, self._target)

    def _recently_asked(self) -> bool:
        """Whether he has touched this slider recently enough to still own it."""
        if self._asked_at is None:
            return False
        return (self._clock() - self._asked_at) < HOLD_AFTER_ASKING

    def _caught_up(self) -> bool:
        """Whether the reading just in agrees with what was last asked for.

        Compared in the slider's own steps rather than in the raw floats, so
        that the tolerance means what it says. A lens does not stop on the exact
        hundredth it was sent to, and two thirds of a per cent of arithmetic
        error is not a lens that has failed to arrive.
        """
        if self._target is None or self._position is None:
            return False
        return abs(round(self._position * STEPS) - round(self._target * STEPS)) <= CAUGHT_UP_STEPS

    def _slid(self, value: int) -> None:
        if self._echoing:
            return
        if self._slider.isSliderDown():
            # Mid-drag, and throttled rather than silent. `PtzCommands` keeps
            # only the latest command per lens, so these do not queue on the
            # wire; the quarter second is what stops the lens being redirected
            # at every pixel the mouse crosses. See DRAG_EVERY_SECONDS.
            now = self._clock()
            if (
                self._drag_sent_at is not None
                and (now - self._drag_sent_at) < DRAG_EVERY_SECONDS
            ):
                return
            self._drag_sent_at = now
            self._drag_sent = value
        self._ask(value / STEPS)

    def _let_go(self) -> None:
        """The end of a drag: a command at the value he stopped on.

        Unthrottled, because this is the only value of the whole gesture he
        actually chose - and skipped when the throttle happened to let that
        exact value through a moment ago, because sending it twice says nothing
        the first one did not. The mailbox absorbs the duplicate either way; it
        is the count of commands per gesture that this control is judged on, and
        a log where every drag ends with the same line twice is one nobody can
        read.

        Both throttle marks are cleared whatever happens, so the next drag sends
        at once rather than waiting out the tail of this one. Two drags in quick
        succession are two intentions, and the second is usually the correction.
        """
        value = self._slider.value()
        unchanged = value == self._drag_sent
        self._drag_sent_at = None
        self._drag_sent = None
        if unchanged:
            return
        self._ask(value / STEPS)

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
        if self._caught_up():
            # The lens has arrived where it was asked to go, so the hold below
            # has nothing left to protect: every reading from here is where the
            # lens IS, not where it was on the way. Holding on for the rest of
            # the seven seconds would leave the handle ignoring a camera that is
            # answering correctly, which is most of what "a lot of delay in the
            # zoom sliders" was on a link that happened to be behaving.
            self._asked_at = None
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

    def repeat_timer(self) -> QTimer:
        """What keeps a held button stepping, so a test can see it stop."""
        return self._repeat


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
