"""The Live tab, driven against fake panes and a fake PTZ."""

from __future__ import annotations

import datetime
import sqlite3

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


def build(qtbot, *names: str, events=None, register: bool = True):
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
    )
    # A tab that is about to be given to a parent widget is left unregistered:
    # qtbot would then close and delete it twice over.
    if register:
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


def test_a_stream_that_will_never_come_back_does_not_fill_the_log(qtbot, caplog) -> None:
    """A camera that is off fails on every tick for as long as the console is
    open - thirty lines a minute into a ring that holds five hundred. Within
    twenty minutes the Logs tab holds nothing else, and go2rtc's "401
    Unauthorized", the line that says why, has been pushed out of the only
    place the operator can read it."""
    tab, _, panes = build(qtbot, "thermal")
    panes["thermal"].pretend_failed()

    with caplog.at_level("WARNING", logger="vmd.desktop.live"):
        for _ in range(200):
            panes["thermal"].pretend_failed()
            tab.refresh()

    assert panes["thermal"].restarts == 200, "it must still be restarted every time"
    assert len(caplog.records) <= 5, "the log was flooded by one dead stream"
    assert caplog.records, "and it must not go silent about it either"
    assert any("200" in record.getMessage() for record in caplog.records), (
        "the reminder has to say how many times, or it reads like the first one"
    )


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
    assert ptz.commands[-1] == ("move", 0.5, 0.0, 0.0)

    tabs.setCurrentIndex(1)

    assert ptz.commands[-1] == ("stop",), "the head was left slewing on another tab"
    # And the key it never saw released is forgotten, so coming back to the tab
    # does not start with a ghost held down.
    tabs.setCurrentIndex(0)
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
