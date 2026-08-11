"""The row of buttons under the picture.

The requirement is the buttons, not the keys: *"i rather buttons, space and
arrows are nice but i need also buttons"*. So every test here presses a control
that is on the screen. The keys are tested where they are bound, on the tab.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton

from vmd.desktop.transport import SPEEDS, TransportBar


def build(qtbot) -> TransportBar:
    bar = TransportBar()
    qtbot.addWidget(bar)
    return bar


def test_every_control_is_something_that_can_be_pressed(qtbot) -> None:
    bar = build(qtbot)
    for button in (
        bar.play_button,
        bar.back_minute,
        bar.back_ten,
        bar.step_back,
        bar.step_forward,
        bar.forward_ten,
        bar.forward_minute,
    ):
        assert isinstance(button, QAbstractButton)
        assert button.text().strip(), "a button with no words on it"


def test_pressing_play_asks_for_play_and_says_pause_afterwards(qtbot) -> None:
    bar = build(qtbot)
    asked: list[bool] = []
    bar.play_pause.connect(lambda: asked.append(True))
    assert "play" in bar.play_button.text().lower()

    qtbot.mouseClick(bar.play_button, Qt.MouseButton.LeftButton)
    assert asked == [True]

    # The console is what knows whether anything is playing; it tells the bar.
    bar.set_playing(True)
    assert "pause" in bar.play_button.text().lower()
    bar.set_playing(False)
    assert "play" in bar.play_button.text().lower()


def test_the_skips_ask_for_the_number_of_seconds_they_say(qtbot) -> None:
    bar = build(qtbot)
    asked: list[float] = []
    bar.skipped.connect(asked.append)

    for button, seconds in (
        (bar.back_minute, -60.0),
        (bar.back_ten, -10.0),
        (bar.step_back, -1.0),
        (bar.step_forward, 1.0),
        (bar.forward_ten, 10.0),
        (bar.forward_minute, 60.0),
    ):
        asked.clear()
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        assert asked == [seconds], button.text()


def test_the_buttons_say_which_way_and_how_far(qtbot) -> None:
    """He is not reading a manual. A button that means "ten seconds back" says
    ten seconds somewhere on it."""
    bar = build(qtbot)
    assert "10" in bar.back_ten.text() and "10" in bar.forward_ten.text()
    assert "1 min" in bar.back_minute.text() and "1 min" in bar.forward_minute.text()


def test_the_speeds_are_the_six_he_asked_for(qtbot) -> None:
    bar = build(qtbot)
    assert SPEEDS == (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
    offered = [bar.speed_selector.itemData(i) for i in range(bar.speed_selector.count())]
    assert offered == list(SPEEDS)


def test_normal_speed_is_where_it_starts(qtbot) -> None:
    bar = build(qtbot)
    assert bar.speed() == 1.0


def test_choosing_a_speed_asks_for_it(qtbot) -> None:
    bar = build(qtbot)
    asked: list[float] = []
    bar.speed_chosen.connect(asked.append)
    bar.speed_selector.setCurrentIndex(list(SPEEDS).index(4.0))
    assert asked == [4.0]
    assert bar.speed() == 4.0


def test_the_speed_can_be_set_without_asking_for_it_again(qtbot) -> None:
    """Setting the control from what the player is actually doing must not
    loop back round and ask the player to do it again."""
    bar = build(qtbot)
    asked: list[float] = []
    bar.speed_chosen.connect(asked.append)
    bar.set_speed(8.0)
    assert bar.speed() == 8.0
    assert asked == []


def test_normal_speed_says_normal(qtbot) -> None:
    """1x is not a number he thinks in. The one that means "as it happened"
    says so in words."""
    bar = build(qtbot)
    words = [bar.speed_selector.itemText(i) for i in range(bar.speed_selector.count())]
    assert any("normal" in word.lower() for word in words), words


def test_nothing_on_it_names_the_machinery(qtbot) -> None:
    bar = build(qtbot)
    banned = ("yolo", "cnn", "classifier", "inference", "model", "sensor", "codec",
              "ffmpeg", "vlc", "seek", "buffer", "rtsp", "frame rate")
    said = " ".join(
        [b.text() for b in bar.findChildren(QAbstractButton)]
        + [bar.speed_selector.itemText(i) for i in range(bar.speed_selector.count())]
        + [b.toolTip() for b in bar.findChildren(QAbstractButton)]
    ).lower()
    for word in banned:
        assert word not in said, said


def test_the_buttons_are_big_enough_to_hit_without_aiming(qtbot) -> None:
    """He is not a mouse athlete and this console is used under pressure. A
    24 px control in a row of seven is a control he misses."""
    bar = build(qtbot)
    for button in bar.findChildren(QAbstractButton):
        assert button.minimumHeight() >= 32, button.text()
        assert button.minimumWidth() >= 44, button.text()


def test_the_bar_can_be_switched_off_when_there_is_nothing_to_play(qtbot) -> None:
    """A transport over an empty day invites a press that does nothing, which
    reads as a console that has stopped answering."""
    bar = build(qtbot)
    bar.set_usable(False)
    assert not bar.play_button.isEnabled()
    assert not bar.forward_ten.isEnabled()
    bar.set_usable(True)
    assert bar.play_button.isEnabled()
