"""The window: four tabs, a status line, and children that outlive a close.

Also the entry point around it. Everything `main()` does except making a
QApplication and running it is a plain function, tested here without a display:
a console that cannot be started on the field laptop is not something anyone
finds out about from a GUI test.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel

from vmd.desktop.app import build_wiring, default_settings_path, pane_factory, parse_args
from vmd.desktop.video import FakeVideoPane
from vmd.desktop.settings_tab import SettingsTab
from vmd.desktop.window import ConsoleWindow
from vmd.settings import (
    CameraSettings,
    Settings,
    StorageSettings,
    StreamSettings,
    load_settings,
    save_settings,
)


# Anything the console reads off the recordings folder is read on a worker now,
# so an assertion about it is an assertion with a moment's delay in it. Every one
# of them waits - bounded, and by beating the console rather than by sleeping, so
# that a reading which never arrives fails the test instead of hanging it.
BEAT_TIMEOUT = 10.0


def beating(window, ready, timeout: float = BEAT_TIMEOUT) -> bool:
    """Heartbeat the console until `ready()`, or give up and say so."""
    import time as _time

    from PySide6.QtWidgets import QApplication

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        window.heartbeat()
        if ready():
            return True
        QApplication.processEvents()
        _time.sleep(0.02)
    return False


# Applying a save happens on a worker now - it restarts child processes, and the
# window may not freeze while it does - so "the save reached the running system"
# is a question with a moment's delay in it. Every assertion about it waits,
# bounded, so that a save which never lands fails the test instead of hanging it.
SAVE_TIMEOUT = 10.0


def save_applied(window, timeout: float = SAVE_TIMEOUT) -> bool:
    """Wait until the save has been put into effect, or give up and say so.

    The Save button coming back is the console's own signal that it is done:
    it is held for exactly as long as the children are being restarted.
    """
    import time as _time

    from PySide6.QtWidgets import QApplication

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        QApplication.processEvents()
        if window.settings_tab.save_button.isEnabled():
            return True
        _time.sleep(0.01)
    return False


class FakeServices:
    def __init__(self) -> None:
        self.ticks = 0
        self.stopped = False
        self.applied: list = []
        # How often the whole state has been composed. On the real services
        # that means the recorder's sentence, the disk reading and the
        # detector's report, and a heartbeat that asks for it twice pays for
        # all three twice.
        self.state_calls = 0

    def apply(self, settings) -> None:
        self.applied.append(settings)

    def start(self) -> None: ...

    def tick(self) -> list[str]:
        self.ticks += 1
        return []

    def stop(self) -> None:
        self.stopped = True

    def local_url(self, name: str) -> str | None:
        return f"rtsp://127.0.0.1:8554/{name}"

    def state(self) -> dict:
        self.state_calls += 1
        return {
            "recording": True,
            "streaming": "streaming",
            "restarts": {},
            "detection": {
                "enabled": True,
                "running": True,
                "restarts": 0,
                "reason": "detecting",
            },
        }


class AngryServices(FakeServices):
    def state(self) -> dict:
        raise RuntimeError("the supervisor is not answering")


class FakePtz:
    def __init__(self) -> None:
        self.applied: list = []

    def apply(self, settings) -> None:
        self.applied.append(settings)

    def status(self) -> dict:
        return {"available": False, "reason": "no camera address set"}

    def move(self, pan, tilt, zoom) -> dict:
        return {"ok": True}

    def stop(self) -> dict:
        return {"ok": True}

    def home(self) -> dict:
        return {"ok": True}


class AngryPtz(FakePtz):
    def apply(self, settings) -> None:
        raise OSError("the camera is not answering")


class FakeRadio:
    def __init__(self) -> None:
        self.applied: list = []

    def apply(self, settings) -> None:
        self.applied.append(settings)

    def status(self) -> dict:
        return {"connected": False, "reason": "the radio is not set up"}


def test_the_live_tab_is_given_the_radio_so_the_link_has_a_panel(
    qtbot, tmp_path: Path
) -> None:
    """The link is the bottleneck of this system, and the console showed one
    number off the radio, in the status bar. The panel is the detail; the bar is
    the glance, and it is unchanged."""
    window, _ = build(qtbot, tmp_path, radio=FakeRadio())
    assert window.live.link_lines(), "the Live tab's side column has no link in it"


class AngryRadio:
    def status(self) -> dict:
        raise OSError("the radio refused the connection")


def write_settings(tmp_path: Path) -> Path:
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.storage.root = tmp_path / "recordings"
    settings.camera.streams = [
        StreamSettings(name="thermal", url="rtsp://camera/thermal", enabled=True)
    ]
    save_settings(settings, path)
    return path


def build(
    qtbot,
    tmp_path: Path,
    services=None,
    radio=None,
    make_pane=None,
    events_path=None,
    ptz=None,
):
    path = write_settings(tmp_path)
    services = services if services is not None else FakeServices()
    window = ConsoleWindow(
        settings_path=path,
        services=services,
        ptz=ptz if ptz is not None else FakePtz(),
        radio=radio if radio is not None else FakeRadio(),
        index_path=tmp_path / "segments.db",
        make_pane=make_pane or (lambda name: FakeVideoPane()),
        events_path=events_path,
    )
    qtbot.addWidget(window)
    return window, services


# --------------------------------------------------------------- the window


def test_the_window_has_the_four_tabs(qtbot, tmp_path: Path) -> None:
    window, _ = build(qtbot, tmp_path)
    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert titles == ["Live", "Playback", "Settings", "Logs"]


def test_the_heartbeat_restarts_what_died(qtbot, tmp_path: Path) -> None:
    window, services = build(qtbot, tmp_path)
    window.heartbeat()
    assert services.ticks == 1


def test_the_status_line_says_what_is_recording_and_streaming(qtbot, tmp_path: Path) -> None:
    window, _ = build(qtbot, tmp_path)
    window.heartbeat()
    text = window.status_text()
    assert "recording" in text.lower()


def test_closing_the_window_does_not_stop_the_recorder(qtbot, tmp_path: Path) -> None:
    """The first requirement this system was given."""
    window, services = build(qtbot, tmp_path)
    window.close()
    assert services.stopped is False


def test_a_tab_that_will_not_build_does_not_take_the_window_with_it(
    qtbot, tmp_path: Path
) -> None:
    """Three working tabs and an apology in the fourth beats a window that
    never opens: Settings and Logs are how a broken installation gets fixed."""

    def explode(name: str):
        raise RuntimeError("libVLC is not installed")

    window, _ = build(qtbot, tmp_path, make_pane=explode)
    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert titles == ["Live", "Playback", "Settings", "Logs"]

    for index in (0, 1):
        failed = window.tabs.widget(index)
        assert isinstance(failed, QLabel)
        assert "libVLC is not installed" in failed.text()

    assert not isinstance(window.tabs.widget(2), QLabel)
    assert not isinstance(window.tabs.widget(3), QLabel)
    # And it still ticks over, rather than falling down the missing tabs.
    window.heartbeat()


def test_the_status_line_survives_a_radio_that_will_not_answer(
    qtbot, tmp_path: Path
) -> None:
    window, _ = build(qtbot, tmp_path, radio=AngryRadio())
    text = window.status_text()
    assert "recording" in text.lower()
    assert "link" in text.lower()


def test_the_status_line_survives_services_that_will_not_answer(
    qtbot, tmp_path: Path
) -> None:
    window, _ = build(qtbot, tmp_path, services=AngryServices())
    text = window.status_text()
    assert text
    assert "could not" in text.lower()
    window.heartbeat()


# ----------------------------------------------------------- saving settings
#
# Settings is the only interface this operator has: no terminal, no second
# machine, and a camera 700 m away. A save that writes the file and reaches
# nothing that is running has changed nothing they can see.


def test_saving_reaches_the_streaming_server_the_camera_and_the_radio(
    qtbot, tmp_path: Path
) -> None:
    ptz, radio = FakePtz(), FakeRadio()
    window, services = build(qtbot, tmp_path, ptz=ptz, radio=radio)
    settings_tab = window.settings_tab
    settings_tab.camera_host = "10.0.0.9"

    assert settings_tab.save() is True
    assert save_applied(window), "the save never reached the running console"

    for applied in (services.applied, ptz.applied, radio.applied):
        assert [s.camera.host for s in applied] == ["10.0.0.9"]


def test_saving_a_new_stream_puts_it_on_the_wall(qtbot, tmp_path: Path) -> None:
    """The panes hold the URLs they were built with. A stream added in Settings
    and not on the wall is a camera the operator cannot see."""
    window, _ = build(qtbot, tmp_path)
    assert window.live.stream_names() == ["thermal"]

    window.settings_tab.add_stream_row("visible", "rtsp://camera/visible")
    assert window.settings_tab.save() is True
    assert save_applied(window), "the save never reached the running console"

    assert window.live.stream_names() == ["thermal", "visible"]


def test_saving_removes_the_pane_of_a_stream_that_is_gone(qtbot, tmp_path: Path) -> None:
    """A pane still showing a stream nobody records is a picture the operator
    has no reason to trust."""
    window, _ = build(qtbot, tmp_path)
    (row,) = window.settings_tab.stream_rows()
    row.name_field.setText("infrared")

    assert window.settings_tab.save() is True
    assert save_applied(window), "the save never reached the running console"

    assert window.live.stream_names() == ["infrared"]


def test_one_part_refusing_the_save_does_not_cost_the_others(qtbot, tmp_path: Path) -> None:
    """The camera is at the far end of a radio link and answers when it feels
    like it. The save itself succeeded; the rest is best effort."""
    radio = FakeRadio()
    window, services = build(qtbot, tmp_path, ptz=AngryPtz(), radio=radio)

    assert window.settings_tab.save() is True
    assert save_applied(window), "the save never reached the running console"
    assert window.settings_tab.message == "Saved."
    assert len(services.applied) == 1
    assert len(radio.applied) == 1


def test_a_settings_file_that_will_not_load_leaves_every_tab_working(
    qtbot, tmp_path: Path
) -> None:
    """This used to leave the Settings tab as an apology label, on the reasoning
    that the other three tabs are how the file gets fixed. They are not: the
    Settings tab is the only thing on this machine that can rewrite that file,
    and an operator with no terminal who loses it has no way back at all."""
    path = tmp_path / "settings.json"
    path.write_text("{ this is not settings", encoding="utf-8")
    window = ConsoleWindow(
        settings_path=path,
        services=FakeServices(),
        ptz=FakePtz(),
        radio=FakeRadio(),
        index_path=tmp_path / "segments.db",
        make_pane=lambda name: FakeVideoPane(),
    )
    qtbot.addWidget(window)

    assert not isinstance(window.settings_tab, QLabel)
    assert not isinstance(window.logs, QLabel)
    window.heartbeat()


# ----------------------------------------------------------- the entry point


def test_parse_args_defaults_to_a_settings_file_beside_the_program() -> None:
    args = parse_args([])
    assert args.settings == str(default_settings_path())
    assert args.no_services is False


def test_parse_args_can_be_told_where_and_to_start_nothing() -> None:
    args = parse_args(["--settings", "C:/vmd/settings.json", "--no-services"])
    assert args.settings == "C:/vmd/settings.json"
    assert args.no_services is True


def test_the_wiring_is_built_without_a_display(tmp_path: Path) -> None:
    path = write_settings(tmp_path)
    wiring = build_wiring(load_settings(path), path, with_services=False)
    assert wiring.settings_path == path
    assert wiring.index_path == tmp_path / "recordings" / "segments.db"
    # Beside the segment index: the two are reclaimed together.
    assert wiring.events_path == tmp_path / "recordings" / "events.db"
    # Nothing to start means nothing was built to start.
    assert wiring.services.streaming is None
    # The detector is built either way; whether it is supervised is the
    # settings' business, not the wiring's.
    assert wiring.services.detector is not None
    assert wiring.services.state()["recording"] is False
    # No stream has detection ticked, so this is off rather than broken - and
    # nothing has created the detector's database.
    assert wiring.services.state()["detection"]["enabled"] is False
    assert not wiring.events_path.exists()


def test_a_pane_that_cannot_be_built_becomes_a_message(qtbot) -> None:
    """A laptop with a broken libVLC still has to reach Settings and Logs."""

    def explode():
        raise RuntimeError("libVLC is not installed")

    pane = pane_factory(explode)("thermal")
    qtbot.addWidget(pane)
    assert "libVLC is not installed" in pane.text()
    assert "thermal" in pane.text()
    # It is a pane in every way the rest of the console needs.
    assert pane.state == "stopped"
    pane.show("rtsp://127.0.0.1:8554/thermal")
    pane.stop()
    assert pane.state == "stopped"


def test_a_pane_that_can_be_built_is_used(qtbot) -> None:
    pane = pane_factory(FakeVideoPane)("thermal")
    assert isinstance(pane, FakeVideoPane)


# ------------------------------------------------------------- detection
#
# Live and Playback read the same events.db, the status line says whether
# detection is running, and a store that cannot be opened costs detection
# rather than the console.


class NoDetectionServices(FakeServices):
    """An older shape of state(), with nothing to say about detection."""

    def state(self) -> dict:
        return {"recording": True, "streaming": "streaming", "restarts": {}}


class FailingDetectionServices(FakeServices):
    def state(self) -> dict:
        self.state_calls += 1
        return {
            "recording": True,
            "streaming": "streaming",
            "restarts": {"detector": 7},
            "detection": {
                "enabled": True,
                "running": False,
                "restarts": 7,
                "reason": "NOT running - restarted 7 times in the last 2 minutes",
            },
        }


def test_the_status_line_says_whether_detection_is_running(qtbot, tmp_path: Path) -> None:
    window, _ = build(qtbot, tmp_path)
    text = window.status_text().lower()
    assert "detection" in text
    assert "detecting" in text


def test_the_status_line_names_a_detector_that_will_not_stay_up(
    qtbot, tmp_path: Path
) -> None:
    """Restarting forever behind a status line that reads "detecting" is the
    failure the operator must not be protected from."""
    window, _ = build(qtbot, tmp_path, services=FailingDetectionServices())
    text = window.status_text()
    assert "NOT running" in text
    assert "7" in text


def test_the_status_line_survives_services_with_nothing_to_say_about_detection(
    qtbot, tmp_path: Path
) -> None:
    window, _ = build(qtbot, tmp_path, services=NoDetectionServices())
    text = window.status_text().lower()
    assert "recording" in text
    window.heartbeat()


def test_live_and_playback_read_the_same_movement(qtbot, tmp_path: Path) -> None:
    """One file, two tabs, one answer.

    Two readers now, not one store: the Live tab reads on a worker because
    events.db lives in the folder that goes away, and sqlite will not let one
    connection cross threads. WAL is what makes two of them safe. What must not
    change is that both tabs show the same movement.

    Bounded, and it beats the console rather than sleeping: a reading that never
    arrives fails this test instead of hanging it.
    """
    from vmd.desktop.timeline import day_bounds
    from vmd.detect.events import EventStore

    start, end = day_bounds(2026, 8, 11)
    events_path = tmp_path / "recordings" / "events.db"
    store = EventStore(events_path)
    store.add("thermal", start + 3600, start + 3604, (1, 2, 3, 4), 40.0)
    store.close()

    window, _ = build(qtbot, tmp_path, events_path=events_path)
    assert beating(window, lambda: len(window.live.recent_rows()) == 1), (
        "the movement never reached the Live tab"
    )
    window.playback.show_day(2026, 8, 11, stream="thermal")
    assert len(window.playback.event_marks) == 1
    window.close()


def test_a_console_told_of_no_events_database_says_nothing_about_it(
    qtbot, tmp_path: Path, caplog
) -> None:
    """There is nothing to open and nothing to complain about."""
    with caplog.at_level("ERROR", logger="vmd.desktop.window"):
        window, _ = build(qtbot, tmp_path)
    assert window.events is None
    assert caplog.records == []
    window.close()


def test_an_event_store_that_will_not_open_costs_detection_not_the_console(
    qtbot, tmp_path: Path
) -> None:
    """The four tabs are how a broken installation gets diagnosed. Losing them
    to the detector's database would take away the tools for fixing it."""
    events_path = tmp_path / "recordings" / "events.db"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_bytes(b"this is not a database")

    window, _ = build(qtbot, tmp_path, events_path=events_path)
    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert titles == ["Live", "Playback", "Settings", "Logs"]
    for index in range(4):
        assert not isinstance(window.tabs.widget(index), QLabel)

    # And it goes on ticking, with no movement to show.
    for _ in range(3):
        window.heartbeat()
    assert window.live.recent_rows() == []
    window.close()


