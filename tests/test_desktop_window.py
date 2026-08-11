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


def test_a_settings_tab_that_would_not_build_leaves_the_window_working(
    qtbot, tmp_path: Path
) -> None:
    """There is nothing to connect to, and nothing that could have been saved.
    The other three tabs are how the file gets fixed."""
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

    assert isinstance(window.settings_tab, QLabel)
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
