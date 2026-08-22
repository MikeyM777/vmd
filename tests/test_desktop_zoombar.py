"""The zoom control under each picture.

Two faults, and they are the same fault. The console had one zoom for a camera
that is two cameras - a thermal sensor and a visible one, each behind its own
lens on a shared gimbal - so zooming "the camera" zoomed whichever profile the
device handed back first. And there was nothing on the screen saying where the
zoom was, which on a perimeter 700 m away matters more than it sounds: a lens
already at its limit and a lens that never got the command look identical
through a picture that is not changing.

The thing being guarded here is the shortcut. A zoom slider that shows a
position is trivially easy to fake - count how long the button was held, call it
a percentage, draw it - and that number is right until the first command that
does not arrive, after which it is wrong for ever and still looks right. So the
tests below say, in several ways, that this control draws what the camera
reported and never what it asked for.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from shiboken6 import isValid

from vmd.desktop.zoombar import (
    CAUGHT_UP_STEPS,
    CHECKING_CAPTION,
    DRAG_EVERY_SECONDS,
    HOLD_AFTER_ASKING,
    CREEP,
    NUDGE,
    REPEAT_EVERY_SECONDS,
    STEPS,
    UNKNOWN_CAPTION,
    ZoomBar,
)


def commands(bar: ZoomBar) -> tuple[list, list]:
    """Everything the bar asks for, in order: absolute moves and creeps."""
    went: list = []
    crept: list = []
    bar.go_to.connect(lambda name, where: went.append((name, where)))
    bar.creep.connect(lambda name, speed: crept.append((name, speed)))
    return went, crept


# --------------------------------------------------------------- what it shows


def test_it_shows_where_the_camera_says_the_lens_is(qtbot) -> None:
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.set_position(0.42)
    assert bar.position() == 0.42
    assert "42" in bar.caption()
    assert bar.slider().value() == round(0.42 * STEPS)


def test_a_camera_that_does_not_report_its_zoom_says_so(qtbot) -> None:
    """The whole point of the control. A slider sitting at zero because nothing
    was reported has told the operator the lens is fully wide."""
    bar = ZoomBar("thermal")
    qtbot.addWidget(bar)
    bar.set_position(None)
    assert bar.position() is None
    assert bar.caption() == UNKNOWN_CAPTION
    assert not bar.slider().isEnabled(), "a slider that cannot show a position may not pretend"


def test_the_buttons_still_work_when_the_position_is_unknown(qtbot) -> None:
    """Not reporting a position is not the same as not zooming. The lens moves;
    it is the readout that is missing, and losing the buttons over that would
    cost the operator the zoom entirely."""
    bar = ZoomBar("thermal")
    qtbot.addWidget(bar)
    bar.set_position(None)
    out, into = bar.buttons()
    assert out.isEnabled() and into.isEnabled()
    _went, crept = commands(bar)
    into.pressed.emit()
    into.released.emit()
    assert crept == [("thermal", CREEP), ("thermal", 0.0)]


def test_not_asked_yet_does_not_read_as_a_camera_with_no_zoom(qtbot) -> None:
    """Lens discovery happens on the worker, so for the first heartbeat or two
    of every morning there is genuinely no answer yet. Drawn as "zoom not
    reported" that is a fault he sees every single day before the console
    works - and a warning somebody has learned to ignore is worse than no
    warning, because the day it is real it looks exactly the same."""
    bar = ZoomBar("thermal")
    qtbot.addWidget(bar)
    bar.set_checking(True)
    bar.set_position(None)
    assert bar.caption() == CHECKING_CAPTION
    assert bar.caption() != UNKNOWN_CAPTION

    bar.set_checking(False)
    assert bar.caption() == UNKNOWN_CAPTION


def test_the_camera_answering_replaces_the_waiting_words_at_once(qtbot) -> None:
    """Without waiting for another heartbeat. The two states are a beat apart
    and a stale caption between them is the confusion this removes."""
    bar = ZoomBar("thermal")
    qtbot.addWidget(bar)
    bar.set_checking(True)
    bar.set_position(None)
    bar.set_checking(False)
    assert bar.caption() == UNKNOWN_CAPTION


def test_waiting_words_never_appear_over_a_position_the_camera_did_give(qtbot) -> None:
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.set_position(0.5)
    bar.set_checking(True)
    assert "50" in bar.caption(), bar.caption()


def test_the_caption_stays_the_same_width_whichever_of_the_two_it_says(qtbot) -> None:
    """Both appear under a picture beside a slider. If one of them is wider the
    slider changes length as the camera answers, which twitches the frame."""
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.resize(400, 24)
    bar.set_checking(True)
    checking = bar.slider().width()
    bar.set_checking(False)
    assert bar.slider().width() == checking


def test_the_ends_of_the_travel_are_named_and_not_only_numbered(qtbot) -> None:
    """"I want to know when I'm fully zoomed." 100% is a number somebody has to
    interpret; "tele" is the answer to the question he actually asked."""
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.set_position(1.0)
    assert "tele" in bar.caption()
    bar.set_position(0.0)
    assert "wide" in bar.caption()
    bar.set_position(0.5)
    assert "tele" not in bar.caption() and "wide" not in bar.caption()


def test_a_position_outside_the_range_is_brought_back_onto_the_slider(qtbot) -> None:
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.set_position(1.6)
    assert bar.position() == 1.0
    bar.set_position(-0.2)
    assert bar.position() == 0.0


# ------------------------------------------------------------ what it asks for


def test_dragging_the_slider_asks_for_that_zoom_on_that_camera(qtbot) -> None:
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.set_position(0.1)
    went, _crept = commands(bar)
    bar.slider().setValue(70)
    assert went == [("visible", 0.7)]


def test_drawing_the_cameras_answer_is_not_a_new_command(qtbot) -> None:
    """The loop this would otherwise make: the camera reports 0.42, the slider
    moves, the slider's movement is read as a command, the camera is told to go
    to 0.42, which it reports, for ever - across a radio link whose round trip
    was last measured at two seconds."""
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    went, crept = commands(bar)
    for position in (0.2, 0.4, 0.6, 0.8):
        bar.set_position(position)
    assert went == [] and crept == []


def test_a_button_steps_from_where_the_lens_actually_is(qtbot) -> None:
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.set_position(0.50)
    went, _crept = commands(bar)
    _out, into = bar.buttons()
    into.pressed.emit()
    assert went == [("visible", 0.5 + NUDGE / STEPS)]


def test_a_button_at_the_end_of_the_travel_does_not_ask_for_more(qtbot) -> None:
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.set_position(1.0)
    went, _crept = commands(bar)
    _out, into = bar.buttons()
    into.pressed.emit()
    assert went == [("visible", 1.0)], "a lens cannot be told to go past its stop"


def test_a_camera_that_cannot_be_sent_anywhere_is_zoomed_by_holding(qtbot) -> None:
    """Some cameras answer GetStatus with a zoom and refuse AbsoluteMove. The
    buttons fall back to what every ONVIF camera can do: move while held."""
    bar = ZoomBar("visible", absolute=False)
    qtbot.addWidget(bar)
    bar.set_position(0.4)
    _went, crept = commands(bar)
    out, _into = bar.buttons()
    out.pressed.emit()
    out.released.emit()
    assert crept == [("visible", -CREEP), ("visible", 0.0)]


def test_each_camera_asks_for_its_own_zoom_and_not_the_others(qtbot) -> None:
    """The fault this replaces: one zoom control on a camera with two lenses."""
    thermal = ZoomBar("thermal")
    visible = ZoomBar("visible")
    qtbot.addWidget(thermal)
    qtbot.addWidget(visible)
    thermal.set_position(0.2)
    visible.set_position(0.2)
    asked: list = []
    thermal.go_to.connect(lambda name, where: asked.append((name, where)))
    visible.go_to.connect(lambda name, where: asked.append((name, where)))
    thermal.slider().setValue(90)
    assert asked == [("thermal", 0.9)]
    assert visible.slider().value() == round(0.2 * STEPS), "the other lens moved"


# -------------------------------------------------------------------- the look


def test_it_is_short_enough_to_sit_under_a_picture(qtbot) -> None:
    """It is under the video in the normal view and in fullscreen, where every
    pixel it takes is a pixel of picture. A control as tall as a toolbar would
    have been left out of fullscreen, which is where he asked for it."""
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.resize(400, bar.sizeHint().height())
    assert bar.sizeHint().height() <= 30, bar.sizeHint()


def test_the_caption_does_not_change_width_as_the_lens_moves(qtbot) -> None:
    """A number beside a slider that resizes the slider every time it changes is
    a picture that twitches under the video for as long as anybody is zooming."""
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.resize(400, 24)
    bar.set_position(0.05)
    narrow = bar.slider().width()
    bar.set_position(1.0)
    assert bar.slider().width() == narrow


def test_the_buttons_never_take_the_keyboard_away_from_steering(qtbot) -> None:
    """The arrow keys steer the camera. A + button that took focus would send
    the next arrow press to the button instead of to the gimbal."""
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    for button in bar.buttons():
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_the_slider_never_takes_the_keyboard_away_from_steering_either(qtbot) -> None:
    """The same rule as the buttons above, on the control that was missed.

    A focused QSlider consumes Left and Right to change its own value, so the
    moment the operator touched the zoom his arrow keys stopped steering the
    camera and started zooming it instead - silently, and against every
    instruction on the tab, which say the arrows pan and tilt. The buttons were
    given `NoFocus` for exactly this reason and the slider was not.
    """
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    assert bar.slider().focusPolicy() == Qt.FocusPolicy.NoFocus


def test_a_slider_that_refuses_the_keyboard_can_still_be_dragged(qtbot) -> None:
    """Refusing focus must not cost the gesture the control exists for.

    `NoFocus` is about the keyboard and nothing else - a widget that will not
    take focus still takes mouse presses - but the drag is the whole of how this
    control feels, so it is pressed, moved and released with a real mouse here
    rather than by calling `setValue`.
    """
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.set_position(0.0)
    bar.resize(400, 24)
    bar.show()
    qtbot.waitExposed(bar)
    went, _crept = commands(bar)

    slider = bar.slider()
    middle = QPoint(slider.width() // 2, slider.height() // 2)
    far = QPoint(int(slider.width() * 0.8), slider.height() // 2)
    qtbot.mousePress(slider, Qt.MouseButton.LeftButton, pos=middle)
    assert slider.isSliderDown(), "the press did not start a drag"
    qtbot.mouseMove(slider, far)
    qtbot.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=far)

    assert went, "a whole drag reached the lens with nothing"
    assert went[-1][0] == "visible"
    assert went[-1][1] == pytest.approx(slider.value() / STEPS), (
        "the lens did not finish at the value he let go on"
    )


def test_a_button_that_refuses_the_keyboard_still_repeats_while_it_is_held(
    qtbot,
) -> None:
    """The other half of the same worry, on the controls that already had it.

    The buttons have refused focus since they were written, and they have since
    been given a repeat while held. Pressed with a real mouse rather than by
    emitting `pressed`, so that a focus policy which stopped the press arriving
    at all would be caught here rather than in the field.
    """
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.set_position(0.5)
    bar.resize(400, 24)
    bar.show()
    qtbot.waitExposed(bar)
    went, _crept = commands(bar)

    _out, into = bar.buttons()
    qtbot.mousePress(into, Qt.MouseButton.LeftButton)
    assert went, "a press of + asked for nothing"
    assert bar.repeat_timer().isActive(), "a held button is not stepping"
    qtbot.mouseRelease(into, Qt.MouseButton.LeftButton)
    assert not bar.repeat_timer().isActive(), "the repeat outlived the button"


def test_the_readout_names_what_it_is_a_number_of(qtbot) -> None:
    """`42%` on its own, under a picture, beside a slider and two buttons.

    A per cent of what. The word "zoom" appeared on this control in exactly one
    state - `zoom not reported` - so it named itself only while it was not
    working, and the one reading he might have to say out loud over a radio was
    a bare number. Every reading carries the noun now, in all three states, and
    the ends of the travel keep the words they had: "tele" and "wide" are the
    answer to the question he actually asked.
    """
    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    for where in (0.0, 0.42, 1.0):
        bar.set_position(where)
        said = bar.caption().lower()
        assert "zoom" in said, said
        assert "%" in said, said
    bar.set_position(1.0)
    assert "tele" in bar.caption().lower()
    bar.set_position(0.0)
    assert "wide" in bar.caption().lower()


def test_naming_the_readout_did_not_make_it_change_width(qtbot) -> None:
    """The noun is worth nothing if the slider beside it moves when it appears.

    Measured across every string this caption can hold, not only across the two
    it used to be measured across, because that is the assumption the noun could
    quietly break: the widest reading is now a position and not a fault.
    """
    from vmd.desktop.zoombar import CHECKING_CAPTION, UNKNOWN_CAPTION

    bar = ZoomBar("visible")
    qtbot.addWidget(bar)
    bar.resize(400, 24)

    def slider_width() -> int:
        # Laid out before it is measured. Without this the widths are whatever
        # they were when the bar was resized, and the measurement would pass
        # however the caption was sized - which is the shape of a test that
        # cannot fail.
        bar.layout().activate()
        return bar.slider().width()

    widths = set()
    for where in (0.0, 0.05, 0.5, 0.999, 1.0):
        bar.set_position(where)
        widths.add(slider_width())
    for checking in (True, False):
        bar.set_position(None)
        bar.set_checking(checking)
        widths.add(slider_width())
    assert len(widths) == 1, (
        f"the slider is {sorted(widths)} px wide depending on what the caption "
        f"says; the longest are {UNKNOWN_CAPTION!r} and {CHECKING_CAPTION!r}"
    )


# ------------------------------------------- the drag, and what it costs the link
#
# "Make sure the slider is working accurately." Two things made it inaccurate,
# and neither was the arithmetic.


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


def test_dragging_steers_the_lens_without_flooding_it(qtbot) -> None:
    """`valueChanged` fires on every intermediate value. Dragging the handle
    across the bar therefore asked for sixty different zooms in a second, at a
    camera whose replies were last measured at two seconds - so the lens spent
    the drag chasing positions he had already left, and stopped somewhere he had
    passed through rather than where he let go.

    Sending none of them was the wrong answer to that, and it is what he
    complained about: the lens did not begin to move until he let go, so the
    whole drag felt dead and the picture changed after he had stopped. A few is
    right. `PtzCommands` keeps only the latest zoom per lens, so a command that
    has not gone out yet is replaced rather than queued and the wire carries at
    most one whatever the slider does; the quarter second is what stops the lens
    being redirected at every pixel the mouse crosses. Four or five places he
    was really pointing at, and the last one is where he let go.
    """
    clock = Clock()
    bar = ZoomBar("visible", clock=clock)
    qtbot.addWidget(bar)
    bar.set_position(0.1)
    went, _crept = commands(bar)

    bar.slider().setSliderDown(True)
    for value in range(20, 80):           # the drag: sixty values across a second
        bar.slider().setValue(value)
        clock.tick(1.0 / 60.0)

    # Letting go. `setSliderDown(False)` is what Qt emits `sliderReleased` from,
    # so this is the real gesture and not a signal poked by hand.
    bar.slider().setSliderDown(False)

    # Pinned at both ends and written out as numbers, because the loose end is
    # the one this change was about: "at most a few" alone is satisfied by
    # sending almost nothing, which is exactly the dead drag being fixed. A
    # second of dragging is four commands from the throttle and one from the
    # release; five is what should come out and six leaves room for a boundary.
    assert 4 <= len(went) <= 6, f"{len(went)} commands for a one-second drag: {went}"
    assert int(1.0 / DRAG_EVERY_SECONDS) == 4, "the bounds above are read off this"
    assert went[-1] == ("visible", 0.79), went[-1]
    wheres = [where for _name, where in went]
    assert wheres == sorted(wheres), f"a drag one way asked for zooms the other way: {wheres}"
    assert all(name == "visible" for name, _where in went)


def test_a_drag_with_no_time_in_it_is_still_one_command(qtbot) -> None:
    """The throttle is a clock and not a counter. Sixty values arriving inside
    one tick is the shape of the flood the old rule was written against, and it
    has to come out as one command however many values Qt emits."""
    bar = ZoomBar("visible", clock=Clock())   # never ticked
    qtbot.addWidget(bar)
    bar.set_position(0.1)
    went, _crept = commands(bar)

    bar.slider().setSliderDown(True)
    for value in range(20, 80):
        bar.slider().setValue(value)
    assert len(went) == 1, f"{len(went)} commands sent within one instant: {went}"


def test_a_click_on_the_groove_still_asks_at_once(qtbot) -> None:
    """The ways of moving a slider that produce one value and mean it - the
    arrow keys, the wheel, a click on the groove - must not wait for a release
    that is never coming."""
    bar = ZoomBar("visible", clock=Clock())
    qtbot.addWidget(bar)
    bar.set_position(0.1)
    went, _crept = commands(bar)
    bar.slider().setValue(70)
    assert went == [("visible", 0.7)]


def test_the_handle_is_not_dragged_out_from_under_him(qtbot) -> None:
    """The lens takes seconds to travel and the console reads it back while it
    does, so a reading arriving mid-drag is where the lens WAS. Writing that into
    the handle pulls it backwards under the mouse."""
    bar = ZoomBar("visible", clock=Clock())
    qtbot.addWidget(bar)
    bar.set_position(0.10)

    bar.slider().setSliderDown(True)
    bar.slider().setValue(80)
    bar.set_position(0.12)               # the lens, still back where it started
    assert bar.slider().value() == 80, "the handle jumped back mid-drag"


def test_a_stale_reading_does_not_snap_the_handle_back_after_he_lets_go(
    qtbot,
) -> None:
    """The same thing a second later, which is worse: he has stopped, the handle
    is where he wants it, and then it moves on its own."""
    clock = Clock()
    bar = ZoomBar("visible", clock=clock)
    qtbot.addWidget(bar)
    bar.set_position(0.10)

    bar.slider().setSliderDown(True)
    bar.slider().setValue(80)
    bar.slider().setSliderDown(False)

    clock.tick(2.0)
    bar.set_position(0.15)               # still travelling
    assert bar.slider().value() == 80, "the handle was pulled back while travelling"

    clock.tick(HOLD_AFTER_ASKING)
    bar.set_position(0.80)               # arrived, and now believed
    assert bar.slider().value() == 80


def test_the_reading_underneath_is_never_held_back(qtbot) -> None:
    """The caption IS the camera's answer, and watching it climb towards where
    he let go is the only sign the lens is on its way. Freezing that with the
    handle would leave him with no feedback at all."""
    clock = Clock()
    bar = ZoomBar("visible", clock=clock)
    qtbot.addWidget(bar)
    bar.set_position(0.10)
    bar.slider().setSliderDown(True)
    bar.slider().setValue(80)

    bar.set_position(0.34)
    assert "34" in bar.caption(), bar.caption()


# ------------------------------------------------- holding the buttons down
#
# "Zoom from wide to tele" used to be a dozen separate presses, because a press
# stepped exactly once on a camera that reports where its lens is.


def test_holding_the_button_keeps_stepping(qtbot) -> None:
    """And each step is further than the last, which is the whole difficulty.

    The camera below reports the SAME position throughout - which is what a
    camera on this link genuinely does while a lens is travelling, because the
    reading lags the lens by seconds. Stepping from the reading would therefore
    ask for the same place over and over and the zoom would crawl on the screen
    while the operator held the button down. Each step is measured from the last
    target instead, so the repeats walk away from where the lens was seen.
    """
    bar = ZoomBar("visible", clock=Clock())
    qtbot.addWidget(bar)
    bar.set_position(0.30)               # and it will not change again
    went, _crept = commands(bar)

    _out, into = bar.buttons()
    into.pressed.emit()
    assert went, "a press that sends nothing until the first repeat reads as a dead button"
    qtbot.waitUntil(lambda: len(went) >= 4, timeout=int(REPEAT_EVERY_SECONDS * 8000))
    into.released.emit()

    wheres = [where for _name, where in went]
    assert wheres[0] == 0.30 + NUDGE / STEPS
    assert all(
        later > earlier for earlier, later in zip(wheres, wheres[1:])
    ), f"the repeats did not get anywhere; each was stepped from the stale reading: {wheres}"


def test_letting_the_button_go_stops_the_repeat(qtbot) -> None:
    """A lens that goes on zooming with nothing held is the zoom's version of a
    head still slewing after the key came up."""
    bar = ZoomBar("visible", clock=Clock())
    qtbot.addWidget(bar)
    bar.set_position(0.30)
    went, _crept = commands(bar)

    _out, into = bar.buttons()
    into.pressed.emit()
    into.released.emit()
    assert not bar.repeat_timer().isActive()

    sent = len(went)
    qtbot.wait(int(REPEAT_EVERY_SECONDS * 3000))
    assert len(went) == sent, f"{len(went) - sent} commands after the button came up"


def test_holding_a_button_on_a_camera_with_no_position_is_unchanged(qtbot) -> None:
    """The creep path is not touched by any of this, and must not be. A camera
    that cannot be sent to a position is zoomed by moving while held and stopped
    when the button comes up, and that stop is a safety property: there is no
    reading to notice the lens is still going."""
    bar = ZoomBar("visible", clock=Clock())
    qtbot.addWidget(bar)
    bar.set_position(None)
    went, crept = commands(bar)

    _out, into = bar.buttons()
    into.pressed.emit()
    assert crept == [("visible", CREEP)]
    assert not bar.repeat_timer().isActive(), "the creep path must not start a repeat"
    qtbot.wait(int(REPEAT_EVERY_SECONDS * 2000))
    into.released.emit()
    assert crept == [("visible", CREEP), ("visible", 0.0)]
    assert went == [], "a camera that reports no position may not be sent to one"


# ------------------------------------------------ letting go of the handle early


def test_a_reading_that_agrees_ends_the_hold_at_once(qtbot) -> None:
    """The hold exists so that readings of where the lens WAS cannot drag the
    handle out from under him. Once a reading arrives that agrees with what he
    asked for, the lens has arrived and there is nothing left to protect - so
    ignoring the camera for the rest of the seven seconds is a handle that has
    stopped listening to a camera answering correctly. That is the delay he felt
    on a link that happened to be behaving."""
    clock = Clock()
    bar = ZoomBar("visible", clock=clock)
    qtbot.addWidget(bar)
    bar.set_position(0.10)

    bar.slider().setSliderDown(True)
    bar.slider().setValue(80)
    bar.slider().setSliderDown(False)

    clock.tick(1.0)
    bar.set_position(0.80)               # the lens says it arrived
    assert bar.slider().value() == 80

    clock.tick(1.0)                      # well inside the old seven seconds
    bar.set_position(0.60)               # and he is told about it
    assert bar.slider().value() == 60, "the handle went on ignoring a camera that had caught up"


def test_two_steps_out_counts_as_arrived_and_three_does_not(qtbot) -> None:
    """A lens does not stop on the exact hundredth it was sent to, and a rule
    that demanded it would never fire. Two steps of a hundred is the tolerance.

    The step counts below are written out as literals rather than derived from
    CAUGHT_UP_STEPS on purpose. Read off the constant, this test says only
    "whatever the tolerance is, it is the tolerance", and it would go on passing
    if the window quietly widened to half the travel - which would put the
    handle back in the hands of readings taken mid-journey, the failure
    HOLD_AFTER_ASKING exists to prevent.
    """
    assert CAUGHT_UP_STEPS == 2, "the readings below are chosen for a two-step window"

    def hold_ended_after(reading: float) -> bool:
        clock = Clock()
        bar = ZoomBar("visible", clock=clock)
        qtbot.addWidget(bar)
        bar.set_position(0.10)
        bar.slider().setSliderDown(True)
        bar.slider().setValue(80)            # asks for 0.80
        bar.slider().setSliderDown(False)
        clock.tick(1.0)
        bar.set_position(reading)
        clock.tick(1.0)                      # still well inside HOLD_AFTER_ASKING
        bar.set_position(0.60)
        return bar.slider().value() == 60

    assert hold_ended_after(0.82), "two steps out is a lens that has arrived"
    assert not hold_ended_after(0.83), "three steps out is a lens still on its way"


def test_a_reading_that_disagrees_is_still_ignored_until_the_hold_expires(qtbot) -> None:
    """Which is the half of this that must not be lost. A lens seconds into a
    journey reports where it started, and that reading is exactly the one that
    used to pull the handle backwards out from under the mouse."""
    clock = Clock()
    bar = ZoomBar("visible", clock=clock)
    qtbot.addWidget(bar)
    bar.set_position(0.10)

    bar.slider().setSliderDown(True)
    bar.slider().setValue(80)
    bar.slider().setSliderDown(False)

    for _ in range(6):                   # six seconds of readings, all of them stale
        clock.tick(1.0)
        bar.set_position(0.11)
        assert bar.slider().value() == 80, "a reading that disagrees moved the handle"

    clock.tick(HOLD_AFTER_ASKING)
    bar.set_position(0.11)               # now it has had its fair chance
    assert bar.slider().value() == 11


# ------------------------------------------------- the ways a hold has to end
#
# Three of them, and each one is a lens that would otherwise go on being
# commanded by a button nobody is holding any more.


def test_the_repeat_stops_when_the_lens_reaches_its_stop(qtbot) -> None:
    """A lens cannot be told to go past its stop, so once the target is there
    every further repeat is the same no-op sent again.

    Harmless on the picture and not harmless on the link. `PtzCommands` keeps
    one lane per lens, and a lane with something in it every six tenths of a
    second is a lane that is never empty - so a stop for the head can be left
    waiting behind an in-flight zoom for the whole time the finger is down,
    rather than for the one call it is allowed to wait for. Before the button
    repeated at all, a press at the stop sent exactly one no-op and stopped.
    """
    bar = ZoomBar("visible", clock=Clock())
    qtbot.addWidget(bar)
    bar.set_position(0.95)               # and the reading never changes
    went, _crept = commands(bar)

    _out, into = bar.buttons()
    into.pressed.emit()
    assert went == [("visible", 1.0)]
    assert not bar.repeat_timer().isActive(), "still repeating at the end of the travel"

    # The finger is still down. Nothing more may go out.
    qtbot.wait(int(REPEAT_EVERY_SECONDS * 3000))
    assert went == [("visible", 1.0)], f"the repeat carried on past the stop: {went}"
    into.released.emit()


def test_the_repeat_steps_up_to_the_stop_before_it_gives_up(qtbot) -> None:
    """Stopping at the end must not become stopping short of it."""
    bar = ZoomBar("visible", clock=Clock())
    qtbot.addWidget(bar)
    bar.set_position(0.90)
    went, _crept = commands(bar)

    _out, into = bar.buttons()
    into.pressed.emit()
    qtbot.waitUntil(
        lambda: not bar.repeat_timer().isActive(),
        timeout=int(REPEAT_EVERY_SECONDS * 8000),
    )
    into.released.emit()
    assert [where for _name, where in went] == [0.90 + NUDGE / STEPS, 1.0], went


def test_the_repeat_stops_at_the_wide_end_too(qtbot) -> None:
    bar = ZoomBar("visible", clock=Clock())
    qtbot.addWidget(bar)
    bar.set_position(0.05)
    went, _crept = commands(bar)

    out, _into = bar.buttons()
    out.pressed.emit()
    assert went == [("visible", 0.0)]
    assert not bar.repeat_timer().isActive()
    qtbot.wait(int(REPEAT_EVERY_SECONDS * 3000))
    assert went == [("visible", 0.0)], went
    out.released.emit()


def test_a_press_that_crept_is_always_ended_by_a_stop(qtbot) -> None:
    """Whatever the camera has said in between, and this is a safety property.

    The press and the release each used to decide for themselves whether this
    was a camera that can be sent to a position, by looking at the camera's
    answer at that moment. The answer can change while the button is down - the
    lens is being polled throughout - so a press that emitted a creep could be
    followed by a release that took the absolute branch and emitted no stop at
    all. What that leaves behind is the failure this console fears most on the
    steering side and had never checked for on the zoom: a lens still travelling
    with no button held, and nothing coming to stop it.
    """
    bar = ZoomBar("visible", clock=Clock())
    qtbot.addWidget(bar)
    bar.set_position(None)               # nothing to be sent to, so: creep
    went, crept = commands(bar)

    _out, into = bar.buttons()
    into.pressed.emit()
    assert crept == [("visible", CREEP)]

    bar.set_position(0.40)               # the camera starts answering mid-hold
    into.released.emit()
    assert crept == [("visible", CREEP), ("visible", 0.0)], (
        "the lens was left creeping with the button already up"
    )
    assert went == [], went


def test_a_press_that_did_not_creep_never_sends_a_stop(qtbot) -> None:
    """The same fault the other way round, which is quieter and still wrong: a
    stop for a movement nobody started. On a camera that has just gone quiet it
    is a zoom halted in the middle of somebody else's command."""
    bar = ZoomBar("visible", clock=Clock())
    qtbot.addWidget(bar)
    bar.set_position(0.40)               # a position to be sent to, so: absolute
    went, crept = commands(bar)

    _out, into = bar.buttons()
    into.pressed.emit()
    assert went == [("visible", 0.45)]

    bar.set_position(None)               # the camera stops answering mid-hold
    into.released.emit()
    assert crept == [], f"a stop was sent for a creep that never started: {crept}"