# --------------------------------------------- the recorder in the status line
#
# Detection has said "NOT running - restarted N times in the last 2 minutes"
# since it was written. The recorder, which matters more, said only "recording"
# or "NOT recording".


class FlappingServices(FakeServices):
    def state(self) -> dict:
        state = super().state()
        state["recording"] = False
        state["recording_state"] = {
            "running": False,
            "restarts": 20,
            "reason": "NOT recording - restarted 20 times in the last 2 minutes",
        }
        return state


def test_the_status_line_says_the_recorder_died_and_was_restarted(
    qtbot, tmp_path: Path
) -> None:
    window, _ = build(qtbot, tmp_path, services=FlappingServices())
    text = window.status_text()
    assert "restarted 20 times" in text
    assert "NOT recording" in text


def test_a_status_line_from_services_that_only_say_yes_or_no_still_reads(
    qtbot, tmp_path: Path
) -> None:
    """The old shape, in case anything still hands one over."""
    window, _ = build(qtbot, tmp_path)
    text = window.status_text()
    assert "recording" in text.lower()


# ---------------------------------------- a save that could not be applied
#
# The operator presses Save, reads "Saved." and walks away. If a child would
# not restart, what is running is not what was saved, and the one place that
# can still be said is the line under the button they just pressed.


class RefusingServices(FakeServices):
    def __init__(self) -> None:
        super().__init__()
        self.applied: list = []

    def apply(self, settings) -> list[str]:
        self.applied.append(settings)
        return ["the recorder did not restart, so recording is still using the old settings"]


