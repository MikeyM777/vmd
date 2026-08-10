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

    def start(self) -> None: ...

    def tick(self) -> list[str]:
        self.ticks += 1
        return []

    def stop(self) -> None:
        self.stopped = True

    def local_url(self, name: str) -> str | None:
        return f"rtsp://127.0.0.1:8554/{name}"

    def state(self) -> dict:
        return {"recording": True, "streaming": "streaming", "restarts": {}}


class AngryServices(FakeServices):
    def state(self) -> dict:
        raise RuntimeError("the supervisor is not answering")


class FakePtz:
    def status(self) -> dict:
        return {"available": False, "reason": "no camera address set"}

    def move(self, pan, tilt, zoom) -> dict:
        return {"ok": True}

    def stop(self) -> dict:
        return {"ok": True}

    def home(self) -> dict:
        return {"ok": True}


class FakeRadio:
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


def build(qtbot, tmp_path: Path, services=None, radio=None, make_pane=None):
    path = write_settings(tmp_path)
    services = services if services is not None else FakeServices()
    window = ConsoleWindow(
        settings_path=path,
        services=services,
        ptz=FakePtz(),
        radio=radio if radio is not None else FakeRadio(),
        index_path=tmp_path / "segments.db",
        make_pane=make_pane or (lambda name: FakeVideoPane()),
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
    # Nothing to start means nothing was built to start.
    assert wiring.services.streaming is None
    assert wiring.services.state()["recording"] is False


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
