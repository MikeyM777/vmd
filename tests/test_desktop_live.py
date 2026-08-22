"""The Live tab, driven against fake panes and a fake PTZ."""

from __future__ import annotations

import datetime
import sqlite3
import threading
import time

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

from vmd.desktop.live import PLAYING_BEFORE_FORGIVEN, LiveTab
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
                # detect=True: these are consoles that are watching. The model
                # defaults it off, which is right for a camera nobody has set
                # up yet and wrong for a fixture whose tests are about alarms.
                StreamSettings(
                    name=name,
                    url=f"rtsp://10.0.0.2/{name}",
                    enabled=True,
                    detect=True,
                )
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


class WidgetPane(QWidget):
    """A pane that is a real widget and asks for a window handle, as the libVLC
    one does.

    The steering is filtered off the pane now, so a test that wants to drag on a
    picture needs something a mouse event can actually be sent to - and the one
    thing that matters about `VlcVideoPane` here is the call to `winId()`, which
    is what turns every widget on the tab into a native window.
    """

    def __init__(self) -> None:
        super().__init__()
        self.url: str | None = None
        self.restarts = 0
        self.at_seconds = 0.0
        self._state = "stopped"

    @property
    def state(self) -> str:
        return self._state

    def show(self, url: str, at_seconds: float = 0.0) -> None:  # noqa: A003
        if self.url is not None:
            self.restarts += 1
        self.url = url
        self.at_seconds = float(at_seconds)
        self._state = "connecting"
        # What `VlcVideoPane._attach_surface` does, and the whole reason the
        # steering may not be a widget laid over this one.
        self.attach()

    def attach(self) -> int:
        return int(self.winId())

    def stop(self) -> None:
        self.url = None
        self._state = "stopped"

    def pretend_playing(self) -> None:
        self._state = "playing"