class QuietServices(FakeServices):
    def apply(self, settings) -> list[str]:
        return []


def test_a_save_that_could_not_be_applied_says_so_where_it_was_pressed(
    qtbot, tmp_path: Path
) -> None:
    window, services = build(qtbot, tmp_path, services=RefusingServices())
    settings = load_settings(window._settings_path)
    window.settings_tab._set_message("Saved.")

    window.settings_saved(settings)
    assert save_applied(window), "the save never reached the running console"

    assert services.applied == [settings]
    message = window.settings_tab.message
    assert message != "Saved."
    assert "did not restart" in message
    assert "Saved" in message, "the file really was written; say both things"


def test_a_save_that_worked_still_reads_as_saved(qtbot, tmp_path: Path) -> None:
    window, _ = build(qtbot, tmp_path, services=QuietServices())
    window.settings_tab._set_message("Saved.")
    window.settings_saved(load_settings(window._settings_path))
    assert save_applied(window), "the save never reached the running console"
    assert window.settings_tab.message == "Saved."


# ------------------------------------------- a settings file that cannot be read
#
# The only tool for fixing settings.json is the Settings tab, which is inside
# the console. A console that refuses to open because settings.json is broken is
# an unrecoverable state for an operator with no terminal and no second machine.


def test_a_settings_file_that_cannot_be_read_does_not_stop_the_console_opening(
    qtbot, tmp_path: Path
) -> None:
    from vmd.desktop.app import load_or_default

    path = tmp_path / "settings.json"
    path.write_text("{ this is not json", encoding="utf-8")

    settings = load_or_default(path)
    assert isinstance(settings, Settings), "the console must still be able to open"


def test_the_settings_tab_shows_a_broken_file_rather_than_becoming_one(
    qtbot, tmp_path: Path
) -> None:
    """It is the one tool that can fix the file, so it must not be the tab that
    is lost to it."""
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json", encoding="utf-8")

    window = ConsoleWindow(
        settings_path=path,
        services=FakeServices(),
        ptz=FakePtz(),
        radio=FakeRadio(),
        index_path=tmp_path / "segments.db",
        make_pane=lambda name: FakeVideoPane(),
    )
    qtbot.addWidget(window)

    assert not isinstance(window.settings_tab, QLabel), (
        "the tab that fixes the file must not be the casualty of it"
    )
    message = window.settings_tab.message
    assert "could not be read" in message.lower()
    assert "save" in message.lower(), "say what to do about it"


def test_a_broken_settings_file_can_be_replaced_from_the_tab(
    qtbot, tmp_path: Path
) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json", encoding="utf-8")

    tab = SettingsTab(settings_path=path)
    qtbot.addWidget(tab)
    tab.load()
    tab.camera_host = "10.0.0.2"
    tab.set_streams([("thermal", "rtsp://10.0.0.2/t", True, "auto")])

    assert tab.save() is True
    assert load_settings(path).camera.host == "10.0.0.2"


