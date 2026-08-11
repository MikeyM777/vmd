"""The Live tab, driven against fake panes and a fake PTZ."""

from __future__ import annotations

import datetime
import sqlite3
import threading
import time

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

from vmd.desktop.live import LiveTab
from vmd.desktop.style import PALETTE
from vmd.desktop.video import FakeVideoPane
from vmd.detect.events import Event
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


class FakeEvents:
    """A reader with the EventStore's shape and none of its SQLite."""

    def __init__(self, events: list[Event] | None = None) -> None:
        self.events = list(events or [])
        self.reads = 0

    def recent(self, limit: int = 50) -> list[Event]:
        self.reads += 1
        newest = sorted(self.events, key=lambda e: (e.started, e.id), reverse=True)
        return newest[:limit]


class BrokenEvents:
    def recent(self, limit: int = 50):
        raise sqlite3.DatabaseError("database disk image is malformed")


def movement(
    event_id: int,
    stream: str = "thermal",
    started: float = 1_770_000_000.0,
    label: str = "",
    confidence: float = 0.0,
) -> Event:
    return Event(
        id=event_id,
        stream=stream,
        started=started,
        ended=started + 3.0,
        box=(10, 20, 13, 30),
        travelled_px=44.0,
        label=label,
        confidence=confidence,
    )


def build(qtbot, *names: str, events=None, register: bool = True, clock=None):
    ptz = FakePtz()
    panes: dict[str, FakeVideoPane] = {}

    def make_pane(name: str) -> FakeVideoPane:
        panes[name] = FakeVideoPane()
        return panes[name]

    tab = LiveTab(
        ptz=ptz,
        make_pane=make_pane,
        local_url=lambda name: f"rtsp://127.0.0.1:8554/{name}",
        events=events,
        clock=clock,
    )
    # A tab that is about to be given to a parent widget is left unregistered:
    # qtbot would then close and delete it twice over.
    if register:
        qtbot.addWidget(tab)
    tab.apply(settings_with(*names))
    return tab, ptz, panes


# PTZ commands leave the console on a thread of their own, because a camera at
# the far end of a radio link takes seconds to answer and the window may not
# stop repainting while it does. So "what the camera was told" is a question
# with a moment's delay in it, and every assertion about it waits - bounded, so
# that a command which never arrives fails the test instead of hanging it.
COMMAND_TIMEOUT = 10.0


def sent(tab, ptz, timeout: float = COMMAND_TIMEOUT) -> list[tuple]:
    """Everything the camera has actually been told, once telling it is done."""
    assert tab.wait_for_camera(timeout), "a PTZ command never left the console"
    return ptz.commands


class SlowPtz(FakePtz):
    """A camera that takes as long to answer as an unreachable one does.

    The wait is bounded by an event the test releases, and by a ceiling of its
    own, so a console that went back to calling this on the GUI thread fails the
    test in a couple of seconds rather than hanging the suite.
    """

    CEILING = 5.0

    def __init__(self) -> None:
        super().__init__()
        self.released = threading.Event()
        self.entered = threading.Event()

    def _wait(self) -> None:
        self.entered.set()
        self.released.wait(self.CEILING)

    def move(self, pan: float, tilt: float, zoom: float) -> dict:
        self._wait()
        return super().move(pan, tilt, zoom)

    def stop(self) -> dict:
        self._wait()
        return super().stop()

    def home(self) -> dict:
        self._wait()
        return super().home()


def slow_tab(qtbot, ptz):
    tab = LiveTab(
        ptz=ptz,
        make_pane=lambda name: FakeVideoPane(),
        local_url=lambda name: f"rtsp://127.0.0.1:8554/{name}",
    )
    qtbot.addWidget(tab)
    tab.apply(settings_with("thermal"))
    return tab


def until(predicate, timeout: float = COMMAND_TIMEOUT) -> bool:
    """Bounded polling, so a regression fails the test rather than hanging it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


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
    assert sent(tab, ptz)[-1] == ("move", 0.5, 0.0, 0.0)
    tab.key_down("up", fine=False)
    assert sent(tab, ptz)[-1] == ("move", 0.5, 0.5, 0.0)
    tab.key_up("up")
    assert sent(tab, ptz)[-1] == ("move", 0.5, 0.0, 0.0)
    tab.key_up("right")
    assert sent(tab, ptz)[-1] == ("stop",)


def test_home_is_sent_once(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    tab.go_home()
    assert sent(tab, ptz) == [("home",)]


def test_the_same_velocity_is_not_sent_twice(qtbot) -> None:
    """Repeat key events must not become a command storm on the link."""
    tab, ptz, _ = build(qtbot, "thermal")
    tab.key_down("right", fine=False)
    tab.key_down("right", fine=False)
    assert len(sent(tab, ptz)) == 1


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


class HandWoundClock:
    """Two seconds per turn, exactly as the heartbeat runs. No test waits."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_a_stream_that_will_never_come_back_is_not_restarted_every_two_seconds(
    qtbot, caplog
) -> None:
    """A camera that is off, or an address that is wrong, fails on every tick
    for as long as the console is open. Restarting it thirty times a minute is
    the recovery-code-firing-too-early mistake this module's docstring is about,
    one level up: it also floods a ring buffer that holds five hundred lines and
    pushes out go2rtc's "401 Unauthorized" - the line that says why."""
    clock = HandWoundClock()
    tab, _, panes = build(qtbot, "thermal", clock=clock)
    panes["thermal"].pretend_failed()

    with caplog.at_level("WARNING", logger="vmd.desktop.live"):
        for _ in range(200):  # 400 s of heartbeats
            panes["thermal"].pretend_failed()
            tab.refresh()
            clock.advance(2.0)

    assert panes["thermal"].restarts >= 1, "it must still try"
    assert panes["thermal"].restarts <= 20, (
        f"{panes['thermal'].restarts} restarts in 400 s is a restart storm"
    )
    assert len(caplog.records) <= 5, "the log was flooded by one dead stream"
    assert caplog.records, "and it must not go silent about it either"


