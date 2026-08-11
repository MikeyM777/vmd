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

from PySide6.QtCore import Qt

from vmd.desktop.zoombar import (
    CHECKING_CAPTION,
    CREEP,
    NUDGE,
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