# ------------------------------------------- the heartbeat, with everything wedged
#
# The heartbeat is what restarts a dead child, redraws the panes and - the whole
# reason this matters - lets the alarm strip appear. Four separate paths used to
# do blocking network or process calls on this thread, and every one of them
# blocks longest when the network is down, which is exactly when the operator
# needs the console. While any of them blocked, a perimeter crossing was
# silently missed.
#
# Every wedge below is bounded by a ceiling of its own, so a regression fails
# this test in a few seconds rather than hanging the suite.

import threading  # noqa: E402
import time  # noqa: E402

from vmd.background import BackgroundValue  # noqa: E402
from vmd.desktop.services import ConsoleServices, RecorderProcess  # noqa: E402
from vmd.radio.service import RadioService  # noqa: E402
from vmd.streaming.go2rtc import Go2rtcService  # noqa: E402

WEDGE_CEILING = 5.0


class Wedge:
    """Something that does not come back until the test lets it."""

    def __init__(self, answer=None) -> None:
        self.answer = answer
        self.calls = 0
        self.entered = threading.Event()
        self.released = threading.Event()

    def hold(self):
        self.calls += 1
        self.entered.set()
        self.released.wait(WEDGE_CEILING)
        return self.answer


class WedgedRadio:
    def __init__(self, wedge: Wedge) -> None:
        self._wedge = wedge

    def status(self):
        self._wedge.hold()
        raise OSError("cannot reach the radio")


class WedgedPtz:
    def __init__(self, wedge: Wedge) -> None:
        self._wedge = wedge
        self.applied: list = []

    def apply(self, settings) -> None:
        self.applied.append(settings)

    def status(self) -> dict:
        return {"available": False, "reason": "no camera address set"}

    def move(self, pan, tilt, zoom) -> dict:
        self._wedge.hold()
        return {"ok": True}

    def stop(self) -> dict:
        self._wedge.hold()
        return {"ok": True}

    def home(self) -> dict:
        self._wedge.hold()
        return {"ok": True}


class DeadOnArrival:
    """go2rtc as a corrupt binary leaves it: spawned, and gone before anyone looks."""

    pid = 5150

    def poll(self):
        return 0

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout=None):
        return 0


