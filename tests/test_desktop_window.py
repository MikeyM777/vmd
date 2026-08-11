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
from vmd.settings import Settings, StreamSettings, load_settings, save_settings


class FakeServices:
    def __init__(self) -> None:
        self.ticks = 0
        self.stopped = False
        self.applied: list = []

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

    for applied in (services.applied, ptz.applied, radio.applied):
        assert [s.camera.host for s in applied] == ["10.0.0.9"]


def test_saving_a_new_stream_puts_it_on_the_wall(qtbot, tmp_path: Path) -> None:
    """The panes hold the URLs they were built with. A stream added in Settings
    and not on the wall is a camera the operator cannot see."""
    window, _ = build(qtbot, tmp_path)
    assert window.live.stream_names() == ["thermal"]

    window.settings_tab.add_stream_row("visible", "rtsp://camera/visible")
    assert window.settings_tab.save() is True

    assert window.live.stream_names() == ["thermal", "visible"]


def test_saving_removes_the_pane_of_a_stream_that_is_gone(qtbot, tmp_path: Path) -> None:
    """A pane still showing a stream nobody records is a picture the operator
    has no reason to trust."""
    window, _ = build(qtbot, tmp_path)
    (row,) = window.settings_tab.stream_rows()
    row.name_field.setText("infrared")

    assert window.settings_tab.save() is True

    assert window.live.stream_names() == ["infrared"]


def test_one_part_refusing_the_save_does_not_cost_the_others(qtbot, tmp_path: Path) -> None:
    """The camera is at the far end of a radio link and answers when it feels
    like it. The save itself succeeded; the rest is best effort."""
    radio = FakeRadio()
    window, services = build(qtbot, tmp_path, ptz=AngryPtz(), radio=radio)

    assert window.settings_tab.save() is True
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
    """One store, two tabs. Two connections to one file would be two answers
    to the same question."""
    from vmd.desktop.timeline import day_bounds
    from vmd.detect.events import EventStore

    start, end = day_bounds(2026, 8, 11)
    events_path = tmp_path / "recordings" / "events.db"
    store = EventStore(events_path)
    store.add("thermal", start + 3600, start + 3604, (1, 2, 3, 4), 40.0)
    store.close()

    window, _ = build(qtbot, tmp_path, events_path=events_path)
    window.heartbeat()

    assert len(window.live.recent_rows()) == 1
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

    assert services.applied == [settings]
    message = window.settings_tab.message
    assert message != "Saved."
    assert "did not restart" in message
    assert "Saved" in message, "the file really was written; say both things"


def test_a_save_that_worked_still_reads_as_saved(qtbot, tmp_path: Path) -> None:
    window, _ = build(qtbot, tmp_path, services=QuietServices())
    window.settings_tab._set_message("Saved.")
    window.settings_saved(load_settings(window._settings_path))
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


class SickServices(FakeServices):
    def state(self) -> dict:
        return {
            "recording": True,
            "streaming": "go2rtc is not installed - run install.bat",
            "restarts": {},
            "detection": {"enabled": True, "running": False, "reason": "not running"},
        }


def test_the_band_says_exactly_what_the_status_line_says(qtbot, tmp_path: Path) -> None:
    """Re-placed, not rewritten. The words were chosen for an operator who is
    not technical, and moving them out of the footer may not change one of
    them."""
    window, _ = build(qtbot, tmp_path)
    window.heartbeat()
    assert window.band.chips() == window.status_text().split(" · ")


def test_the_band_knows_which_of_its_chips_is_the_bad_one(qtbot, tmp_path: Path) -> None:
    """A wall of four identical grey sentences is what it replaced. Each part
    now carries the state it is reporting, so the one that is wrong can be the
    one that is red."""
    healthy, _ = build(qtbot, tmp_path / "well", services=FakeServices())
    states = dict((text, state) for text, state in healthy.status_parts())
    assert states["streaming: streaming"] == "ok"

    sick, _ = build(qtbot, tmp_path / "ill", services=SickServices())
    states = dict((text, state) for text, state in sick.status_parts())
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