# ------------------------------------------------------- saying what you mean


def test_the_release_does_not_repeat_the_value_the_drag_just_sent(qtbot) -> None:
    """The throttle lets a value through and then the release sends the same
    value again. The mailbox absorbs it, so nothing breaks - but a control that
    says the same thing twice is a control whose log cannot be read, and the
    count of commands per gesture is the measurement this whole change is
    judged on."""
    clock = Clock()
    bar = ZoomBar("visible", clock=clock)
    qtbot.addWidget(bar)
    bar.set_position(0.10)
    went, _crept = commands(bar)

    bar.slider().setSliderDown(True)
    bar.slider().setValue(70)            # the throttle is open: this goes out
    bar.slider().setSliderDown(False)    # and he let go on the same value
    assert went == [("visible", 0.7)], went


def test_the_release_sends_whenever_the_handle_moved_after_the_last_one(qtbot) -> None:
    """Which is the point of the release emit and must survive the tidying
    above: the value he stopped on is the only one of the gesture he chose."""
    clock = Clock()
    bar = ZoomBar("visible", clock=clock)
    qtbot.addWidget(bar)
    bar.set_position(0.10)
    went, _crept = commands(bar)

    bar.slider().setSliderDown(True)
    bar.slider().setValue(70)            # goes out
    bar.slider().setValue(72)            # throttled: no clock has passed
    bar.slider().setSliderDown(False)
    assert went == [("visible", 0.7), ("visible", 0.72)], went