class Ticking:
    """The supervisor's clock, wound two seconds per heartbeat by hand.

    Without it the supervisor's own restart delay means it only tries to start a
    dead go2rtc once every two real seconds, so a burst of heartbeats measured
    back to back would never reach the launch at all - which is the very path
    that used to sleep 0.8 s on this thread.
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def heartbeat(self) -> None:
        self.now += 2.0


def wedged_console(tmp_path: Path, wedges: dict, clock=None):
    """A console whose every slow dependency has stopped answering."""
    path = write_settings(tmp_path)
    settings = load_settings(path)

    streaming = Go2rtcService(
        settings,
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        endpoint_path=tmp_path / "streaming.json",
        spawn=lambda command: DeadOnArrival(),
    )

    (tmp_path / "recorder.pid").write_text("4242", encoding="utf-8")
    recorder = RecorderProcess(
        path,
        pid_path=tmp_path / "recorder.pid",
        spawn=lambda command: DeadOnArrival(),
        kill_tree=lambda pid: True,
        alive=_wedged_alive(wedges["liveness"]),
    )

    services = ConsoleServices(
        settings=settings,
        settings_path=path,
        streaming=streaming,
        recorder=recorder,
        clock=clock or time.monotonic,
    )
    services.start()

    radio = RadioService(Settings())
    radio.radio = WedgedRadio(wedges["radio"])
    radio._reading = BackgroundValue(
        read=radio.radio.status, stale_after=0.0, name="a wedged radio"
    )
    return path, services, radio


def _wedged_alive(wedge: Wedge):
    """A `tasklist` that answers once - so the child is adopted at all - and
    then stops coming back."""

    def alive(pid: int) -> bool:
        if wedge.calls == 0:
            wedge.calls += 1
            return True
        wedge.hold()
        return True

    return alive


def test_the_heartbeat_returns_at_once_with_every_dependency_wedged(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("vmd.desktop.services.LIVENESS_SECONDS", 0.0)
    wedges = {"radio": Wedge(), "liveness": Wedge(), "ptz": Wedge()}
    clock = Ticking()
    path, services, radio = wedged_console(tmp_path, wedges, clock=clock)
    ptz = WedgedPtz(wedges["ptz"])

    window = ConsoleWindow(
        settings_path=path,
        services=services,
        ptz=ptz,
        radio=radio,
        index_path=tmp_path / "segments.db",
        make_pane=lambda name: FakeVideoPane(),
    )
    qtbot.addWidget(window)
    try:
        # A camera command on the wire as well: the operator is steering while
        # all this is going on, which is the state the freeze was measured in.
        window.live.key_down("right", fine=False)
        assert wedges["ptz"].entered.wait(WEDGE_CEILING)

        clock.heartbeat()
        window.heartbeat()  # the first one may still be starting things up
        slowest = 0.0
        for _ in range(5):
            clock.heartbeat()
            started = time.monotonic()
            window.heartbeat()
            slowest = max(slowest, time.monotonic() - started)
        assert len(services.supervisor.restarts) and services.supervisor.restarts[
            "streaming"
        ] >= 4, "the dead go2rtc was never relaunched, so its launch was not measured"
    finally:
        for wedge in wedges.values():
            wedge.released.set()
        radio.close()
        window.live.shutdown()

    assert slowest < 0.3, f"a heartbeat took {slowest:.2f} s with everything wedged"


def test_the_status_line_admits_it_has_not_heard_from_the_radio(
    qtbot, tmp_path: Path
) -> None:
    """A blank, or a dash, is what this console says when the link is fine."""
    wedges = {"radio": Wedge(), "liveness": Wedge(), "ptz": Wedge()}
    path, services, radio = wedged_console(tmp_path, wedges)
    window = ConsoleWindow(
        settings_path=path,
        services=services,
        ptz=WedgedPtz(wedges["ptz"]),
        radio=radio,
        index_path=tmp_path / "segments.db",
        make_pane=lambda name: FakeVideoPane(),
    )
    qtbot.addWidget(window)
    try:
        assert "link checking" in window.status_text()
    finally:
        for wedge in wedges.values():
            wedge.released.set()
        radio.close()


def test_a_link_reading_that_has_gone_stale_says_how_old_it_is() -> None:
    """Never a stale value presented as current."""
    fresh = ConsoleWindow._link_words({"signal_dbm": -63, "age_seconds": 2.0})
    stale = ConsoleWindow._link_words({"signal_dbm": -63, "age_seconds": 41.0})
    assert fresh == "link -63 dBm"
    assert "41" in stale and "ago" in stale


# ------------------------------------------------- the band, and the red dot
#
# The state of the whole system used to be one grey sentence in an eleven-pixel
# footer: the least prominent thing on the screen, and the most important. It is
# now a band of chips across the top of every tab, and the recording one is a
# dot that pulses. These tests are about what that band claims, because a
# console that says "recording" while nothing is being written is the exact lie
# that made the owner stop believing this window.


class NotRecordingServices(FakeServices):
    """Footage is not reaching the disk, whatever any process is doing."""

    def state(self) -> dict:
        state = super().state()
        state["recording"] = False
        state["recording_state"] = {"running": False, "reason": "NOT recording"}
        return state


class LinkedRadio(FakeRadio):
    """A link that is up and was read a moment ago: the state the console spends
    almost all of its life in, and the one the band has to be quiet about."""

    def status(self) -> dict:
        return {"connected": True, "signal_dbm": -63.0, "age_seconds": 2.0}


class SickServices(FakeServices):
    def state(self) -> dict:
        return {
            "recording": True,
            "streaming": "go2rtc is not installed - run install.bat",
            "restarts": {},
            "detection": {"enabled": True, "running": False, "reason": "not running"},
        }


def test_a_healthy_band_says_the_name_of_each_part_and_no_more(
    qtbot, tmp_path: Path
) -> None:
    """`streaming: streaming` is reassurance, not information.

    A console with nothing wrong has four things to say and says them in four
    words, because the glyph beside each already says it is fine. What that buys
    is height, on a screen whose whole purpose is showing video."""
    window, _ = build(qtbot, tmp_path, radio=LinkedRadio())
    window.heartbeat()
    assert window.band.chips() == ["recording", "streaming", "detection", "link"]


def test_a_fault_says_the_whole_sentence_the_footer_used_to_say(
    qtbot, tmp_path: Path
) -> None:
    """The short form is only ever the healthy form. The moment something is
    wrong the chip carries the whole sentence, unchanged - those words were
    chosen for an operator who is not technical, and shortening the one sentence
    that has to be read would be shortening the wrong one."""
    window, _ = build(qtbot, tmp_path, services=SickServices())
    window.heartbeat()
    said = window.band.chips()
    assert "streaming: go2rtc is not installed - run install.bat" in said
    assert "detection: not running" in said
    # And the sentence in the band is a sentence the footer would have said.
    for chip in said:
        assert chip in window.status_text() or chip in ("recording", "link")


def test_the_sentences_themselves_are_unchanged(qtbot, tmp_path: Path) -> None:
    """The band draws less; nothing about what the console KNOWS got shorter.
    `status_text` is what the logs and every other test read."""
    window, _ = build(qtbot, tmp_path)
    window.heartbeat()
    assert window.status_text().startswith("recording · streaming: streaming · ")


def test_the_band_is_one_line_of_its_own_type_and_not_a_block(
    qtbot, tmp_path: Path
) -> None:
    """It was 53 px of a 720 px screen. The band earns its place by being read
    from two metres, which is the 16 px - not by the padding around it."""
    from vmd.desktop.style import SIZE_BAND

    window, _ = build(qtbot, tmp_path / "well", radio=LinkedRadio())
    window.heartbeat()
    assert window.band.sizeHint().height() <= 2 * SIZE_BAND

    # And a console with everything wrong is still a line. A chip asks for the
    # width of its sentence rather than for the squarish block a wrapped label
    # would choose, so the words go sideways into the room beside them before
    # they ever go downwards into the pictures.
    sick, _ = build(qtbot, tmp_path / "ill", services=SickServices())
    sick.heartbeat()
    assert sick.band.sizeHint().height() <= 2 * SIZE_BAND


def test_the_band_knows_which_of_its_chips_is_the_bad_one(qtbot, tmp_path: Path) -> None:
    """A wall of four identical grey sentences is what it replaced. Each part
    now carries the state it is reporting, so the one that is wrong can be the
    one that is red."""
    healthy, _ = build(qtbot, tmp_path / "well", services=FakeServices())
    states = dict((words, state) for _glance, words, state in healthy.status_parts())
    assert states["streaming: streaming"] == "ok"

    sick, _ = build(qtbot, tmp_path / "ill", services=SickServices())
    states = dict((words, state) for _glance, words, state in sick.status_parts())
    assert states["streaming: go2rtc is not installed - run install.bat"] == "alarm"


def test_detection_that_nobody_switched_on_is_not_drawn_as_a_fault(
    qtbot, tmp_path: Path
) -> None:
    """Off is not a failure. A console that painted "nobody ticked the box" in
    alarm red would teach its operator to ignore the chip that one day says
    something true."""
    from vmd.desktop.window import _detection_state

    assert _detection_state({"enabled": False, "running": False}) == "muted"
    assert _detection_state({"enabled": True, "running": True}) == "ok"
    assert _detection_state({"enabled": True, "running": False}) == "alarm"


def test_the_link_chip_and_the_link_panel_read_the_same_bands(
    qtbot, tmp_path: Path
) -> None:
    """One set of thresholds, in the module where they are explained. A chip
    calling -84 dBm healthy while the panel under it calls the same reading
    marginal would be the console arguing with itself."""
    from vmd.desktop.window import _link_state

    assert _link_state({"signal_dbm": -63.0, "age_seconds": 1.0}) == "ok"
    assert _link_state({"signal_dbm": -72.0, "age_seconds": 1.0}) == "warn"
    assert _link_state({"signal_dbm": -84.0, "age_seconds": 1.0}) == "alarm"
    # A reading nobody has taken for a while may never be drawn in the colour
    # that means "the link is fine right now".
    assert _link_state({"signal_dbm": -63.0, "age_seconds": 400.0}) == "muted"


def test_the_dot_pulses_only_while_footage_is_reaching_the_disk(
    qtbot, tmp_path: Path
) -> None:
    """The dot follows `recording`, which is whether anything was written - not
    whether a process was alive at the instant the console looked."""
    window, _ = build(qtbot, tmp_path)
    window.heartbeat()
    assert window.recording_now() is True
    assert window.band.recording_glyph() == "●"

    bright = window.band.recording_colour()
    window._beat()
    assert window.band.recording_colour() != bright, "the dot has to actually move"
    window._beat()
    assert window.band.recording_colour() == bright


def test_not_recording_is_a_still_bar_and_not_merely_a_missing_dot(
    qtbot, tmp_path: Path
) -> None:
    """Somebody glancing over has to be able to tell "not recording" from "I
    looked away on the dim beat"."""
    window, _ = build(qtbot, tmp_path, services=NotRecordingServices())
    window.heartbeat()
    assert window.recording_now() is False
    assert window.band.recording_glyph() == "■"
    still = window.band.recording_colour()
    window._show_recording()
    assert window.band.recording_colour() == still
    assert window._blink.isActive() is False, "nothing may pulse while nothing is written"


def test_services_that_cannot_be_asked_leave_the_dot_saying_not_recording(
    qtbot, tmp_path: Path
) -> None:
    """The safe way round. A dot that keeps pulsing because nobody could be
    asked is exactly the lie this indicator exists to stop telling."""
    window, _ = build(qtbot, tmp_path, services=AngryServices())
    window.heartbeat()
    assert window.recording_now() is False
    assert window.band.recording_glyph() == "■"


