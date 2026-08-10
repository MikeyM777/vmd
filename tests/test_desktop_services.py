"""go2rtc and the recorder as children of the window - and outliving it."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from vmd.desktop.services import (
    ConsoleServices,
    RecorderProcess,
    _creation_flags,
    _taskkill_tree,
)
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


class StubbornProcess:
    """Records how it was asked to die, and in what order.

    `wait` raises while the process is alive, exactly as Popen.wait does, so the
    escalation is driven by the same signal the real thing gives.
    """

    def __init__(self, dies_on: str | None) -> None:
        self.dies_on = dies_on
        self.alive = True
        self.calls: list[str] = []
        self.pid = 4242

    def poll(self):
        return None if self.alive else 0

    def record(self, name: str) -> None:
        self.calls.append(name)
        if self.dies_on == name:
            self.alive = False

    def terminate(self) -> None:
        self.record("terminate")

    def kill(self) -> None:
        self.record("kill")

    def wait(self, timeout=None):
        if self.alive:
            raise subprocess.TimeoutExpired(cmd="recorder", timeout=timeout)
        return 0


def tree_killer(process: StubbornProcess, works: bool, seen: list[int]):
    def kill_tree(pid: int) -> bool:
        seen.append(pid)
        process.record("kill_tree")
        return works

    return kill_tree


def test_the_whole_recorder_tree_is_stopped_not_just_the_process_we_spawned(
    tmp_path: Path,
) -> None:
    """ffmpeg is a grandchild. Stopping only the process we spawned leaves it
    writing segments into the recording directory with nothing supervising it -
    and the next window, finding a correctly stale PID file, starts a second
    recorder on the same directory and the same index."""
    process = StubbornProcess(dies_on="kill_tree")
    seen: list[int] = []
    recorder = RecorderProcess(
        tmp_path / "settings.json",
        spawn=lambda command: process,
        kill_tree=tree_killer(process, works=True, seen=seen),
    )
    recorder.start()
    recorder.stop()

    assert process.calls == ["kill_tree"]
    assert seen == [process.pid], "the tree must be stopped by the pid we spawned"
    assert "terminate" not in process.calls, "no need to force what is already gone"
    assert recorder.running is False


def test_a_recorder_that_survives_the_tree_kill_is_escalated_and_stays_tracked(
    tmp_path: Path,
) -> None:
    """Recording must stop even for a wedged process - but only once it is confirmed
    dead may it be forgotten, or a second recorder would join the first."""
    process = StubbornProcess(dies_on=None)
    seen: list[int] = []
    recorder = RecorderProcess(
        tmp_path / "settings.json",
        spawn=lambda command: process,
        kill_tree=tree_killer(process, works=False, seen=seen),
    )
    recorder.start()
    recorder.stop()

    assert process.calls == ["kill_tree", "terminate", "kill"]
    assert recorder.running is True, "a process that may still be writing must stay tracked"


def test_the_tree_kill_reports_failure_instead_of_raising() -> None:
    """It is only an optimisation over terminate, so a failure must fall through
    to it rather than escape and leave the recorder running. Off Windows there is
    no taskkill at all, and the answer is the same."""
    assert _taskkill_tree(999999) is False


def test_the_recorder_gets_no_console_window() -> None:
    flags = _creation_flags()
    if os.name == "nt":
        assert flags & subprocess.CREATE_NO_WINDOW, "the operator must not get a console window"
    else:
        assert flags == 0


def test_an_adopted_recorder_is_not_killed_by_stop(tmp_path: Path) -> None:
    """It belongs to a window that is gone; stopping this one must not stop recording."""
    settings_path = tmp_path / "settings.json"
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    recorder = RecorderProcess(settings_path, pid_path=pid_file, spawn=lambda c: FakeProcess())
    recorder.start()
    assert recorder.running is True
    recorder.stop()
    assert recorder.running is False, "it lets go of the adopted process"
    # The adopted PID is this test process. If stop() had signalled or killed it,
    # this line would never run.
    assert os.getpid() > 0


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