def test_the_next_drag_sends_at_once_however_soon_it_starts(qtbot) -> None:
    """The throttle is per gesture, not a rolling window over the session. Two
    drags in quick succession are two intentions, and making the second one wait
    out the tail of the first is the dead handle coming back for the drag that
    corrects the one before it."""
    bar = ZoomBar("visible", clock=Clock())   # never ticked: no time passes at all
    qtbot.addWidget(bar)
    bar.set_position(0.10)
    went, _crept = commands(bar)

    bar.slider().setSliderDown(True)
    bar.slider().setValue(30)
    bar.slider().setSliderDown(False)
    assert went == [("visible", 0.3)], went

    bar.slider().setSliderDown(True)
    bar.slider().setValue(60)
    assert went[-1] == ("visible", 0.6), f"the second drag was throttled by the first: {went}"


def test_the_repeat_cannot_outlive_the_widget(qtbot) -> None:
    """A held button that survived its own bar would go on commanding a lens
    through a control nobody can see. The timer is a child of the widget, so Qt
    destroys it with its parent - and this is the test that it really is one.

    `deleteLater` only posts an event. `processEvents` will NOT deliver a
    DeferredDelete posted at loop level 0, so a version of this test written
    with `processEvents` alone passes whether or not the timer is parented to
    anything, which is a test that cannot fail. `sendPostedEvents` with that
    event type asked for by name is what actually runs the destructor.
    """
    bar = ZoomBar("visible", clock=Clock())   # deliberately not given to qtbot:
    bar.set_position(0.30)                    # this test destroys it by hand
    _out, into = bar.buttons()
    into.pressed.emit()
    timer = bar.repeat_timer()
    assert timer.isActive(), "nothing is being proved unless the repeat was running"

    bar.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(timer), "the repeat outlived the bar that owns it"