def test_nothing_pulses_behind_a_window_nobody_is_looking_at(
    qtbot, tmp_path: Path
) -> None:
    """One timer for the whole console, and it stops when the window goes away.
    This runs for months on a laptop that is never rebooted."""
    window, _ = build(qtbot, tmp_path)
    window.show()
    window.heartbeat()
    assert window._blink.isActive() is True
    window.hide()
    assert window._blink.isActive() is False
    window.close()
    assert window._blink.isActive() is False


def test_the_view_the_operator_chose_survives_a_restart(qtbot, tmp_path: Path) -> None:
    """An operator who wants thermal alone wants it tomorrow too. A choice that
    does not survive the night is not a choice, it is a chore."""
    window, _ = build(qtbot, tmp_path)
    window.live.show_view("thermal")
    assert load_settings(window._settings_path).wall_view == "thermal"

    # Opened again against the file that was just written, rather than through
    # `build`, which writes a fresh settings.json over it.
    reopened = ConsoleWindow(
        settings_path=window._settings_path,
        services=FakeServices(),
        ptz=FakePtz(),
        radio=FakeRadio(),
        index_path=tmp_path / "segments.db",
        make_pane=lambda name: FakeVideoPane(),
    )
    qtbot.addWidget(reopened)
    assert reopened.live.chosen_view() == "thermal"
    assert reopened.live.shown_streams() == ["thermal"]


def test_a_settings_file_that_cannot_be_written_still_changes_the_wall(
    qtbot, tmp_path: Path
) -> None:
    """A full disk is one of the things this console exists to report, and it
    may not cost the operator the view they just asked for."""
    window, _ = build(qtbot, tmp_path)
    window._settings_path = tmp_path / "no-such-folder" / "settings.json"
    window.live.show_view("thermal")
    assert window.live.chosen_view() == "thermal"


# ------------------------------------------- a radio that refused the login
#
# The failure at the far end of a 700 m link was the one the band would not
# report. `_link_state` fell through to `muted` whenever `signal_dbm` was not a
# number - which is precisely the case where the radio has been asked and has
# REFUSED - so a hard authentication failure was drawn exactly like one still
# being checked: no box, no colour, `link -`. Meanwhile the panel one screen
# below printed fourteen wrapped grey lines ending in a program to run, at a man
# with no terminal. Both halves are the same defect: the glance says fine and
# the reading says impossible.

REFUSED_403 = (
    "the radio answered HTTP 403 (Forbidden) to the login at "
    "http://192.168.1.20/login.cgi. It is reachable and it refused the request, "
    "which need not mean the password is wrong: airOS also answers 403 to a "
    "login sent without the session cookie from its own login page, to one that "
    "does not look like it came from that page, and after too many tries. All "
    "login flows were tried. Run spike/probe_radio.py against this radio and "
    "send what it prints."
)

REFUSED_SAID_SO = (
    'the radio refused the login and said so: "Invalid credentials." '
    "(HTTP 403 from http://192.168.1.20/login.cgi). Those are the radio's own "
    "words - check the username and the password in Settings."
)


def refusal(reason: str = REFUSED_403) -> dict:
    """What `RadioService.status()` leaves behind after a refused login."""
    return {"connected": False, "reason": reason, "age_seconds": 2.0}


def test_a_radio_that_refused_the_login_is_drawn_as_a_fault(qtbot) -> None:
    """Asked and refused is not the same as still being asked."""
    from vmd.desktop.window import _link_state

    assert _link_state(refusal()) == "alarm"
    assert _link_state(refusal(REFUSED_SAID_SO)) == "alarm"
    assert (
        _link_state({"connected": False, "reason": "cannot reach 192.168.1.20", "age_seconds": 9.0})
        == "alarm"
    )


def test_a_radio_nobody_has_set_up_or_asked_yet_is_still_quiet() -> None:
    """The other two states with no signal figure in them. Neither is a fault,
    and drawing them as one would teach the operator to ignore the chip."""
    from vmd.desktop.window import _link_state

    assert _link_state({"connected": False, "reason": "the radio is not set up"}) == "muted"
    assert _link_state({"connected": False, "checking": True, "reason": "checking"}) == "muted"


def test_a_radio_that_answered_without_a_signal_figure_warns() -> None:
    """The panel calls this a warning. A chip calling the same reading nothing
    would be the console arguing with itself one line down the screen."""
    from vmd.desktop.window import _link_state

    assert _link_state({"connected": True, "signal_dbm": None, "age_seconds": 1.0}) == "warn"


def test_the_band_says_what_a_refused_radio_did_rather_than_a_dash() -> None:
    """`link -` is what this console says when the radio has nothing to report."""
    words = ConsoleWindow._link_words(refusal())
    assert words != "link -"
    assert "link" in words
    # Short enough to sit in a one-line band beside three other chips.
    assert len(words) <= 60, words
    for jargon in ("HTTP", "403", "cookie", ".py", "login.cgi", "airOS"):
        assert jargon not in words, f"{jargon!r} is in the band: {words}"


def test_a_radio_with_nothing_to_report_still_says_nothing() -> None:
    assert ConsoleWindow._link_words({"connected": False, "reason": "the radio is not set up"}) == (
        "link -"
    )
    assert ConsoleWindow._link_words({"checking": True}) == "link checking"


# ------------------------------------- the heartbeat and the folder that goes
#
# `vmd/desktop/disk.py` opens by stating the rule: none of it runs on the GUI
# thread and none of it runs on the two-second heartbeat, because a disconnected
# drive can leave a filesystem call blocked for many seconds. Two things broke
# it against that same folder - the detector's report and the movement list -
# so the dead-drive case the rule exists for was also the case in which the
# console froze every two seconds.


# How long a wedged read pretends the drive is still thinking about it. Long
# enough that a console reading on the GUI thread is caught, short enough that
# being caught costs the suite seconds rather than minutes.
WEDGE_SECONDS = 3.0


class WedgedStore:
    """A movement database on a drive that has gone away.

    `recent` never returns for as long as anyone would wait for it. Bounded
    inside and released at the end of the test, so that a console which really
    did read it on the GUI thread fails this test rather than hanging the suite.
    """

    def __init__(self) -> None:
        import threading

        self.released = threading.Event()
        self.asked = threading.Event()

    def recent(self, limit: int):
        self.asked.set()
        self.released.wait(WEDGE_SECONDS)
        return []


