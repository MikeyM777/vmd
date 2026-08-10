"""go2rtc and the recorder as children of the window - and outliving it."""

from __future__ import annotations

from pathlib import Path

from vmd.desktop.services import ConsoleServices, RecorderProcess
from vmd.settings import CameraSettings, Settings, StorageSettings, StreamSettings


class FakeProcess:
    def __init__(self) -> None:
        self.alive = True
        self.terminated = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def kill(self) -> None:
        self.alive = False

    def wait(self, timeout=None):
        return 0


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name="thermal", url="rtsp://10.0.0.2/t", enabled=True)],
        ),
        storage=StorageSettings(root=tmp_path / "rec"),
    )


def test_the_recorder_is_started_as_its_own_process(tmp_path: Path) -> None:
    spawned: list[list[str]] = []
    recorder = RecorderProcess(
        settings_path=tmp_path / "settings.json",
        spawn=lambda command: (spawned.append(command), FakeProcess())[1],
    )
    recorder.start()
    assert recorder.running is True
    assert any("vmd.record_main" in part for part in spawned[0])
    assert any(str(tmp_path / "settings.json") in part for part in spawned[0])


def test_a_dead_recorder_is_restarted_by_a_tick(tmp_path: Path) -> None:
    processes: list[FakeProcess] = []

    def spawn(command):
        process = FakeProcess()
        processes.append(process)
        return process

    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=tmp_path / "settings.json",
        streaming=None,
        recorder=RecorderProcess(tmp_path / "settings.json", spawn=spawn),
    )
    services.start()
    assert services.recorder.running is True

    processes[0].alive = False          # it died on its own
    assert services.recorder.running is False
    services.tick()
    assert services.recorder.running is True
    assert len(processes) == 2


def test_stopping_the_console_stops_its_children(tmp_path: Path) -> None:
    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=tmp_path / "settings.json",
        streaming=None,
        recorder=RecorderProcess(tmp_path / "settings.json", spawn=lambda c: FakeProcess()),
    )
    services.start()
    services.stop()
    assert services.recorder.running is False


def test_a_recorder_left_running_is_adopted_not_duplicated(tmp_path: Path) -> None:
    """Children outlive the window on purpose. Opening the window again must not
    start a second recorder on the same directory - two of them would fight over
    the same files and the same index."""
    import os

    settings_path = tmp_path / "settings.json"
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")  # a PID that is alive

    spawned: list = []
    recorder = RecorderProcess(
        settings_path,
        pid_path=pid_file,
        spawn=lambda command: (spawned.append(command), FakeProcess())[1],
    )
    recorder.start()
    assert recorder.running is True, "the live process should be adopted"
    assert spawned == [], "nothing new should have been started"


def test_a_stale_pid_file_does_not_stop_a_start(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text("999999", encoding="utf-8")  # nothing is running there

    spawned: list = []
    recorder = RecorderProcess(
        settings_path,
        pid_path=pid_file,
        spawn=lambda command: (spawned.append(command), FakeProcess())[1],
    )
    recorder.start()
    assert recorder.running is True
    assert len(spawned) == 1, "a dead PID must not block recording forever"


def test_state_reports_what_the_operator_needs_to_know(tmp_path: Path) -> None:
    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=tmp_path / "settings.json",
        streaming=None,
        recorder=RecorderProcess(tmp_path / "settings.json", spawn=lambda c: FakeProcess()),
    )
    services.start()
    state = services.state()
    assert state["recording"] is True
    assert "streaming" in state