def build(qtbot, *names: str, events=None, register: bool = True, clock=None, pane=None):
    ptz = FakePtz()
    panes: dict = {}
    make = pane or FakeVideoPane

    def make_pane(name: str):
        panes[name] = make()
        return panes[name]

    tab = LiveTab(
        ptz=ptz,
        make_pane=make_pane,
        local_url=lambda name: f"rtsp://127.0.0.1:8554/{name}",
        events=events,
        clock=clock,
        # Read on the caller's thread, so no test here waits on one. In the
        # console the movement list is read on a worker, because events.db is
        # in the recordings folder and that is the folder that goes away.
        executor=lambda work: work(),
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


def keeps_playing(tab, pane, beats: int = PLAYING_BEFORE_FORGIVEN) -> None:
    """A stream that stays up, rather than one that is up for one reading."""
    pane.pretend_playing()
    for _ in range(beats):
        tab.refresh()


def test_a_stream_that_stays_back_forgets_the_backoff(qtbot) -> None:
    clock = HandWoundClock()
    tab, _, panes = build(qtbot, "thermal", clock=clock)
    for _ in range(30):
        panes["thermal"].pretend_failed()
        tab.refresh()
        clock.advance(2.0)

    keeps_playing(tab, panes["thermal"])
    assert "not coming back" not in tab.stream_label_text("thermal").lower()

    before = panes["thermal"].restarts
    panes["thermal"].pretend_failed()
    tab.refresh()
    assert panes["thermal"].restarts == before + 1, "a recovered stream waits again"


def test_a_stream_that_stays_back_and_fails_again_is_reported_again(qtbot, caplog) -> None:
    tab, _, panes = build(qtbot, "thermal")
    with caplog.at_level("WARNING", logger="vmd.desktop.live"):
        panes["thermal"].pretend_failed()
        tab.refresh()
        keeps_playing(tab, panes["thermal"])
        panes["thermal"].pretend_failed()
        tab.refresh()

    assert len(caplog.records) == 2


def test_a_stream_that_only_flaps_keeps_climbing_the_backoff(qtbot) -> None:
    """The fault as the operator's Logs tab showed it.

    A stream on a marginal link goes failed, playing, failed, playing. One good
    reading used to forgive the whole ladder, so it never climbed one: two
    seconds between restarts for as long as the console was open. The restarts
    were never the damage - the damage was that `%s failed; restarting it` at
    that rate evicts the 500-line ring the operator reads, which is how go2rtc's
    "401 Unauthorized" was lost while the fault was being hunted.
    """
    clock = HandWoundClock()
    tab, _, panes = build(qtbot, "thermal", clock=clock)
    restarts_seen = []
    for _ in range(12):
        panes["thermal"].pretend_failed()
        tab.refresh()
        restarts_seen.append(panes["thermal"].restarts)
        # One good reading and then straight back to failed: not a recovery.
        panes["thermal"].pretend_playing()
        tab.refresh()
        clock.advance(4.0)

    # The ladder is 2, 4, 8, 16, 32, then a minute. Four seconds between
    # attempts stops being enough almost at once, so a flapping stream is
    # restarted a handful of times rather than on every single failure.
    assert panes["thermal"].restarts <= 5, (
        f"restarted {panes['thermal'].restarts} times in twelve failures"
    )
    assert restarts_seen[-1] == restarts_seen[-2], "it should have stopped by now"


def test_a_stream_a_dead_server_keeps_refusing_climbs_the_backoff(qtbot, caplog) -> None:
    """The operator's Logs tab, three minutes of it, at the pane's own pace.

    This is the shape the field actually produces, and it has no `playing` in it
    at all: the pane is restarted, libVLC spends a few seconds failing to set up
    the RTSP session, and the pane reports `failed` again - so the console sees
    failed, connecting, connecting, failed, for as long as it is open. None of
    connecting, late or stopped is a recovery, and if any of them forgave the
    ladder the way one `playing` reading used to, this stream would be restarted
    every six seconds for ever and the Logs tab would carry nothing else.

    Six seconds is the measured spacing from his laptop, and it is why the first
    two rungs - two seconds and four - are invisible there: they are shorter
    than libVLC's own time to give up, so the first three restarts are that far
    apart however the ladder is set. It has to have climbed past them by the
    end of three minutes, and the line has to have stopped being written.
    """
    clock = HandWoundClock()
    tab, _, panes = build(qtbot, "thermal", clock=clock)
    pane = panes["thermal"]
    since_restart = 99

    with caplog.at_level("WARNING", logger="vmd.desktop.live"):
        for _ in range(90):  # 180 s of heartbeats
            if since_restart >= 3:  # libVLC takes about six seconds to give up
                pane.pretend_failed()
            before = pane.restarts
            tab.refresh()
            since_restart = 0 if pane.restarts > before else since_restart + 1
            clock.advance(2.0)

    assert pane.restarts >= 3, "it must go on trying"
    assert pane.restarts <= 10, (
        f"{pane.restarts} restarts in three minutes: the ladder never climbed"
    )
    assert len(caplog.records) <= 4, (
        f"{len(caplog.records)} lines about one stream in three minutes evicts "
        "everything that explains it from a 500-line ring"
    )


def test_saving_something_else_does_not_forgive_a_camera_that_is_off(qtbot) -> None:
    """A Save that corrects the storage folder has said nothing about a camera
    that is switched off, and it used to put every pane back on the bottom rung
    of the ladder."""
    clock = HandWoundClock()
    tab, _, panes = build(qtbot, "thermal", clock=clock)
    for _ in range(8):
        panes["thermal"].pretend_failed()
        tab.refresh()
        clock.advance(64.0)
    climbed = panes["thermal"].restarts
    assert climbed >= 6, "the ladder has to have been climbed for this to mean anything"

    # The same streams, the same addresses: nothing here is about the camera.
    tab.apply(settings_with("thermal"))
    assert tab._restarts.get("thermal", 0) == climbed, (
        "a Save that touched nothing may not reset the backoff"
    )

    # And the ladder goes on from where it was rather than starting again.
    clock.advance(64.0)
    tab._panes["thermal"].pretend_failed()
    tab.refresh()
    assert tab._restarts["thermal"] == climbed + 1


def test_a_stream_whose_address_changed_starts_again_from_the_bottom(qtbot) -> None:
    """The other half: correcting the address IS a reason to try at once."""
    clock = HandWoundClock()
    tab, _, panes = build(qtbot, "thermal", clock=clock)
    for _ in range(8):
        panes["thermal"].pretend_failed()
        tab.refresh()
        clock.advance(64.0)

    corrected = settings_with("thermal")
    corrected.camera.streams[0].url = "rtsp://10.0.0.9/thermal"
    tab.apply(corrected)
    assert tab._restarts.get("thermal", 0) == 0


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

    tab._movement_line.setFocus()
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
    assert tab.announced(), "movement went unannounced while the camera hung"
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


# ------------------------------------------------- steering from the picture
#
# There is no widget over the video any more. A drag on the picture itself is
# what steers, so these send their mouse events to the pane.


def test_dragging_near_the_right_edge_pans_right_and_release_stops(qtbot) -> None:
    tab, ptz, panes = build(qtbot, "thermal", pane=WidgetPane)
    picture = panes["thermal"]
    picture.resize(200, 100)

    qtbot.mousePress(picture, Qt.MouseButton.LeftButton, pos=QPoint(198, 50))
    assert sent(tab, ptz)[-1][0] == "move"
    assert sent(tab, ptz)[-1][1] > 0.0

    qtbot.mouseMove(picture, QPoint(190, 50))
    assert sent(tab, ptz)[-1][1] > 0.0

    qtbot.mouseRelease(picture, Qt.MouseButton.LeftButton, pos=QPoint(190, 50))
    assert sent(tab, ptz)[-1] == ("stop",)


def test_the_pointer_does_not_steer_unless_a_button_is_held(qtbot) -> None:
    tab, ptz, panes = build(qtbot, "thermal", pane=WidgetPane)
    picture = panes["thermal"]
    picture.resize(200, 100)
    qtbot.mouseMove(picture, QPoint(198, 50))
    assert sent(tab, ptz) == []


def test_the_pointer_leaving_the_picture_stops_the_camera(qtbot) -> None:
    tab, ptz, panes = build(qtbot, "thermal", pane=WidgetPane)
    picture = panes["thermal"]
    picture.resize(200, 100)
    qtbot.mousePress(picture, Qt.MouseButton.LeftButton, pos=QPoint(198, 50))
    assert sent(tab, ptz)[-1][0] == "move"
    QApplication.instance().sendEvent(picture, QEvent(QEvent.Type.Leave))
    assert sent(tab, ptz)[-1] == ("stop",)


def test_dragging_the_second_picture_steers_by_where_it_is_in_that_picture(qtbot) -> None:
    """Each picture is its own frame. Near the right edge of the visible view
    means the same thing as near the right edge of the thermal one - it used to
    be measured across the whole wall, where the right edge of the left-hand
    picture was the middle and steered nothing."""
    tab, ptz, panes = build(qtbot, "thermal", "visible", pane=WidgetPane)
    picture = panes["visible"]
    picture.resize(200, 100)

    qtbot.mousePress(picture, Qt.MouseButton.LeftButton, pos=QPoint(198, 50))
    assert sent(tab, ptz)[-1][0] == "move"
    assert sent(tab, ptz)[-1][1] > 0.0
    qtbot.mouseRelease(picture, Qt.MouseButton.LeftButton, pos=QPoint(198, 50))


def test_nothing_is_laid_over_the_pictures(qtbot) -> None:
    """The bug this whole arrangement exists to prevent, asserted structurally.

    `VlcVideoPane` asks for a window handle so libVLC has something to draw
    into. Qt answers by making every widget on this tab a native window of its
    own - measured: the steering overlay's `internalWinId()` was 0 before a pane
    asked and non-zero afterwards. A native window is its own surface in the
    stacking order, so anything overlapping a picture can hide it outright,
    while the pane goes on counting frames and reporting `playing` in green.

    So the rule is not "the thing over the video must be transparent" - it is
    that there is nothing over the video. Ancestors are exempt: a pane is inside
    its frame, its splitter and this tab by construction.
    """
    tab, _, panes = build(qtbot, "thermal", "visible", pane=WidgetPane)
    tab.resize(900, 600)
    tab.show()
    qtbot.waitExposed(tab)
    for picture in panes.values():
        assert picture.attach(), "the pane never asked for a window handle"

    def ancestors(widget) -> set:
        seen = set()
        parent = widget.parentWidget()
        while parent is not None:
            seen.add(parent)
            parent = parent.parentWidget()
        return seen

    covered: list[str] = []
    for name, picture in panes.items():
        if not picture.isVisible():
            continue
        above = ancestors(picture)
        for other in tab.findChildren(QWidget):
            if other is picture or other in above or not other.isVisible():
                continue
            if other in ancestors(picture) or picture in ancestors(other):
                continue
            here = picture.rect().translated(picture.mapToGlobal(QPoint(0, 0)))
            there = other.rect().translated(other.mapToGlobal(QPoint(0, 0)))
            if here.intersects(there):
                covered.append(
                    f"{type(other).__name__}({other.objectName() or 'unnamed'}) "
                    f"covers {name}; its own window is {other.internalWinId()}"
                )
    assert not covered, covered


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
    assert tab.announced() is False

    events.events.append(movement(1, stream="thermal", started=1_770_000_123.0))
    tab.refresh()

    assert tab.announced() is True
    assert "thermal" in tab.alarm_text()
    assert clock_text(1_770_000_123.0) in tab.alarm_text()
    

def test_acknowledging_clears_the_alarm(qtbot) -> None:
    events = FakeEvents()
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()
    events.events.append(movement(1))
    tab.refresh()
    assert tab.announced() is True

    tab.acknowledge()

    assert tab.announced() is False
    # And it stays cleared: the same event must not raise it again on the next
    # tick, two seconds later, forever.
    tab.refresh()
    assert tab.announced() is False


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

    assert tab.announced() is False
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
    assert tab.announced() is False

    # The clock is corrected backwards by a minute; the next confirmed movement
    # carries an earlier timestamp than the one before it.
    events.events.append(movement(2, stream="thermal", started=1_770_000_540.0))
    tab.refresh()

    assert tab.announced() is True, "movement was recorded and nothing said so"
    assert "thermal" in tab.alarm_text()


def test_a_movement_database_that_started_again_still_raises_the_alarm(qtbot) -> None:
    """A replaced disk, or a database rebuilt after corruption, starts its ids
    at 1 again. A console left open across that must not go quiet for ever
    because it once saw id 5000."""
    events = FakeEvents([movement(5000, started=1_770_000_600.0)])
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()
    assert tab.announced() is False

    events.events = [movement(1, started=1_770_000_900.0)]
    tab.refresh()

    assert tab.announced() is True


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

    assert tab.announced() is False


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
    assert tab.announced() is False


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


def test_the_movement_line_says_when_nothing_has_moved(qtbot) -> None:
    """The table is gone from the column - he asked for it - and the record is
    not. What is left is one line, and it still has to say which nothing this
    is: nothing has moved, or nothing is watching."""
    events = FakeEvents()
    tab, _, _ = build(qtbot, "thermal", events=events)
    tab.refresh()
    assert tab.recent_rows() == []
    assert "nothing has moved" in tab.movement_note().lower()
    assert tab._movement_line.isEnabled() is False, "a press that would do nothing"

    events.events.append(movement(1, stream="thermal", started=1_770_000_123.0))
    tab.refresh()
    said = tab.movement_note().lower()
    assert "1 movement" in said, said
    assert tab._movement_line.isEnabled() is True


def test_the_movement_line_counts_what_there_is_and_names_the_newest(qtbot) -> None:
    """A count and a time is the whole of what a glance needs. The list of every
    movement is a tab away, which is where he said it belongs."""
    tab, _, _ = build(
        qtbot,
        "thermal",
        events=FakeEvents([movement(1), movement(2, started=1_770_000_100.0)]),
    )
    tab.refresh()
    said = tab.movement_note().lower()
    assert "2 movements" in said, said
    assert "watch" in said, "it never says it can be pressed"


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


# ------------------------------------- the radio's paragraph, cut to one line
#
# When the radio refuses the login the panel printed fourteen wrapped grey lines
# - the code, the address, every login flow that was tried - ending "Run
# spike/probe_radio.py against this radio and send what it prints". This
# operator has no terminal. The detail is not deleted; it moves to the Logs tab,
# where technical detail belongs and where it is one click away, and the panel
# keeps the one line he can act on.

REFUSED_403 = (
    "the radio answered HTTP 403 (Forbidden) to the login at "
    "http://192.168.1.20/login.cgi. It is reachable and it refused the request, "
    "which need not mean the password is wrong: airOS also answers 403 to a "
    "login sent without the session cookie from its own login page, to one that "
    "does not look like it came from that page, and after too many tries. All "
    "login flows were tried. Run spike/probe_radio.py against this radio and "
    "send what it prints."
)


def link_tab(qtbot, radio):
    tab = LiveTab(
        ptz=FakePtz(),
        make_pane=lambda name: FakeVideoPane(),
        local_url=lambda name: None,
        radio=radio,
    )
    qtbot.addWidget(tab)
    tab.apply(settings_with("thermal"))
    return tab


def test_a_refused_login_is_one_line_the_operator_can_act_on(qtbot) -> None:
    radio = CachedRadio({"connected": False, "reason": REFUSED_403, "age_seconds": 2.0})
    tab = link_tab(qtbot, radio)

    lines = tab.link_lines()
    assert lines, "the panel said nothing about a radio that refused the login"
    first = lines[0][0]
    assert "Settings" in first, first
    # The whole point: it is a line, not a paragraph.
    assert len(first) <= 200, first
    for jargon in ("probe_radio", ".py", "cookie", "HTTP", "login.cgi", "airOS", "403"):
        assert jargon not in first, f"{jargon!r} is still on the Live tab: {first}"


def test_the_radio_s_own_words_survive_being_shortened(qtbot) -> None:
    """"Invalid credentials." is the useful part of the whole paragraph, and the
    console does not paraphrase it into something the radio did not say."""
    said = (
        'the radio refused the login and said so: "Invalid credentials." '
        "(HTTP 403 from http://192.168.1.20/login.cgi). Those are the radio's "
        "own words - check the username and the password in Settings."
    )
    tab = link_tab(qtbot, CachedRadio({"connected": False, "reason": said, "age_seconds": 2.0}))
    first = tab.link_lines()[0][0]
    assert "Invalid credentials." in first, first
    assert "login.cgi" not in first


def test_the_detail_goes_to_the_logs_once_and_not_every_heartbeat(qtbot, caplog) -> None:
    """The Logs tab is a 500-line ring and it is the only diagnostic on this
    machine. A paragraph repeated thirty times a minute destroys it."""
    radio = CachedRadio({"connected": False, "reason": REFUSED_403, "age_seconds": 2.0})
    with caplog.at_level("WARNING", logger="vmd.desktop.live"):
        tab = link_tab(qtbot, radio)
        for _ in range(5):
            tab.refresh()
    said = [r for r in caplog.records if "probe_radio" in r.getMessage()]
    assert len(said) == 1, [r.getMessage() for r in said]


def test_a_healthy_radio_is_not_rewritten(qtbot) -> None:
    tab = link_tab(qtbot, CachedRadio())
    assert any("-63 dBm" in text for text, _ in tab.link_lines()), tab.link_lines()


# ------------------------------------------------------- take him to the footage
#
# The console never navigated the operator anywhere: an alarm fired and he had
# to change tab, pick the day, pick the stream and hit a three-pixel mark on a
# timeline, all under the pressure the alarm just created. The tab does not own
# the Playback tab and never will, so it says what was asked for and the window
# does the rest.


def moved(tab, event) -> None:
    """Put one movement in front of the tab, as the heartbeat would."""
    tab.refresh()  # the first read only learns what was already there
    tab._events.events.append(event)
    tab.refresh()


def test_the_outline_takes_itself_off_after_half_a_minute(qtbot) -> None:
    """Nothing dismisses it any more - the button that did went with the strip.

    So it has to come off on its own, or a gust at 03:40 leaves one picture
    outlined in red until somebody restarts the console, and the outline stops
    meaning "just now" and starts meaning nothing at all.
    """
    from vmd.desktop.live import OUTLINE_SECONDS

    now = [1000.0]
    tab, _ptz, _panes = build(
        qtbot, "thermal", events=FakeEvents([movement(1)]), clock=lambda: now[0]
    )
    moved(tab, movement(2, started=1_770_000_100.0))
    assert tab.announced()
    assert tab.outlined_stream() == "thermal"

    now[0] += OUTLINE_SECONDS - 1.0
    tab.refresh()
    assert tab.outlined_stream() == "thermal", "it came off while it still meant now"

    now[0] += 2.0
    tab.refresh()
    assert tab.outlined_stream() is None
    assert not tab.announced()


def test_show_me_asks_for_nothing_when_there_is_no_alarm(qtbot) -> None:
    """The strip is hidden, so the button is not reachable - but a stray click
    from a test, a shortcut or a future keyboard path must not send the window
    off to a movement that does not exist."""
    tab, _ptz, _panes = build(qtbot, "thermal", events=FakeEvents([]))
    asked: list = []
    tab.show_footage.connect(asked.append)
    tab.show_the_footage()
    assert asked == []


def test_pressing_the_movement_line_asks_for_the_newest_footage(qtbot) -> None:
    """Where he looks for the one that happened four minutes ago, rather than
    the one happening now. The strip answers for the one happening now."""
    tab, _ptz, _panes = build(
        qtbot,
        "thermal",
        events=FakeEvents([movement(1), movement(2, started=1_770_000_100.0)]),
    )
    tab.refresh()
    assert len(tab.recent_rows()) == 2

    asked: list = []
    tab.show_footage.connect(asked.append)
    tab._movement_line.click()
    assert len(asked) == 1
    assert asked[0] is tab._shown[0], "took him to the wrong movement"


def test_pressing_the_movement_line_with_nothing_behind_it_asks_for_nothing(
    qtbot,
) -> None:
    """Nothing having moved is a real state, and a control that answers a press
    with somebody else's event - or with an exception - is worse than one that
    says it has nothing."""
    tab, _ptz, _panes = build(qtbot, "thermal", events=FakeEvents())
    tab.refresh()
    asked: list = []
    tab.show_footage.connect(asked.append)
    tab._show_newest()
    assert asked == []

# ------------------------------------------------ how soon the room is told
#
# The movement list used to be read on the window's two-second heartbeat and
# nothing else, so up to two whole seconds passed between the detector writing a
# row and the sound being made - on top of everything the camera, the link and
# the detector had already spent. It was the largest fixable part of "after a
# couple of seconds it beeped", and the only part that costs nothing to remove.


def test_the_movement_list_is_read_on_its_own_timer_and_not_the_heartbeat(qtbot) -> None:
    from vmd.desktop.live import MOVEMENT_POLL_MS

    tab, _ptz, _panes = build(qtbot, "thermal", events=FakeEvents([]))
    assert tab._movement_timer.isActive()
    assert tab._movement_timer.interval() == MOVEMENT_POLL_MS
    # A quarter of a second at most, which is below what anybody perceives. The
    # heartbeat this replaces is two seconds.
    assert MOVEMENT_POLL_MS <= 500


def test_the_timer_really_reads_the_movement_list(qtbot) -> None:
    """The connection, not just the interval: a timer wired to nothing keeps
    perfect time and tells nobody anything."""
    events = FakeEvents()
    tab, _ptz, _panes = build(qtbot, "thermal", events=events)
    tab.refresh()  # the first read only learns what was already there

    events.events.append(movement(1, stream="thermal"))
    tab._movement_timer.timeout.emit()

    assert tab.announced(), "the timer fired and nothing was read"


def test_a_tab_with_no_movement_list_does_not_run_a_timer_for_nothing(qtbot) -> None:
    """A console with no events database is an ordinary state - detection off,
    or a folder that has gone - and four wake-ups a second for a question
    nobody can answer is a machine that runs for months doing it."""
    tab, _ptz, _panes = build(qtbot, "thermal", events=None)
    assert not tab._movement_timer.isActive()


def test_closing_the_tab_stops_the_timer(qtbot) -> None:
    """It outlives the window otherwise, reading a database the console has
    closed on the way out."""
    tab, _ptz, _panes = build(qtbot, "thermal", events=FakeEvents([]))
    assert tab._movement_timer.isActive()
    tab.shutdown()
    assert not tab._movement_timer.isActive()


# ------------------------------------------- a camera that has been switched off
#
# "Although I turned off the VMD for the thermal it's still beeping in the
# thermal camera."
#
# It really was off where the watching happens: `detected_streams` drops the
# camera and the detector is restarted without it. But the SOUND is not made
# there - it is made here, from whatever rows are in events.db - and this tab
# announced every one of them whatever the settings said.


def watching(*names: str, master: bool = True):
    """Settings with detection ticked on exactly these streams."""
    from vmd.settings import Settings, StreamSettings

    settings = Settings()
    settings.detection.enabled = master
    settings.camera.streams = [
        StreamSettings(
            name=name, url=f"rtsp://camera/{name}", enabled=True, detect=name in names
        )
        for name in ("thermal", "visible")
    ]
    return settings


def test_a_camera_switched_off_makes_no_sound_however_the_row_got_there(qtbot) -> None:
    """A detector that has not finished dying, one left over from an earlier
    run, or rows written before the switch was thrown - none of them may make a
    noise about a camera the operator has switched off."""
    events = FakeEvents()
    tab, _ptz, _panes = build(qtbot, "thermal", "visible", events=events)
    tab.apply(watching("visible"))
    tab.refresh()

    events.events.append(movement(1, stream="thermal"))
    tab.refresh()

    assert not tab.announced(), "it announced movement on a camera that is switched off"
    assert tab.outlined_stream() is None
    assert tab.recent_rows() == [], "it was drawn in the column as well"


def test_the_camera_that_is_still_on_still_announces(qtbot) -> None:
    """The other half, and the half that matters: switching one off must not
    quietly switch the other off too."""
    events = FakeEvents()
    tab, _ptz, _panes = build(qtbot, "thermal", "visible", events=events)
    tab.apply(watching("visible"))
    tab.refresh()

    events.events.append(movement(1, stream="visible"))
    tab.refresh()

    assert tab.announced()
    assert "visible" in tab.alarm_text()


def test_the_master_switch_silences_every_camera(qtbot) -> None:
    events = FakeEvents()
    tab, _ptz, _panes = build(qtbot, "thermal", "visible", events=events)
    tab.apply(watching("thermal", "visible", master=False))
    tab.refresh()

    events.events.append(movement(1, stream="thermal"))
    events.events.append(movement(2, stream="visible"))
    tab.refresh()

    assert not tab.announced()


def test_switching_a_camera_back_on_does_not_announce_the_night_it_missed(
    qtbot,
) -> None:
    """The trap in filtering. Every event is remembered as seen whether or not
    it was announced - so turning thermal back on tomorrow morning does not
    raise an alarm for movement that happened while it was off, which would be
    a console shouting about something eight hours old."""
    events = FakeEvents()
    tab, _ptz, _panes = build(qtbot, "thermal", "visible", events=events)
    tab.apply(watching("visible"))
    tab.refresh()

    events.events.append(movement(1, stream="thermal"))
    tab.refresh()
    assert not tab.announced()

    tab.apply(watching("thermal", "visible"))
    tab.refresh()
    assert not tab.announced(), "it shouted about movement from while it was off"

    # And something that moves now is announced normally.
    events.events.append(movement(2, stream="thermal"))
    tab.refresh()
    assert tab.announced()
    assert "thermal" in tab.alarm_text()


def test_a_tab_that_has_not_been_told_anything_announces_everything(qtbot) -> None:
    """The safe direction for the unknown case. A console that has not been
    given its settings must not sit silent through an intrusion because a set
    was never delivered: failing open on an alarm is right, failing closed is
    how a system kills somebody."""
    # Built directly, because `build` applies a settings object and this is
    # about the state before any settings have arrived at all.
    events = FakeEvents()
    tab = LiveTab(
        ptz=FakePtz(),
        make_pane=lambda name: FakeVideoPane(),
        local_url=lambda name: f"rtsp://127.0.0.1:8554/{name}",
        events=events,
        executor=lambda work: work(),
    )
    qtbot.addWidget(tab)
    tab.refresh()

    events.events.append(movement(1, stream="thermal"))
    tab.refresh()

    assert tab.announced()


# --------------------------------------------------- a box on the live picture
#
# "I want a box on the view when the VMD detects something, like YOLO does but
# without the classifying text."
#
# It is handed to libVLC as a subpicture rather than drawn over the video - see
# vmd/desktop/boxes.py for why that is the only way - and it is off until it has
# been seen working on a real camera.


class BoxPane(FakeVideoPane):
    """A pane that can be asked to show an overlay, and remembers being asked."""

    def __init__(self, size=(1920, 1080)) -> None:
        super().__init__()
        self._size = size
        self.shown: list[str] = []
        self.hidden = 0

    def video_size(self):
        return self._size

    def show_overlay(self, path: str) -> bool:
        self.shown.append(path)
        return True

    def hide_overlay(self) -> bool:
        self.hidden += 1
        return True


def boxed(qtbot, on: bool, size=(1920, 1080), clock=None):
    """A tab whose panes can take an overlay, with boxes on or off."""
    from vmd.settings import Settings

    events = FakeEvents()
    tab, _ptz, panes = build(
        qtbot, "thermal", events=events, pane=lambda: BoxPane(size), clock=clock
    )
    settings = settings_with("thermal")
    settings.show_boxes = on
    tab.apply(settings)
    return tab, events, panes


def test_a_box_is_drawn_at_the_video_size_and_shown_on_the_pane(qtbot) -> None:
    """The box is in FRAME coordinates and the overlay is drawn at the video's
    own size, so it lands on the thing by construction."""
    from pathlib import Path

    tab, events, panes = boxed(qtbot, on=True)
    tab.refresh()
    events.events.append(movement(1, stream="thermal"))
    tab.refresh()

    pane = panes["thermal"]
    assert pane.shown, "nothing was put on the picture"
    written = Path(pane.shown[-1])
    assert written.is_file(), "the pane was given a file that was never written"

    from PySide6.QtGui import QImage

    overlay = QImage(str(written))
    assert (overlay.width(), overlay.height()) == (1920, 1080), (
        "the overlay is not the size of the video, so the box lands in the wrong place"
    )


def test_no_box_is_drawn_when_it_has_not_been_asked_for(qtbot) -> None:
    tab, events, panes = boxed(qtbot, on=False)
    tab.refresh()
    events.events.append(movement(1, stream="thermal"))
    tab.refresh()

    assert panes["thermal"].shown == []


def test_a_pane_with_no_picture_yet_is_not_drawn_on(qtbot) -> None:
    """A pane reports no size until libVLC has a picture. There is nothing to
    draw on and nothing to work out the coordinates against."""
    tab, events, panes = boxed(qtbot, on=True, size=(0, 0))
    tab.refresh()
    events.events.append(movement(1, stream="thermal"))
    tab.refresh()

    assert panes["thermal"].shown == []


def test_the_box_comes_off_by_itself(qtbot) -> None:
    """A box that stays stops meaning "this is where it was just now" and starts
    looking like something that is still there."""
    from vmd.desktop.boxes import BOX_SECONDS

    now = [1000.0]
    tab, events, panes = boxed(qtbot, on=True, clock=lambda: now[0])
    tab.refresh()
    events.events.append(movement(1, stream="thermal"))
    tab.refresh()
    assert panes["thermal"].shown

    now[0] += BOX_SECONDS - 1.0
    tab.refresh()
    assert panes["thermal"].hidden == 0, "it came off while it still meant now"

    now[0] += 2.0
    tab.refresh()
    assert panes["thermal"].hidden == 1


def test_the_file_name_changes_every_time(qtbot) -> None:
    """libVLC is being asked to re-read a file it has already read. An image
    loader that caches by name would draw the first box for ever."""
    tab, events, panes = boxed(qtbot, on=True)
    tab.refresh()
    for index in (1, 2, 3):
        events.events.insert(0, movement(index, stream="thermal"))
        tab.refresh()

    shown = panes["thermal"].shown
    assert len(shown) >= 2
    assert shown[-1] != shown[-2], "the same name twice running"


def test_a_camera_that_is_switched_off_gets_no_box_either(qtbot) -> None:
    """The box follows the announcement, and the announcement follows the
    detection switch."""
    from vmd.settings import Settings

    events = FakeEvents()
    tab, _ptz, panes = build(
        qtbot, "thermal", "visible", events=events, pane=lambda: BoxPane()
    )
    settings = watching("visible")
    settings.show_boxes = True
    tab.apply(settings)
    tab.refresh()

    events.events.append(movement(1, stream="thermal"))
    tab.refresh()

    assert panes["thermal"].shown == []


def test_the_box_is_turned_with_a_picture_that_is_shown_upside_down(qtbot) -> None:
    """"The box that marks the movement is upside down. I've flipped the view
    and the mark boxes are still on the opposite side."

    libVLC applies the transform to the VIDEO and blends subpictures over the
    result, so a box handed over in frame coordinates lands at the point
    reflection of where it belongs. A person at the bottom left is boxed at the
    top right - which is very often static scenery, and makes the whole system
    look like it is marking things that are not moving.
    """
    from vmd.desktop.boxes import turned

    # A person low on the left of a 640x512 thermal.
    assert turned((60, 400, 30, 70), 640, 512) == (550, 42, 30, 70)
    # Turning it twice is where it started.
    assert turned(turned((60, 400, 30, 70), 640, 512), 640, 512) == (60, 400, 30, 70)


def test_the_overlay_is_drawn_where_the_movement_was(qtbot, tmp_path) -> None:
    """The mark that reaches the file is at the position it was given.

    This was a pair - plain and flipped - until the switch that turned the
    live picture was taken out. What it still guards is the half that was
    always the point: the box lands where the detector said it was.
    """
    from PySide6.QtGui import QImage

    from vmd.desktop.boxes import draw

    plain = tmp_path / "plain.png"
    assert draw(plain, 640, 512, [(60, 400, 30, 70)])

    def where(path):
        picture = QImage(str(path))
        inked = [
            (x, y)
            for y in range(picture.height())
            for x in range(picture.width())
            if picture.pixelColor(x, y).alpha() > 0
        ]
        return min(x for x, _ in inked), min(y for _, y in inked)

    assert where(plain)[0] < 200, where(plain)
    assert where(plain)[1] > 300, where(plain)