def test_a_recordings_folder_that_has_gone_does_not_freeze_the_heartbeat(
    qtbot, tmp_path: Path
) -> None:
    import threading
    import time as _time

    from vmd.desktop.live import LiveTab
    from vmd.desktop.video import FakeVideoPane

    store = WedgedStore()
    tab = LiveTab(
        ptz=FakePtz(),
        make_pane=lambda name: FakeVideoPane(),
        local_url=lambda name: None,
        events=store,
    )
    qtbot.addWidget(tab)
    try:
        tab.apply(load_settings(_settings_with_a_stream(tmp_path)))
        slowest = 0.0
        for _ in range(5):
            started = _time.monotonic()
            tab.refresh()
            slowest = max(slowest, _time.monotonic() - started)
        assert store.asked.wait(10.0), "the movement list was never read at all"
        assert slowest < 1.0, f"a refresh took {slowest:.1f} s against a dead folder"
        # And the one blocked reading did not become one worker per beat.
        stuck = [t for t in threading.enumerate() if "movement" in t.name]
        assert len(stuck) <= 1, [t.name for t in stuck]
    finally:
        store.released.set()
        tab.shutdown()


def test_the_detector_s_report_is_not_read_on_the_heartbeat(
    tmp_path: Path, monkeypatch
) -> None:
    """The same folder, the same rule. `read_detection_status` was reached from
    `heartbeat` -> `status_parts` -> `state` -> `detection_state`, and it opens
    a file in the recordings root."""
    import threading
    import time as _time

    from vmd.desktop import services as services_module
    from vmd.desktop.services import ConsoleServices, DetectorProcess, RecorderProcess

    released = threading.Event()
    asked = threading.Event()

    class Living:
        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            return None

        def kill(self):
            return None

    settings_path = _settings_with_a_stream(tmp_path, detect=True)
    services = ConsoleServices(
        settings=load_settings(settings_path),
        settings_path=settings_path,
        streaming=None,
        recorder=RecorderProcess(settings_path, spawn=lambda c: Living()),
        detector=DetectorProcess(settings_path, spawn=lambda c: Living()),
    )

    def wedged(path):
        asked.set()
        released.wait(WEDGE_SECONDS)
        return None

    # The file itself, not the console's plumbing: the question is whether
    # opening it happens on the thread that asked for the state.
    monkeypatch.setattr(services_module, "read_detection_status", wedged)
    try:
        slowest = 0.0
        for _ in range(5):
            started = _time.monotonic()
            services.state()
            slowest = max(slowest, _time.monotonic() - started)
        assert asked.wait(10.0), "the detector's report was never read at all"
        assert slowest < 1.0, f"state() took {slowest:.1f} s against a dead folder"
    finally:
        released.set()


def test_the_services_are_asked_what_they_are_doing_once_a_beat(
    qtbot, tmp_path: Path
) -> None:
    """`status_parts` and `recording_now` both need it, and both used to ask -
    so every beat composed the recorder's sentence, the disk reading and the
    detector's report twice over."""
    window, services = build(qtbot, tmp_path)
    before = services.state_calls
    window.heartbeat()
    assert services.state_calls - before == 1, (
        f"one heartbeat asked for the state {services.state_calls - before} times"
    )
    window.close()


def _settings_with_a_stream(tmp_path: Path, detect: bool = False) -> Path:
    path = tmp_path / "settings.json"
    save_settings(
        Settings(
            camera=CameraSettings(
                host="10.0.0.2",
                streams=[
                    StreamSettings(
                        name="thermal",
                        url="rtsp://10.0.0.2/t",
                        enabled=True,
                        detect=detect,
                    )
                ],
            ),
            storage=StorageSettings(root=tmp_path / "recordings"),
        ),
        path,
    )
    return path


# --------------------------------------------------- Save, and the frozen window
#
# `ConsoleServices.apply` runs `taskkill` and up to four process waits per child,
# and it ran inside the Save slot on the GUI thread. Tens of seconds in which
# nothing repaints, the supervisor does not tick and the alarm strip cannot
# appear - at the one moment the operator is most likely to be standing in front
# of the machine watching it. It is the same fault the PTZ and the radio
# services were both rewritten to remove.


class SlowServices(FakeServices):
    """Children that take as long to restart as `taskkill` and four waits do."""

    def __init__(self, seconds: float = 3.0) -> None:
        super().__init__()
        import threading

        self.started = threading.Event()
        self.released = threading.Event()
        self.seconds = seconds
        self.on_progress = lambda step: None
        self.thread = ""

    def apply(self, settings) -> list[str]:
        import threading

        self.thread = threading.current_thread().name
        self.started.set()
        self.on_progress("restarting the streaming server")
        self.released.wait(self.seconds)
        self.on_progress("restarting the recorder")
        self.applied.append(settings)
        return []


def test_save_does_not_freeze_the_window_while_the_children_restart(
    qtbot, tmp_path: Path
) -> None:
    import time as _time

    services = SlowServices()
    window, _ = build(qtbot, tmp_path, services=services)
    try:
        started = _time.monotonic()
        assert window.settings_tab.save() is True
        pressed = _time.monotonic() - started
        assert pressed < 1.0, f"Save held the window for {pressed:.1f} s"

        assert services.started.wait(10.0), "the children were never restarted"
        # And the heartbeat still runs while they are, which is what the alarm
        # strip needs.
        beats = window._services.ticks
        window.heartbeat()
        assert window._services.ticks == beats + 1
    finally:
        services.released.set()
        assert save_applied(window)
        window.close()


def test_the_operator_is_told_what_is_being_done_while_it_is_being_done(
    qtbot, tmp_path: Path
) -> None:
    """A frozen window says nothing and a finished one says "Saved." The seconds
    in between are the ones he is actually watching."""
    from PySide6.QtWidgets import QApplication

    services = SlowServices()
    window, _ = build(qtbot, tmp_path, services=services)
    try:
        assert window.settings_tab.save() is True
        assert services.started.wait(10.0)

        import time as _time

        seen: set[str] = set()
        deadline = _time.monotonic() + 10.0
        while _time.monotonic() < deadline:
            QApplication.processEvents()
            seen.add(window.settings_tab.message)
            if any("streaming server" in line.lower() for line in seen):
                break
            _time.sleep(0.01)

        said = " | ".join(sorted(seen))
        assert "Saved." in said, said
        assert "streaming server" in said.lower(), said
        # And the button is held while it is being done: a second press would
        # queue a second restart behind the first.
        assert window.settings_tab.save_button.isEnabled() is False
    finally:
        services.released.set()
        assert save_applied(window)
        assert window.settings_tab.message == "Saved."
        assert window.settings_tab.save_button.isEnabled() is True
        window.close()


def test_a_save_that_threw_still_answers_the_operator(qtbot, tmp_path: Path) -> None:
    """A save that silently returned before the work was done would be worse
    than a slow one."""

    class ThrowingServices(FakeServices):
        def apply(self, settings):
            raise RuntimeError("taskkill is not on this machine")

    window, _ = build(qtbot, tmp_path, services=ThrowingServices())
    window.settings_tab._set_message("Saved.")
    window.settings_saved(load_settings(window._settings_path))
    assert save_applied(window), "a save that threw never came back at all"
    assert "Saved" in window.settings_tab.message
    assert "would not take" in window.settings_tab.message
    window.close()


