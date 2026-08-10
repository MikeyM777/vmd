"""The Live tab, driven against fake panes and a fake PTZ."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from vmd.desktop.live import LiveTab
from vmd.desktop.video import FakeVideoPane
from vmd.settings import CameraSettings, Settings, StreamSettings


class FakePtz:
    def __init__(self) -> None:
        self.commands: list[tuple] = []

    def status(self) -> dict:
        return {"available": True, "reason": "ready"}

    def move(self, pan: float, tilt: float, zoom: float) -> dict:
        self.commands.append(("move", pan, tilt, zoom))
        return {"ok": True}

    def stop(self) -> dict:
        self.commands.append(("stop",))
        return {"ok": True}

    def home(self) -> dict:
        self.commands.append(("home",))
        return {"ok": True}


def settings_with(*names: str) -> Settings:
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(name=name, url=f"rtsp://10.0.0.2/{name}", enabled=True)
                for name in names
            ],
        )
    )


def build(qtbot, *names: str):
    ptz = FakePtz()
    panes: dict[str, FakeVideoPane] = {}

    def make_pane(name: str) -> FakeVideoPane:
        panes[name] = FakeVideoPane()
        return panes[name]

    tab = LiveTab(ptz=ptz, make_pane=make_pane, local_url=lambda name: f"rtsp://127.0.0.1:8554/{name}")
    qtbot.addWidget(tab)
    tab.apply(settings_with(*names))
    return tab, ptz, panes


def test_a_pane_appears_for_every_enabled_stream(qtbot) -> None:
    tab, _, panes = build(qtbot, "thermal", "visible")
    assert set(panes) == {"thermal", "visible"}


def test_panes_are_pointed_at_the_local_server_not_the_camera(qtbot) -> None:
    """One connection crosses the radio link, and it is not this one."""
    tab, _, panes = build(qtbot, "thermal")
    assert panes["thermal"].url == "rtsp://127.0.0.1:8554/thermal"


def test_arrow_keys_move_the_camera_and_release_stops_it(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    tab.key_down("right", fine=False)
    assert ptz.commands[-1] == ("move", 0.5, 0.0, 0.0)
    tab.key_down("up", fine=False)
    assert ptz.commands[-1] == ("move", 0.5, 0.5, 0.0)
    tab.key_up("up")
    assert ptz.commands[-1] == ("move", 0.5, 0.0, 0.0)
    tab.key_up("right")
    assert ptz.commands[-1] == ("stop",)


def test_home_is_sent_once(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    tab.go_home()
    assert ptz.commands == [("home",)]


def test_the_same_velocity_is_not_sent_twice(qtbot) -> None:
    """Repeat key events must not become a command storm on the link."""
    tab, ptz, _ = build(qtbot, "thermal")
    tab.key_down("right", fine=False)
    tab.key_down("right", fine=False)
    assert len(ptz.commands) == 1


def test_a_late_stream_is_reported_and_left_alone(qtbot) -> None:
    tab, _, panes = build(qtbot, "thermal")
    panes["thermal"].pretend_playing()
    panes["thermal"].pretend_late()
    tab.refresh()
    assert "late" in tab.stream_status_text("thermal").lower()
    assert panes["thermal"].restarts == 0


def test_a_failed_stream_is_restarted(qtbot) -> None:
    tab, _, panes = build(qtbot, "thermal")
    panes["thermal"].pretend_failed()
    tab.refresh()
    assert panes["thermal"].restarts == 1


def test_changing_the_streams_replaces_the_panes(qtbot) -> None:
    tab, _, panes = build(qtbot, "thermal")
    tab.apply(settings_with("thermal", "visible"))
    assert set(panes) == {"thermal", "visible"}


# --------------------------------------------------------------- the keyboard
#
# The tests above call the steering methods directly, which proves the logic and
# nothing about the wiring. These press keys at the widget.


def test_a_real_arrow_key_press_moves_the_camera(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    qtbot.keyPress(tab, Qt.Key.Key_Right)
    assert ptz.commands[-1] == ("move", 0.5, 0.0, 0.0)
    qtbot.keyRelease(tab, Qt.Key.Key_Right)
    assert ptz.commands[-1] == ("stop",)


def test_shift_held_with_an_arrow_steers_finely(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    qtbot.keyPress(tab, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert ptz.commands[-1] == ("move", -0.08, 0.0, 0.0)


def test_the_home_key_sends_home(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    qtbot.keyClick(tab, Qt.Key.Key_Home)
    assert ("home",) in ptz.commands


def test_the_zoom_keys_zoom_and_releasing_them_stops(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    qtbot.keyPress(tab, Qt.Key.Key_Plus)
    assert ptz.commands[-1] == ("move", 0.0, 0.0, 0.5)
    qtbot.keyRelease(tab, Qt.Key.Key_Plus)
    assert ptz.commands[-1] == ("stop",)
    qtbot.keyPress(tab, Qt.Key.Key_Minus)
    assert ptz.commands[-1] == ("move", 0.0, 0.0, -0.5)
    qtbot.keyRelease(tab, Qt.Key.Key_Minus)
    assert ptz.commands[-1] == ("stop",)


def send_auto_repeat(tab, kind: QEvent.Type, key: Qt.Key) -> None:
    """The press and release Windows generates while a key is simply held."""
    event = QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier, "", True)
    QApplication.instance().sendEvent(tab, event)


def test_auto_repeat_neither_repeats_nor_stops_the_movement(qtbot) -> None:
    """Holding a key on Windows produces repeat press *and* release events.
    Acting on the repeat release would stutter the head; acting on the repeat
    press would put one request per repeat onto the link."""
    tab, ptz, _ = build(qtbot, "thermal")
    qtbot.keyPress(tab, Qt.Key.Key_Right)
    assert ptz.commands == [("move", 0.5, 0.0, 0.0)]

    for _ in range(5):
        send_auto_repeat(tab, QEvent.Type.KeyRelease, Qt.Key.Key_Right)
        send_auto_repeat(tab, QEvent.Type.KeyPress, Qt.Key.Key_Right)

    assert ptz.commands == [("move", 0.5, 0.0, 0.0)]

    qtbot.keyRelease(tab, Qt.Key.Key_Right)
    assert ptz.commands[-1] == ("stop",)


def test_losing_focus_stops_the_camera(qtbot) -> None:
    """A window that loses focus mid-slew must not leave the head moving."""
    tab, ptz, _ = build(qtbot, "thermal")
    qtbot.keyPress(tab, Qt.Key.Key_Right)
    QApplication.instance().sendEvent(tab, QEvent(QEvent.Type.FocusOut))
    assert ptz.commands[-1] == ("stop",)

    # And the key it never saw released is forgotten, so the next press is a
    # fresh movement rather than a diagonal with a ghost.
    qtbot.keyPress(tab, Qt.Key.Key_Up)
    assert ptz.commands[-1] == ("move", 0.0, 0.5, 0.0)


# ---------------------------------------------------------------- the overlay


def test_dragging_near_the_right_edge_pans_right_and_release_stops(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    overlay = tab.overlay
    overlay.resize(200, 100)

    qtbot.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(198, 50))
    assert ptz.commands[-1][0] == "move"
    assert ptz.commands[-1][1] > 0.0

    qtbot.mouseMove(overlay, QPoint(190, 50))
    assert ptz.commands[-1][1] > 0.0

    qtbot.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(190, 50))
    assert ptz.commands[-1] == ("stop",)


def test_the_pointer_does_not_steer_unless_a_button_is_held(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    overlay = tab.overlay
    overlay.resize(200, 100)
    qtbot.mouseMove(overlay, QPoint(198, 50))
    assert ptz.commands == []


def test_the_pointer_leaving_the_overlay_stops_the_camera(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    overlay = tab.overlay
    overlay.resize(200, 100)
    qtbot.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(198, 50))
    assert ptz.commands[-1][0] == "move"
    QApplication.instance().sendEvent(overlay, QEvent(QEvent.Type.Leave))
    assert ptz.commands[-1] == ("stop",)


# ------------------------------------------------------------- state on screen


def test_the_side_column_shows_every_stream_by_name(qtbot) -> None:
    tab, _, _ = build(qtbot, "thermal", "visible")
    assert "thermal" in tab.stream_label_text("thermal").lower()
    assert "visible" in tab.stream_label_text("visible").lower()


def test_a_late_stream_looks_different_from_a_playing_one(qtbot) -> None:
    tab, _, panes = build(qtbot, "thermal")
    panes["thermal"].pretend_playing()
    tab.refresh()
    playing_text = tab.stream_label_text("thermal")
    playing_look = tab.stream_label_style("thermal")

    panes["thermal"].pretend_late()
    tab.refresh()
    late_text = tab.stream_label_text("thermal")

    assert late_text != playing_text
    assert "late" in late_text.lower()
    assert tab.stream_label_style("thermal") != playing_look


def test_a_removed_stream_leaves_no_label_behind(qtbot) -> None:
    tab, _, _ = build(qtbot, "thermal", "visible")
    tab.apply(settings_with("thermal"))
    assert tab.stream_names() == ["thermal"]