def test_a_stream_that_keeps_failing_stops_claiming_it_will_fix_itself(qtbot) -> None:
    clock = HandWoundClock()
    tab, _, panes = build(qtbot, "thermal", clock=clock)
    for _ in range(200):
        panes["thermal"].pretend_failed()
        tab.refresh()
        clock.advance(2.0)

    label = tab.stream_label_text("thermal").lower()
    assert "not coming back" in label
    assert "settings" in label, "say where the operator can do something about it"


def test_a_stream_that_comes_back_forgets_the_backoff(qtbot) -> None:
    clock = HandWoundClock()
    tab, _, panes = build(qtbot, "thermal", clock=clock)
    for _ in range(30):
        panes["thermal"].pretend_failed()
        tab.refresh()
        clock.advance(2.0)

    panes["thermal"].pretend_playing()
    tab.refresh()
    assert "not coming back" not in tab.stream_label_text("thermal").lower()

    before = panes["thermal"].restarts
    panes["thermal"].pretend_failed()
    tab.refresh()
    assert panes["thermal"].restarts == before + 1, "a recovered stream waits again"


def test_a_stream_that_comes_back_and_fails_again_is_reported_again(qtbot, caplog) -> None:
    tab, _, panes = build(qtbot, "thermal")
    with caplog.at_level("WARNING", logger="vmd.desktop.live"):
        panes["thermal"].pretend_failed()
        tab.refresh()
        panes["thermal"].pretend_playing()
        tab.refresh()
        panes["thermal"].pretend_failed()
        tab.refresh()

    assert len(caplog.records) == 2


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
    assert sent(tab, ptz)[-1] == ("move", 0.5, 0.0, 0.0)
    qtbot.keyRelease(tab, Qt.Key.Key_Right)
    assert sent(tab, ptz)[-1] == ("stop",)