# ------------------------------------------- what opening the console costs
#
# Three comments in `services.py` and `window.py` used to say that importing the
# detector would drag cv2, numpy and eventually the classifier's weights into
# the window's process - and each had a copied constant or a duplicated rule
# behind it, paid for on that reasoning. The detector package resolves its
# re-exports on first use now, so the copies are gone and the imports are
# direct. This is what stops that being quietly undone.
#
# Checked in a process of its own, with the vision stack made genuinely
# unimportable, because a check inside this interpreter proves nothing: pytest
# has already imported cv2 for the detection tests.

BLOCK_THE_VISION_STACK = """
import sys


class Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("cv2", "numpy", "ultralytics", "torch"):
            raise ImportError(name + " is not installed on this machine")
        return None


sys.meta_path.insert(0, Blocked())
"""

# Long enough that a cold interpreter on a busy laptop is not a failure, short
# enough that a hang fails this test rather than stopping the suite.
IMPORT_TIMEOUT = 120


def test_the_console_opens_on_a_laptop_with_no_vision_stack() -> None:
    """The window, its services and its entry point, with cv2 unimportable.

    This is the laptop the console has to open on: opencv missing, or present
    and refusing to load because a Visual C++ runtime is not there. A console
    that will not open is a perimeter nobody is watching.
    """
    import subprocess
    import sys as _sys

    body = """
import vmd.desktop.app
import vmd.desktop.services
import vmd.desktop.window
from vmd.detect.events import EventStore

import sys
for unwanted in ("cv2", "numpy", "ultralytics", "torch"):
    assert unwanted not in sys.modules, unwanted
print("opened")
"""
    done = subprocess.run(
        [_sys.executable, "-c", BLOCK_THE_VISION_STACK + body],
        capture_output=True,
        text=True,
        timeout=IMPORT_TIMEOUT,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "opened" in done.stdout


# ------------------------------------------------------ take him to the footage
#
# The finding this answers: there was not one `setCurrentIndex` on the tab bar
# anywhere in the console. An alarm fired and the operator - one person, no
# terminal, no second machine - had to change tab, pick the day, pick the
# stream and hit a three-pixel mark on a timeline, all under the pressure the
# alarm had just created. The Live tab says what was asked for; the window is
# the only thing that owns both tabs, so the going is done here.


class SteeringPtz(FakePtz):
    """A camera that remembers what it was told, for the stop that is owed."""

    def __init__(self) -> None:
        super().__init__()
        self.commands: list[tuple] = []

    def move(self, pan, tilt, zoom) -> dict:
        self.commands.append(("move", pan, tilt, zoom))
        return {"ok": True}

    def stop(self) -> dict:
        self.commands.append(("stop",))
        return {"ok": True}


def a_day_with(tmp_path: Path, *, recorded: bool):
    """A segment index and an empty events database, for 2026-08-11."""
    from vmd.desktop.timeline import day_bounds
    from vmd.detect.events import EventStore
    from vmd.storage.index import SegmentIndex

    start, _end = day_bounds(2026, 8, 11)
    index = SegmentIndex(tmp_path / "segments.db")
    if recorded:
        index.add("thermal", str(tmp_path / "a.mp4"), start + 3600, start + 5400, 1000)
    index.close()

    events_path = tmp_path / "recordings" / "events.db"
    EventStore(events_path).close()
    return start, events_path


def something_moves(events_path: Path, when: float) -> None:
    from vmd.detect.events import EventStore

    store = EventStore(events_path)
    store.add("thermal", when, when + 3.0, (1, 2, 3, 4), 40.0)
    store.close()


def alarmed(window, events_path: Path, when: float) -> None:
    """Let the console learn what was already there, then move in front of it."""
    assert beating(window, lambda: window.live._seen_ids is not None), (
        "the movement list was never read at all"
    )
    something_moves(events_path, when)
    assert beating(window, window.live.alarm_visible), "the movement raised no alarm"


def test_show_me_takes_him_to_the_footage(qtbot, tmp_path: Path) -> None:
    start, events_path = a_day_with(tmp_path, recorded=True)
    window, _ = build(qtbot, tmp_path, events_path=events_path)
    alarmed(window, events_path, start + 3660)

    window.live._show_me.click()

    assert window.tabs.currentWidget() is window.playback, "he is still on the Live tab"
    assert window.playback.stream_selector.currentText() == "thermal"
    assert "a.mp4" in window.playback.status_text, window.playback.status_text
    window.close()


def test_show_me_says_it_when_the_footage_is_gone(qtbot, tmp_path: Path) -> None:
    """Nothing was recorded on that day. He is taken to Playback anyway - the
    tab is where he can go looking - and told plainly that there is nothing
    there, rather than left in front of an empty bar."""
    start, events_path = a_day_with(tmp_path, recorded=False)
    window, _ = build(qtbot, tmp_path, events_path=events_path)
    alarmed(window, events_path, start + 3660)

    window.live._show_me.click()

    assert window.tabs.currentWidget() is window.playback
    said = window.playback.status_text.lower()
    assert "no recording" in said, window.playback.status_text
    assert "thermal" in said, window.playback.status_text
    window.close()


def test_double_clicking_a_movement_takes_him_to_it_too(qtbot, tmp_path: Path) -> None:
    start, events_path = a_day_with(tmp_path, recorded=True)
    window, _ = build(qtbot, tmp_path, events_path=events_path)
    alarmed(window, events_path, start + 3660)

    window.live._show_row(0)

    assert window.tabs.currentWidget() is window.playback
    assert "a.mp4" in window.playback.status_text, window.playback.status_text
    window.close()


def test_show_me_does_not_leave_the_head_slewing(qtbot, tmp_path: Path) -> None:
    """The hazard this console has already paid for once.

    An arrow key is held; the focus is on a child of the Live tab, so no
    focusOut will arrive, and changing tab means the key release never reaches
    the tab either. Taking him to Playback must therefore deliver the stop the
    camera is owed, or the head slews to its own end stop while he watches
    footage on another tab.
    """
    start, events_path = a_day_with(tmp_path, recorded=True)
    ptz = SteeringPtz()
    window, _ = build(qtbot, tmp_path, events_path=events_path, ptz=ptz)
    alarmed(window, events_path, start + 3660)

    window.live.key_down("right", fine=False)
    assert window.live.wait_for_camera(10.0)
    assert ptz.commands[-1] == ("move", 0.5, 0.0, 0.0)

    window.live._show_me.click()

    assert window.live.wait_for_camera(10.0), "a PTZ command never left the console"
    assert ptz.commands[-1] == ("stop",), "the head was left slewing on the Playback tab"
    window.close()


def test_show_me_costs_nothing_when_the_playback_tab_could_not_be_built(
    qtbot, tmp_path: Path, caplog
) -> None:
    """Three tabs beat no window, and that rule does not stop applying because
    the operator pressed a button."""
    start, events_path = a_day_with(tmp_path, recorded=True)
    window, _ = build(qtbot, tmp_path, events_path=events_path)
    alarmed(window, events_path, start + 3660)
    window.playback = QLabel("The Playback tab could not be opened")

    with caplog.at_level("WARNING", logger="vmd.desktop.window"):
        window.live._show_me.click()

    assert caplog.records, "nothing was said about a console that could not go"
    window.close()