def test_shift_held_with_an_arrow_steers_finely(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    qtbot.keyPress(tab, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert sent(tab, ptz)[-1] == ("move", -0.08, 0.0, 0.0)


def test_the_home_key_sends_home(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    qtbot.keyClick(tab, Qt.Key.Key_Home)
    assert ("home",) in sent(tab, ptz)


def test_the_zoom_keys_zoom_and_releasing_them_stops(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    qtbot.keyPress(tab, Qt.Key.Key_Plus)
    assert sent(tab, ptz)[-1] == ("move", 0.0, 0.0, 0.5)
    qtbot.keyRelease(tab, Qt.Key.Key_Plus)
    assert sent(tab, ptz)[-1] == ("stop",)
    qtbot.keyPress(tab, Qt.Key.Key_Minus)
    assert sent(tab, ptz)[-1] == ("move", 0.0, 0.0, -0.5)
    qtbot.keyRelease(tab, Qt.Key.Key_Minus)
    assert sent(tab, ptz)[-1] == ("stop",)


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
    assert sent(tab, ptz) == [("move", 0.5, 0.0, 0.0)]

    for _ in range(5):
        send_auto_repeat(tab, QEvent.Type.KeyRelease, Qt.Key.Key_Right)
        send_auto_repeat(tab, QEvent.Type.KeyPress, Qt.Key.Key_Right)

    assert sent(tab, ptz) == [("move", 0.5, 0.0, 0.0)]

    qtbot.keyRelease(tab, Qt.Key.Key_Right)
    assert sent(tab, ptz)[-1] == ("stop",)


def test_losing_focus_stops_the_camera(qtbot) -> None:
    """A window that loses focus mid-slew must not leave the head moving."""
    tab, ptz, _ = build(qtbot, "thermal")
    qtbot.keyPress(tab, Qt.Key.Key_Right)
    QApplication.instance().sendEvent(tab, QEvent(QEvent.Type.FocusOut))
    assert sent(tab, ptz)[-1] == ("stop",)

    # And the key it never saw released is forgotten, so the next press is a
    # fresh movement rather than a diagonal with a ghost.
    qtbot.keyPress(tab, Qt.Key.Key_Up)
    assert sent(tab, ptz)[-1] == ("move", 0.0, 0.5, 0.0)


def test_switching_tabs_stops_a_camera_a_child_widget_started_moving(qtbot) -> None:
    """The hazard, in the order it actually happens.

    The operator clicks the movement list (or the Acknowledge button), then
    holds an arrow. The key event travels up to the tab unhandled, so the head
    moves - but the tab itself does not hold the focus. Switching to Settings
    therefore delivers no focusOut, and nothing will ever deliver the key
    release either: the head keeps slewing until it hits its stop while the
    operator is looking at another tab.
    """
    tab, ptz, _ = build(qtbot, "thermal", register=False)
    tabs = QTabWidget()
    tabs.addTab(tab, "Live")
    tabs.addTab(QWidget(), "Settings")
    qtbot.addWidget(tabs)

    tab._movement.setFocus()
    QApplication.instance().sendEvent(
        tab, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
    )
    assert sent(tab, ptz)[-1] == ("move", 0.5, 0.0, 0.0)

    tabs.setCurrentIndex(1)

    assert sent(tab, ptz)[-1] == ("stop",), "the head was left slewing on another tab"
    # And the key it never saw released is forgotten, so coming back to the tab
    # does not start with a ghost held down.
    tabs.setCurrentIndex(0)
    qtbot.keyPress(tab, Qt.Key.Key_Up)
    assert sent(tab, ptz)[-1] == ("move", 0.0, 0.5, 0.0)


# ------------------------------------------------- steering a camera that is slow
#
# Measured against a camera at 10.255.255.1: 6.19 s for the press and 6.17 s for
# the release, so 12.36 s for one tap of an arrow key. All of it on the thread
# that draws the window, which means no repaint, no supervisor tick and - the
# reason this is severe rather than annoying - no alarm strip. A perimeter
# crossing during that freeze is silently missed.


def test_a_key_tap_does_not_wait_for_the_camera(qtbot) -> None:
    """Press and release must return at once, however long the camera takes."""
    ptz = SlowPtz()
    tab = slow_tab(qtbot, ptz)
    try:
        started = time.monotonic()
        tab.key_down("right", fine=False)
        tab.key_up("right")
        elapsed = time.monotonic() - started
    finally:
        ptz.released.set()
    assert elapsed < 0.5, f"one tap held the GUI thread for {elapsed:.2f} s"


def test_the_alarm_strip_can_still_appear_while_the_camera_is_not_answering(
    qtbot,
) -> None:
    """The whole reason this is severe. A refresh - the heartbeat's own call -
    must raise the alarm while a PTZ command is still in flight."""
    ptz = SlowPtz()
    tab = LiveTab(
        ptz=ptz,
        make_pane=lambda name: FakeVideoPane(),
        local_url=lambda name: f"rtsp://127.0.0.1:8554/{name}",
        events=FakeEvents([movement(1)]),
    )
    qtbot.addWidget(tab)
    tab.apply(settings_with("thermal"))
    try:
        tab.refresh()  # the first read only learns what was already there
        tab.key_down("right", fine=False)
        assert ptz.entered.wait(5.0), "the command never reached the camera"
        tab._events.events.append(movement(2))
        started = time.monotonic()
        tab.refresh()
        elapsed = time.monotonic() - started
    finally:
        ptz.released.set()
    assert tab.alarm_visible(), "movement went unannounced while the camera hung"
    assert elapsed < 0.5, f"the heartbeat blocked for {elapsed:.2f} s"


def test_a_stop_is_delivered_even_when_it_arrives_while_a_move_is_in_flight(
    qtbot,
) -> None:
    """The hazard: the head slewing with no key held because the settling call
    was still on the wire when the operator let go."""
    ptz = SlowPtz()
    tab = slow_tab(qtbot, ptz)
    tab.key_down("right", fine=False)
    assert ptz.entered.wait(5.0), "the move never reached the camera"
    tab.key_up("right")
    ptz.released.set()
    assert tab.wait_for_camera(COMMAND_TIMEOUT)
    assert ptz.commands[-1] == ("stop",), "the head was left moving with no key held"


def test_a_stop_beats_a_move_that_is_still_waiting_to_be_sent(qtbot) -> None:
    """Commands are coalesced - four taps while one is on the wire must not
    replay as four - but the one command that must never be coalesced away is
    the stop."""
    ptz = SlowPtz()
    tab = slow_tab(qtbot, ptz)
    tab.key_down("right", fine=False)          # this one goes on the wire
    assert ptz.entered.wait(5.0)
    tab.key_down("up", fine=False)             # queued behind it
    tab.key_up("up")
    tab.key_up("right")                        # and now a stop is owed
    ptz.released.set()
    assert tab.wait_for_camera(COMMAND_TIMEOUT)
    assert ptz.commands[-1] == ("stop",)
    assert len(ptz.commands) <= 3, f"every keystroke was replayed: {ptz.commands}"


def test_a_burst_of_arrows_leaves_the_camera_doing_the_last_one(qtbot) -> None:
    ptz = SlowPtz()
    tab = slow_tab(qtbot, ptz)
    tab.key_down("right", fine=False)
    assert ptz.entered.wait(5.0)
    for key in ("up", "down", "left"):
        tab.key_down(key, fine=False)
        tab.key_up(key)
    ptz.released.set()
    assert tab.wait_for_camera(COMMAND_TIMEOUT)
    # Right is still held, so that is what the camera must end up doing.
    assert ptz.commands[-1] == ("move", 0.5, 0.0, 0.0)


def test_closing_the_tab_delivers_the_stop_it_owes(qtbot) -> None:
    """A window closed with a key down must not leave the head slewing."""
    ptz = FakePtz()
    tab = slow_tab(qtbot, ptz)
    tab.key_down("right", fine=False)
    tab.shutdown()
    assert ptz.commands[-1] == ("stop",), "the console closed with the head moving"


def test_shutdown_does_not_hang_on_a_camera_that_never_answers(qtbot) -> None:
    ptz = SlowPtz()
    tab = slow_tab(qtbot, ptz)
    tab.key_down("right", fine=False)
    assert ptz.entered.wait(5.0)
    try:
        started = time.monotonic()
        tab.shutdown()
        elapsed = time.monotonic() - started
    finally:
        ptz.released.set()
    assert elapsed < 4.0, f"closing the window waited {elapsed:.2f} s on the camera"


def test_a_command_the_camera_has_not_answered_is_reported_as_such(qtbot) -> None:
    """Never a blank that reads as fine: an unanswered command says so."""
    ptz = SlowPtz()
    tab = slow_tab(qtbot, ptz)
    tab.key_down("right", fine=False)
    assert ptz.entered.wait(5.0)
    try:
        assert until(lambda: (tab.refresh(), "not answer" in tab.camera_note())[1])
    finally:
        ptz.released.set()


def test_a_refused_command_says_what_the_camera_said(qtbot) -> None:
    class RefusingPtz(FakePtz):
        def move(self, pan, tilt, zoom) -> dict:
            super().move(pan, tilt, zoom)
            return {"ok": False, "error": "the camera refused the login (401)"}

    ptz = RefusingPtz()
    tab = slow_tab(qtbot, ptz)
    tab.key_down("right", fine=False)
    assert tab.wait_for_camera(COMMAND_TIMEOUT)
    tab.refresh()
    assert "401" in tab.camera_note()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_stop_still_gets_through_after_the_command_thread_has_died(qtbot) -> None:
    """A stop is the one command that must never be dropped, so losing the
    thread that sends them must not be the end of it.

    KeyboardInterrupt because it is not an Exception: it goes straight past the
    guard around each command and ends the thread, which is the only way that
    thread can be lost.
    """

    class ExplodingPtz(FakePtz):
        def move(self, pan, tilt, zoom) -> dict:
            raise KeyboardInterrupt("the thread is gone")

    ptz = ExplodingPtz()
    tab = slow_tab(qtbot, ptz)
    tab.key_down("right", fine=False)
    assert until(lambda: not tab._commands._thread.is_alive()), "the thread survived"
    tab.key_up("right")
    assert tab.wait_for_camera(COMMAND_TIMEOUT)
    assert ptz.commands[-1] == ("stop",)


# ---------------------------------------------------------------- the overlay


def test_dragging_near_the_right_edge_pans_right_and_release_stops(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    overlay = tab.overlay
    overlay.resize(200, 100)

    qtbot.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(198, 50))
    assert sent(tab, ptz)[-1][0] == "move"
    assert sent(tab, ptz)[-1][1] > 0.0

    qtbot.mouseMove(overlay, QPoint(190, 50))
    assert sent(tab, ptz)[-1][1] > 0.0

    qtbot.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(190, 50))
    assert sent(tab, ptz)[-1] == ("stop",)


def test_the_pointer_does_not_steer_unless_a_button_is_held(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    overlay = tab.overlay
    overlay.resize(200, 100)
    qtbot.mouseMove(overlay, QPoint(198, 50))
    assert sent(tab, ptz) == []


def test_the_pointer_leaving_the_overlay_stops_the_camera(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    overlay = tab.overlay
    overlay.resize(200, 100)
    qtbot.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(198, 50))
    assert sent(tab, ptz)[-1][0] == "move"
    QApplication.instance().sendEvent(overlay, QEvent(QEvent.Type.Leave))
    assert sent(tab, ptz)[-1] == ("stop",)


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


def test_a_replaced_pane_hands_libvlc_back(qtbot) -> None:
    """python-vlc frees nothing when its objects are collected. A pane that is
    only stopped keeps a player, its decoder threads and a whole libVLC
    instance, and the panes are rebuilt every time the streams change."""
    tab, _, panes = build(qtbot, "thermal")
    first = panes["thermal"]
    assert first.released is False

    tab.apply(settings_with("visible"))

    assert first.released is True, "the old pane still holds libVLC"
    assert panes["visible"].released is False


def test_a_removed_stream_leaves_no_label_behind(qtbot) -> None:
    tab, _, _ = build(qtbot, "thermal", "visible")
    tab.apply(settings_with("thermal"))
    assert tab.stream_names() == ["thermal"]


# ------------------------------------------------------------- what moved
#
# The alarm strip and the recent movement list. Every test here drives an
# injected reader, so none of them opens a database, and none of them waits.


def clock_text(started: float) -> str:
    return datetime.datetime.fromtimestamp(started).strftime("%H:%M:%S")


def test_a_new_event_raises_the_alarm_naming_the_stream_and_the_time(qtbot) -> None:
    events = FakeEvents()
    tab, _, _ = build(qtbot, "thermal", "visible", events=events)
    tab.refresh()  # nothing has moved yet
    assert tab.alarm_visible() is False

    events.events.append(movement(1, stream="thermal", started=1_770_000_123.0))
    tab.refresh()

    assert tab.alarm_visible() is True
    assert "thermal" in tab.alarm_text()
    assert clock_text(1_770_000_123.0) in tab.alarm_text()
    assert PALETTE["alarm"] in tab.alarm_style()


def test_acknowledging_clears_the_alarm(qtbot) -> None:
    events = FakeEvents()
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()
    events.events.append(movement(1))
    tab.refresh()
    assert tab.alarm_visible() is True

    tab.acknowledge()

    assert tab.alarm_visible() is False
    # And it stays cleared: the same event must not raise it again on the next
    # tick, two seconds later, forever.
    tab.refresh()
    assert tab.alarm_visible() is False


def test_the_alarm_outlines_the_pane_the_movement_was_seen_on(qtbot) -> None:
    events = FakeEvents()
    tab, _, _ = build(qtbot, "thermal", "visible", events=events)
    tab.refresh()
    events.events.append(movement(1, stream="visible"))
    tab.refresh()

    assert tab.outlined_stream() == "visible"
    assert PALETTE["alarm"] in tab.pane_outline_style("visible")
    assert PALETTE["alarm"] not in tab.pane_outline_style("thermal")

    tab.acknowledge()
    assert tab.outlined_stream() is None
    assert PALETTE["alarm"] not in tab.pane_outline_style("visible")


def test_movement_the_console_missed_does_not_raise_a_stale_alarm(qtbot) -> None:
    """The recorder and the detector outlive the window. Opening the console
    days later must not blare about something that moved on Tuesday - but the
    list still shows it, because it happened."""
    events = FakeEvents([movement(1), movement(2)])
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()
    # Twice: the first read has to *remember* what was already there, not merely
    # skip it, or the alarm arrives two seconds late instead of never.
    tab.refresh()

    assert tab.alarm_visible() is False
    assert len(tab.recent_rows()) == 2


def test_an_unidentified_event_leaves_the_confidence_blank(qtbot) -> None:
    """This is the whole point. At 700 m a person is about 13 pixels: the
    classifier cannot name it, and the operator still needs to know. "0%" or
    "unknown" in that cell would read as "nothing was there"."""
    events = FakeEvents([movement(1, stream="thermal", label="", confidence=0.0)])
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()

    (row,) = tab.recent_rows()
    time_text, stream, what, confidence = row
    assert stream == "thermal"
    assert time_text
    assert what == "", f"an unnamed thing must be blank, not {what!r}"
    assert confidence == "", f"an unnamed thing must be blank, not {confidence!r}"
    for lie in ("0%", "0.0", "unknown", "none", "nothing"):
        assert lie not in confidence.lower()
        assert lie not in what.lower()


def test_the_list_says_that_blank_means_unidentified_not_uncertain(qtbot) -> None:
    tab, _, _ = build(qtbot, "thermal", events=FakeEvents())
    note = tab.movement_note().lower()
    assert "unidentified" in note
    assert "uncertain" in note


def test_a_named_event_shows_what_it_was_and_how_sure(qtbot) -> None:
    events = FakeEvents([movement(1, label="person", confidence=0.82)])
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()

    (row,) = tab.recent_rows()
    assert row[2] == "person"
    assert "82" in row[3]


def test_the_newest_movement_is_at_the_top(qtbot) -> None:
    events = FakeEvents(
        [
            movement(1, stream="thermal", started=1_770_000_000.0),
            movement(2, stream="visible", started=1_770_000_500.0),
        ]
    )
    tab, _, _ = build(qtbot, "thermal", "visible", events=events)
    tab.refresh()
    assert [row[1] for row in tab.recent_rows()] == ["visible", "thermal"]


def test_movement_after_the_clock_was_set_back_still_raises_the_alarm(qtbot) -> None:
    """The laptop's clock is set by hand, and gets set backwards.

    `recent()` orders by the time the movement happened, so an event recorded
    after the clock was wound back is no longer the first row - an older event
    with a later timestamp is. An alarm that compares only that first row's id
    against the highest id already seen never fires again until the clock has
    caught up with itself, which is a missed alarm on a system that exists to
    raise them.
    """
    events = FakeEvents([movement(1, stream="thermal", started=1_770_000_600.0)])
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()  # what was already there, established rather than alarmed about
    assert tab.alarm_visible() is False

    # The clock is corrected backwards by a minute; the next confirmed movement
    # carries an earlier timestamp than the one before it.
    events.events.append(movement(2, stream="thermal", started=1_770_000_540.0))
    tab.refresh()

    assert tab.alarm_visible() is True, "movement was recorded and nothing said so"
    assert "thermal" in tab.alarm_text()


def test_a_movement_database_that_started_again_still_raises_the_alarm(qtbot) -> None:
    """A replaced disk, or a database rebuilt after corruption, starts its ids
    at 1 again. A console left open across that must not go quiet for ever
    because it once saw id 5000."""
    events = FakeEvents([movement(5000, started=1_770_000_600.0)])
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()
    assert tab.alarm_visible() is False

    events.events = [movement(1, started=1_770_000_900.0)]
    tab.refresh()

    assert tab.alarm_visible() is True


def test_retention_taking_the_newest_event_does_not_invent_an_alarm(qtbot) -> None:
    """Events are deleted with the footage they point at. Nothing new has
    happened, so nothing may be announced."""
    events = FakeEvents(
        [movement(1, started=1_770_000_000.0), movement(2, started=1_770_000_600.0)]
    )
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()

    events.events = [e for e in events.events if e.id != 2]
    tab.refresh()

    assert tab.alarm_visible() is False


def test_the_list_is_not_rebuilt_when_nothing_has_moved(qtbot) -> None:
    """refresh() runs every two seconds for months. Rebuilding a table nobody
    changed is work done a million times a month for nothing."""
    events = FakeEvents([movement(1)])
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()
    assert tab.rebuilds == 1

    for _ in range(20):
        tab.refresh()
    assert tab.rebuilds == 1, "the list was rebuilt with nothing to show for it"

    events.events.append(movement(2, started=1_770_000_600.0))
    tab.refresh()
    assert tab.rebuilds == 2


def test_a_list_shortened_by_retention_is_redrawn(qtbot) -> None:
    """Retention deletes the oldest events with the footage they point at. The
    newest id does not change, so a signature made of it alone would leave rows
    on screen pointing at files that are gone."""
    events = FakeEvents([movement(1, started=1_770_000_000.0), movement(2, started=1_770_000_600.0)])
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()
    assert tab.rebuilds == 1

    events.events = [e for e in events.events if e.id != 1]
    tab.refresh()
    assert tab.rebuilds == 2
    assert len(tab.recent_rows()) == 1


def test_an_event_store_that_cannot_be_read_does_not_stop_the_pictures(qtbot) -> None:
    """Detection is the thing that fails here. The camera is not."""
    tab, _, panes = build(qtbot, "thermal", events=BrokenEvents())
    panes["thermal"].pretend_failed()
    tab.refresh()
    assert panes["thermal"].restarts == 1
    assert tab.alarm_visible() is False


def test_a_console_with_no_event_reader_still_shows_the_pictures(qtbot, caplog) -> None:
    """--no-services, or a machine where detection was never installed.

    Quietly, too. This runs every two seconds for months, and a console that
    complained each time would fill the Logs tab with the news that nothing is
    wrong."""
    tab, _, panes = build(qtbot, "thermal")
    panes["thermal"].pretend_failed()
    with caplog.at_level("ERROR", logger="vmd.desktop.live"):
        tab.refresh()
        tab.refresh()
    assert panes["thermal"].restarts == 1
    assert tab.recent_rows() == []
    assert caplog.records == [], "no reader is not a fault to report"


def test_rebuilding_the_panes_keeps_the_movement_already_listed(qtbot) -> None:
    """apply() replaces the panes when the streams change. The list of what
    moved belongs to the detector, not to the panes."""
    events = FakeEvents([movement(1)])
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()
    assert len(tab.recent_rows()) == 1

    tab.apply(settings_with("thermal", "visible"))
    assert len(tab.recent_rows()) == 1


# ---------------------------------------------------------------- storage
#
# "Disk filling" is one of the failure states the design calls first-class, and
# no tab showed free space, the budget, or how close either was to full.


def test_the_live_tab_shows_storage(qtbot, tmp_path) -> None:
    from vmd.desktop.disk import DiskWatcher

    watcher = DiskWatcher(
        settings_with("thermal"), executor=lambda work: work(), clock=lambda: 1000.0
    )
    tab = LiveTab(
        ptz=FakePtz(),
        make_pane=lambda name: FakeVideoPane(),
        local_url=lambda name: None,
        storage=watcher,
    )
    qtbot.addWidget(tab)
    tab.apply(settings_with("thermal"))
    assert tab.storage_lines(), "the right column has no storage in it"


def test_the_live_tab_redraws_storage_on_a_refresh(qtbot, tmp_path) -> None:
    from vmd.desktop.disk import DiskWatcher

    settings = settings_with("thermal")
    settings.storage.root = tmp_path / "rec"
    (tmp_path / "rec").mkdir()
    watcher = DiskWatcher(settings, executor=lambda work: work(), clock=lambda: 1000.0)
    tab = LiveTab(
        ptz=FakePtz(),
        make_pane=lambda name: FakeVideoPane(),
        local_url=lambda name: None,
        storage=watcher,
    )
    qtbot.addWidget(tab)
    tab.apply(settings)
    watcher.poll()
    tab.refresh()
    assert any("Drive" in text for text, _ in tab.storage_lines())


def test_a_live_tab_with_no_storage_watcher_still_works(qtbot) -> None:
    """--no-services opens a console with no folder to watch. It must cost the
    storage lines and nothing else."""
    tab, _, panes = build(qtbot, "thermal")
    tab.refresh()
    assert tab.storage_lines() == []
    assert set(panes) == {"thermal"}


# ------------------------------------------------------------------- the link
#
# The link is the bottleneck of this whole system: one camera at the far end of
# a Ubiquiti hop of more than 15 km carrying about 5 Mb/s. The console parsed
# nine figures off the radio and showed one of them, in the status bar. The
# design's side column - "steering, zoom, link, storage, recent movement" - had
# no link in it at all.


class CachedRadio:
    """A radio service that answers from a cache, as the real one does."""

    def __init__(self, status: dict | None = None) -> None:
        self._status = status or {
            "connected": True,
            "signal_dbm": -63,
            "noise_dbm": -96,
            "rx_mbps": 4.2,
            "rx_capacity_mbps": 18.0,
            "device": "LOCO-north",
            "age_seconds": 1.0,
        }
        self.asked = 0

    def status(self) -> dict:
        self.asked += 1
        return dict(self._status)


def test_the_live_tab_shows_the_link(qtbot) -> None:
    tab = LiveTab(
        ptz=FakePtz(),
        make_pane=lambda name: FakeVideoPane(),
        local_url=lambda name: None,
        radio=CachedRadio(),
    )
    qtbot.addWidget(tab)
    tab.apply(settings_with("thermal"))
    assert any("-63 dBm" in text for text, _ in tab.link_lines()), tab.link_lines()


def test_the_live_tab_redraws_the_link_on_a_refresh(qtbot) -> None:
    radio = CachedRadio({"connected": False, "checking": True})
    tab = LiveTab(
        ptz=FakePtz(),
        make_pane=lambda name: FakeVideoPane(),
        local_url=lambda name: None,
        radio=radio,
    )
    qtbot.addWidget(tab)
    tab.apply(settings_with("thermal"))
    assert any("hecking" in text for text, _ in tab.link_lines())
    radio._status = {"connected": True, "signal_dbm": -84, "age_seconds": 1.0}
    tab.refresh()
    assert any("-84 dBm" in text for text, _ in tab.link_lines())
    assert any(colour == PALETTE["alarm"] for _, colour in tab.link_lines())


def test_a_live_tab_with_no_radio_still_works(qtbot) -> None:
    """--no-services opens a console with no radio service. It must cost the
    link lines and nothing else."""
    tab, _, panes = build(qtbot, "thermal")
    tab.refresh()
    assert tab.link_lines() == []
    assert set(panes) == {"thermal"}


def test_the_link_panel_is_not_squeezed_by_the_rest_of_the_column(qtbot) -> None:
    """The column now carries streams, link, storage, movement and steering. On
    a laptop screen that is more than fits, and a Qt layout short of room does
    not shrink a wrapped sentence - it lays the next line over the tail of it.
    The sentence that gets cut is the three-line one saying the link is full,
    which is the whole reason the panel is there."""
    busy = {
        "connected": True,
        "signal_dbm": -84,
        "noise_dbm": -96,
        "ccq": 612.0,
        "tx_mbps": 0.5,
        "rx_mbps": 17.6,
        "tx_capacity_mbps": 24.0,
        "rx_capacity_mbps": 18.0,
        "distance_m": 15400,
        "uptime_s": 84231,
        "device": "LOCO-north",
        "age_seconds": 1.0,
    }
    tab = LiveTab(
        ptz=FakePtz(),
        make_pane=lambda name: FakeVideoPane(),
        local_url=lambda name: None,
        radio=CachedRadio(busy),
    )
    qtbot.addWidget(tab)
    tab.resize(1280, 720)
    tab.apply(settings_with("thermal", "visible"))
    tab.show()
    QApplication.processEvents()
    assert tab._link_panel.clipped() == [], "half a sentence is worse than none"


# ------------------------------------------------------------ the view modes
#
# Both pictures side by side, or one of them filling the wall. The browser
# console had this and the desktop one lost it, and the operator changes it all
# day - so the tests here are about the two things that make it usable rather
# than merely present: what is actually decoding, and what happens to a camera
# that is mid-slew when the mode changes.


def test_the_wall_offers_every_view_the_camera_has_and_no_others(qtbot) -> None:
    """A camera calls its views whatever it likes. Offering a fixed "visible
    only" on a machine with no visible stream would be offering a black
    rectangle."""
    two, _, _ = build(qtbot, "thermal", "visible")
    assert two.views.labels() == ["All", "thermal", "visible"]

    one, _, _ = build(qtbot, "thermal")
    assert one.views.labels() == ["All", "thermal"]


def test_choosing_one_view_fills_the_wall_with_it(qtbot) -> None:
    tab, _, _ = build(qtbot, "thermal", "visible")
    assert tab.shown_streams() == ["thermal", "visible"]

    tab.show_view("visible")
    assert tab.shown_streams() == ["visible"]
    assert tab.chosen_view() == "visible"

    tab.show_view("")
    assert tab.shown_streams() == ["thermal", "visible"]


def test_a_view_nobody_is_looking_at_actually_stops(qtbot) -> None:
    """The point of the mode, not a tidy-up after it. This laptop has one job
    and no headroom, and libVLC decoding a picture nobody can see costs it real
    processor time for nothing."""
    tab, _, panes = build(qtbot, "thermal", "visible")
    assert panes["visible"].url is not None

    tab.show_view("thermal")
    assert panes["visible"].url is None, "a hidden view has to stop, not play into nothing"
    assert panes["thermal"].url is not None

    tab.show_view("visible")
    assert panes["visible"].url is not None, "switching back has to bring it up again"


def test_a_stopped_view_is_not_mistaken_for_a_failed_one(qtbot) -> None:
    """`refresh` restarts what failed. A view the operator switched away from
    has not failed, and restarting it every two seconds would undo the mode."""
    tab, _, panes = build(qtbot, "thermal", "visible")
    tab.show_view("thermal")
    started = panes["visible"].restarts
    for _ in range(5):
        tab.refresh()
    assert panes["visible"].url is None
    assert panes["visible"].restarts == started


def test_the_number_keys_choose_the_view(qtbot) -> None:
    """Digits, because every other key on this tab is already steering."""
    tab, _, _ = build(qtbot, "thermal", "visible")
    qtbot.keyClick(tab, Qt.Key.Key_2)
    assert tab.chosen_view() == "thermal"
    qtbot.keyClick(tab, Qt.Key.Key_3)
    assert tab.chosen_view() == "visible"
    qtbot.keyClick(tab, Qt.Key.Key_1)
    assert tab.chosen_view() == ""


def test_a_number_key_with_no_view_behind_it_does_nothing(qtbot) -> None:
    tab, _, _ = build(qtbot, "thermal")
    qtbot.keyClick(tab, Qt.Key.Key_9)
    assert tab.chosen_view() == ""


def test_changing_the_view_while_an_arrow_is_held_still_stops_the_camera(
    qtbot,
) -> None:
    """The hazard the shortcuts had to be designed around. A head left slewing
    because a shortcut swallowed a key release is a hazard, not an
    inconvenience - and it is the failure this tab has already paid for once."""
    tab, ptz, _ = build(qtbot, "thermal", "visible")
    qtbot.keyPress(tab, Qt.Key.Key_Right)
    assert sent(tab, ptz)[-1][0] == "move"

    qtbot.keyClick(tab, Qt.Key.Key_2)
    assert tab.chosen_view() == "thermal"
    # Still moving: the operator asked for a different picture, not for the
    # camera to stop.
    assert sent(tab, ptz)[-1][0] == "move"

    qtbot.keyRelease(tab, Qt.Key.Key_Right)
    assert sent(tab, ptz)[-1] == ("stop",)


def test_the_view_buttons_never_take_the_keyboard_off_the_camera(qtbot) -> None:
    """A button that took focus would leave the next arrow key going nowhere
    until the operator clicked back on the picture."""
    tab, _, _ = build(qtbot, "thermal", "visible")
    for button in tab.views._buttons:
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_the_view_is_remembered_and_a_view_that_is_gone_is_not(qtbot) -> None:
    """An operator who wants thermal alone wants it tomorrow too - and a saved
    view whose stream has since been removed falls back to showing everything,
    which is the state that cannot hide anything."""
    tab, _, _ = build(qtbot, "thermal", "visible")
    remembered = settings_with("thermal", "visible")
    remembered.wall_view = "visible"
    tab.apply(remembered)
    assert tab.chosen_view() == "visible"
    assert tab.shown_streams() == ["visible"]

    gone = settings_with("thermal")
    gone.wall_view = "visible"
    tab.apply(gone)
    assert tab.chosen_view() == ""
    assert tab.shown_streams() == ["thermal"]


def test_a_camera_with_no_views_says_so_rather_than_showing_black(qtbot) -> None:
    tab, _, _ = build(qtbot)
    assert tab.shown_streams() == []
    assert tab._no_views.isVisibleTo(tab), "a black rectangle is not an explanation"


def test_the_movement_list_says_when_nothing_has_moved(qtbot) -> None:
    """An empty table is a black rectangle with a header on it, and a black
    rectangle is indistinguishable from a list that failed to load."""
    events = FakeEvents()
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()
    assert tab.recent_rows() == []
    assert tab._movement_empty.isVisibleTo(tab)
    assert tab._movement.isVisibleTo(tab) is False

    events.events.append(movement(1, stream="thermal", started=1_770_000_123.0))
    tab.refresh()
    assert tab._movement.isVisibleTo(tab)
    assert tab._movement_empty.isVisibleTo(tab) is False


def test_the_side_column_grows_with_the_window(qtbot) -> None:
    """It was 340 px on a 1366 laptop panel and 340 px on a 4K screen, which is
    the same paragraph wrapped to four lines with a third of the width wasted
    beside it."""
    tab, _, _ = build(qtbot, "thermal")
    # The rule, at sizes no machine running this has a screen for: a window
    # asked to be 3840 px wide on a 1080p desk is quietly given 1684.
    narrow = LiveTab.column_width(1366)
    wide = LiveTab.column_width(3840)
    assert wide > narrow
    assert 300 <= narrow <= 420 and 300 <= wide <= 420

    # And that a real resize actually applies it. Shown, because Qt holds a
    # resize event back until a widget is on screen.
    tab.show()
    tab.setGeometry(0, 0, 1366, 768)
    QApplication.processEvents()
    assert tab._side.maximumWidth() == LiveTab.column_width(tab.width())


def test_each_picture_is_labelled_on_the_picture(qtbot) -> None:
    """The name and the state used to be a list in the side column, three feet
    of screen away from the black rectangle they described."""
    tab, _, _ = build(qtbot, "thermal", "visible")
    label = tab._labels["thermal"]
    frame = tab._frames["thermal"]
    parent = label.parentWidget()
    assert parent is frame, "the label belongs to the picture it is about"
