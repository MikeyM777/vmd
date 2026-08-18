"""go2rtc and the recorder as children of the window - and outliving it."""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from tests.test_streaming import FakeGo2rtc
from vmd.desktop.disk import DiskWatcher
from vmd.desktop.logs import LogBuffer, attach
from vmd.desktop.services import (
    DETECTION_STATUS_FILENAME,
    FLAP_LIMIT,
    FLAP_WINDOW,
    SPAWN_LIMIT,
    ConsoleServices,
    DetectorProcess,
    RecorderProcess,
    _creation_flags,
    _default_spawn,
    _pid_alive,
    _taskkill_tree,
    detector_fingerprint,
    process_started_at,
    read_detection_status,
    recordable,
    recorder_fingerprint,
    streaming_fingerprint,
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


def settings_for(tmp_path: Path, detect: bool = False) -> Settings:
    """Settings for a console that is genuinely recording.

    The folder and a fresh segment are made here on purpose. "Recording" now
    means footage is reaching the disk, not that a process was alive when the
    console looked, so a test about anything else must not accidentally be a
    test about an empty folder.
    """
    root = tmp_path / "rec"
    recorded(root, "thermal")
    return Settings(
        # Recording is off while the Playback tab is - see `recordable` - and
        # this fixture is for a console that is genuinely recording.
        show_playback=True,
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(
                    name="thermal", url="rtsp://10.0.0.2/t", enabled=True, detect=detect
                )
            ],
        ),
        storage=StorageSettings(root=root),
    )


def recorded(root: Path, stream: str, age: float = 1.0) -> Path:
    """One segment file, written `age` seconds ago."""
    directory = root / stream
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "2026-08-11_10-00-00.mp4"
    path.write_bytes(b"\0" * 4096)
    written = time.time() - age
    os.utime(path, (written, written))
    return path


def watching(settings: Settings) -> DiskWatcher:
    """A disk watcher that has read the folder, and reads again when told to.

    Read once here, on the caller's thread, so that no test waits on one - and
    so that "recording" in these tests means what it means in the console:
    footage has been seen arriving in a folder somebody actually looked at. A
    watcher handed over unread makes every `state()["recording"] is True` in
    this file prove nothing but that a process object exists.
    """
    watcher = DiskWatcher(settings, executor=lambda work: work(), clock=Clock())
    watcher.poll()
    return watcher


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
    settings = settings_for(tmp_path)
    services = ConsoleServices(
        settings=settings,
        settings_path=tmp_path / "settings.json",
        streaming=None,
        recorder=RecorderProcess(tmp_path / "settings.json", spawn=lambda c: FakeProcess()),
        disk=watching(settings),
    )
    services.start()
    state = services.state()
    assert state["recording"] is True
    assert "streaming" in state


# ------------------------------------------------------------- the detector
#
# Supervised exactly like the recorder, and for the same reason: the console
# must not be able to stop detection, and detection must not be able to stop
# the console.


class Clock:
    """A hand-wound monotonic clock. No test here waits for real seconds."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class DeadOnArrival:
    """A child that is never alive - the detector that will not stay up."""

    def __init__(self) -> None:
        self.pid = 31337

    def poll(self):
        return 1

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout=None):
        return 1


def console_with_detector(
    tmp_path: Path, detector_spawn, detect: bool = True, clock=None, now=None
):
    settings_path = tmp_path / "settings.json"
    settings = settings_for(tmp_path, detect=detect)
    return ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=None,
        recorder=RecorderProcess(
            settings_path, pid_path=tmp_path / "recorder.pid", spawn=lambda c: FakeProcess()
        ),
        detector=DetectorProcess(
            settings_path, pid_path=tmp_path / "detector.pid", spawn=detector_spawn
        ),
        clock=clock or Clock(),
        now=now or (lambda: 1_000_000.0),
        disk=watching(settings),
        # Read on the caller's thread, so no test here waits on one. The
        # detector's report is read on a worker in the console, for the same
        # reason the folder is.
        executor=lambda work: work(),
    )


def test_the_detector_is_started_as_its_own_process(tmp_path: Path) -> None:
    spawned: list[list[str]] = []
    detector = DetectorProcess(
        settings_path=tmp_path / "settings.json",
        spawn=lambda command: (spawned.append(command), FakeProcess())[1],
    )
    detector.start()
    assert detector.running is True
    assert any("vmd.detect_main" in part for part in spawned[0])
    assert any(str(tmp_path / "settings.json") in part for part in spawned[0])


def test_a_detector_left_running_is_adopted_not_duplicated(tmp_path: Path) -> None:
    """It writes into events.db. Two of them would fight over the same file."""
    pid_file = tmp_path / "detector.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    spawned: list = []
    detector = DetectorProcess(
        tmp_path / "settings.json",
        pid_path=pid_file,
        spawn=lambda command: (spawned.append(command), FakeProcess())[1],
    )
    detector.start()
    assert detector.running is True
    assert spawned == [], "the live detector should have been adopted"


def test_the_detector_keeps_its_own_pid_file(tmp_path: Path) -> None:
    """Sharing recorder.pid would make each adopt the other."""
    detector = DetectorProcess(tmp_path / "settings.json")
    recorder = RecorderProcess(tmp_path / "settings.json")
    assert detector.pid_path != recorder.pid_path


def test_detection_nobody_asked_for_reads_as_off_not_as_failure(tmp_path: Path) -> None:
    """No stream ticked for detection is a choice, not a fault. An operator who
    is told detection failed when they never turned it on learns to ignore the
    line that will one day say something true."""
    services = console_with_detector(tmp_path, lambda c: FakeProcess(), detect=False)
    services.start()
    detection = services.state()["detection"]

    assert detection["enabled"] is False
    assert "off" in detection["reason"].lower()
    assert "fail" not in detection["reason"].lower()
    assert "not running" not in detection["reason"].lower()


def test_a_detector_nobody_asked_for_is_never_started(tmp_path: Path) -> None:
    """`vmd.detect_main` prints "nothing to detect" and exits 0 when no stream
    is ticked. Supervising that would respawn the same exit every two seconds
    for as long as the console is open."""
    spawned: list = []
    services = console_with_detector(
        tmp_path,
        lambda command: (spawned.append(command), FakeProcess())[1],
        detect=False,
    )
    services.start()
    services.tick()
    services.tick()
    assert spawned == []


def test_a_running_detector_says_it_is_detecting(tmp_path: Path) -> None:
    services = console_with_detector(tmp_path, lambda c: FakeProcess())
    services.start()
    detection = services.state()["detection"]
    assert detection["enabled"] is True
    assert detection["running"] is True
    assert "detecting" in detection["reason"].lower()


def test_a_detector_that_will_not_stay_up_is_reported_not_hidden(tmp_path: Path) -> None:
    """Restarting forever while the operator believes the perimeter is watched
    is the failure this reports. The clock is hand-wound: no waiting, and the
    test cannot hang however the restart policy is broken."""
    clock = Clock()
    services = console_with_detector(tmp_path, lambda c: DeadOnArrival(), clock=clock)
    services.start()

    for _ in range(6):
        clock.advance(3.0)  # past the supervisor's restart delay
        services.tick()

    detection = services.state()["detection"]
    assert detection["enabled"] is True
    assert detection["running"] is False
    assert detection["restarts"] >= 4
    reason = detection["reason"].lower()
    assert "not running" in reason
    assert str(detection["restarts"]) in detection["reason"], "the restarts must be named"


def test_a_detector_that_dies_once_is_just_restarted(tmp_path: Path) -> None:
    """One death is not a failing detector; it is what a supervisor is for."""
    clock = Clock()
    processes: list[FakeProcess] = []

    def spawn(command):
        process = FakeProcess()
        processes.append(process)
        return process

    services = console_with_detector(tmp_path, spawn, clock=clock)
    services.start()
    processes[0].alive = False
    clock.advance(3.0)
    services.tick()

    assert len(processes) == 2
    detection = services.state()["detection"]
    assert detection["running"] is True
    assert "not running" not in detection["reason"].lower()


def test_stopping_detection_does_not_stop_recording(tmp_path: Path) -> None:
    """The oldest requirement in the system, from the detection side."""
    services = console_with_detector(tmp_path, lambda c: FakeProcess())
    services.start()
    assert services.recorder.running is True
    assert services.detector.running is True

    services.detector.stop()

    assert services.detector.running is False
    assert services.recorder.running is True
    assert services.state()["recording"] is True


def test_stopping_the_recorder_does_not_stop_detection(tmp_path: Path) -> None:
    services = console_with_detector(tmp_path, lambda c: FakeProcess())
    services.start()
    services.recorder.stop()

    assert services.recorder.running is False
    assert services.detector.running is True
    assert services.state()["detection"]["running"] is True


def test_a_console_with_no_detector_still_reports_a_state(tmp_path: Path) -> None:
    """--no-services builds no detector. The status line still has to say something."""
    services = ConsoleServices(
        settings=settings_for(tmp_path, detect=True),
        settings_path=tmp_path / "settings.json",
        streaming=None,
        recorder=RecorderProcess(tmp_path / "settings.json", spawn=lambda c: FakeProcess()),
    )
    detection = services.state()["detection"]
    assert detection["running"] is False
    assert detection["reason"]


# ------------------------------------------------ what the children say out loud
#
# The machine is offline and the operator has no terminal, so the Logs tab is
# the only window into the child processes. Sending their output to DEVNULL has
# already cost this project a day: go2rtc's "401 Unauthorized" was the one line
# that said why there was no picture, and it was being thrown away.


@contextlib.contextmanager
def console_log_buffer(capacity: int = 500):
    """The buffer the Logs tab reads, attached exactly where the console attaches it.

    On the root logger, at INFO - not on a private logger - so that these tests
    fail if a child's line is logged somewhere the Logs tab never looks.
    """
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)
    buffer = attach(LogBuffer(capacity=capacity))
    try:
        yield buffer
    finally:
        root.removeHandler(buffer)
        root.setLevel(previous)


class PipedProcess(FakeProcess):
    """A child whose stdout is a pipe under the test's control, as Popen's is."""

    def __init__(self, output: bytes = b"") -> None:
        super().__init__()
        self.pid = 5150
        self.stdout = io.BytesIO(output)


class BlockingStream:
    """A pipe that says nothing until it is released - a working child, mostly.

    Every wait in here is bounded, so a reader that never arrives fails the
    test in five seconds instead of wedging the suite.
    """

    def __init__(self) -> None:
        self.release = threading.Event()
        self.spent = False

    def read(self, size: int = -1) -> bytes:
        if self.spent:
            return b""
        self.release.wait(5.0)
        self.spent = True
        return b"late line\n"

    def close(self) -> None:
        self.spent = True


def child_lines(buffer: LogBuffer, source: str) -> list[str]:
    return [line["text"] for line in buffer.snapshot() if line["source"] == source]


def test_a_child_s_output_reaches_the_logs_tab(tmp_path: Path) -> None:
    """The line that says why there is no picture has to arrive somewhere the
    operator can read it, which is this buffer and nowhere else."""
    with console_log_buffer() as buffer:
        recorder = RecorderProcess(
            tmp_path / "settings.json", spawn=lambda c: PipedProcess(b"401 Unauthorized\n")
        )
        recorder.start()
        assert recorder.wait_for_output(5.0), "the reader must finish when the pipe ends"
        texts = [line["text"] for line in buffer.snapshot()]

    assert any("401 Unauthorized" in text for text in texts)


def test_each_line_says_which_child_it_came_from(tmp_path: Path) -> None:
    """"Segment closed" from nowhere is a line the operator cannot act on."""
    with console_log_buffer() as buffer:
        recorder = RecorderProcess(
            tmp_path / "settings.json",
            pid_path=tmp_path / "recorder.pid",
            spawn=lambda c: PipedProcess(b"segment closed\n"),
        )
        detector = DetectorProcess(
            tmp_path / "settings.json",
            pid_path=tmp_path / "detector.pid",
            spawn=lambda c: PipedProcess(b"thermal: detecting\n"),
        )
        recorder.start()
        detector.start()
        assert recorder.wait_for_output(5.0)
        assert detector.wait_for_output(5.0)

        from_recorder = child_lines(buffer, "recorder")
        from_detector = child_lines(buffer, "detector")

    assert any("segment closed" in text for text in from_recorder)
    assert any("recorder" in text for text in from_recorder), "the line must name its child"
    assert any("thermal: detecting" in text for text in from_detector)
    assert any("detector" in text for text in from_detector)
    assert not any("segment closed" in text for text in from_detector)


def test_reading_a_child_s_pipe_never_blocks_the_caller(tmp_path: Path) -> None:
    """start() runs on the GUI thread. A pipe read there would freeze the window
    for as long as the child stayed quiet - which, for a recorder that is
    working, is nearly all of the time."""
    stream = BlockingStream()
    process = PipedProcess()
    process.stdout = stream

    with console_log_buffer() as buffer:
        recorder = RecorderProcess(tmp_path / "settings.json", spawn=lambda c: process)
        began = time.perf_counter()
        recorder.start()
        elapsed = time.perf_counter() - began

        assert elapsed < 1.0, f"start() waited {elapsed:.2f}s on a silent child"
        assert recorder.output_thread is not None
        assert recorder.output_thread.daemon is True, "it must not hold the interpreter open"

        stream.release.set()
        assert recorder.wait_for_output(5.0)
        texts = [line["text"] for line in buffer.snapshot()]

    assert any("late line" in text for text in texts)


def test_a_child_that_dies_mid_line_still_reports_what_it_said(tmp_path: Path) -> None:
    """No trailing newline because there was no time for one. That half line is
    usually the most interesting thing the child ever said."""
    with console_log_buffer() as buffer:
        recorder = RecorderProcess(
            tmp_path / "settings.json",
            spawn=lambda c: PipedProcess(b"ffmpeg: connection refused"),
        )
        recorder.start()
        assert recorder.wait_for_output(5.0)
        texts = [line["text"] for line in buffer.snapshot()]

    assert any("connection refused" in text for text in texts)


def test_output_that_is_not_utf8_does_not_kill_the_reader(tmp_path: Path) -> None:
    """ffmpeg puts filenames in the machine's own code page, and a chunk can end
    half way through a multi-byte character. Neither may end the reader - the
    next line is the one that matters."""
    with console_log_buffer() as buffer:
        recorder = RecorderProcess(
            tmp_path / "settings.json",
            spawn=lambda c: PipedProcess(b"\xff\xfe not utf-8\nthe next line still arrives\n"),
        )
        recorder.start()
        assert recorder.wait_for_output(5.0)
        texts = [line["text"] for line in buffer.snapshot()]

    assert any("not utf-8" in text for text in texts)
    assert any("the next line still arrives" in text for text in texts)


def test_a_torrent_of_output_cannot_grow_the_log_without_bound(tmp_path: Path) -> None:
    """A child in a restart loop can write for hours. The buffer's capacity is
    the whole defence, and it is worth nothing if the path to it accumulates."""
    output = b"".join(b"line %d\n" % index for index in range(5000))
    with console_log_buffer(capacity=10) as buffer:
        recorder = RecorderProcess(
            tmp_path / "settings.json", spawn=lambda c: PipedProcess(output)
        )
        recorder.start()
        assert recorder.wait_for_output(5.0)
        lines = buffer.snapshot()

    assert len(lines) == 10
    assert "line 4999" in lines[-1]["text"]


def test_one_enormous_line_is_cut_rather_than_held_whole(tmp_path: Path) -> None:
    """A child that writes half a megabyte without a newline must not be able to
    make the console hold half a megabyte on its way into a ring that would have
    dropped it anyway."""
    with console_log_buffer() as buffer:
        recorder = RecorderProcess(
            tmp_path / "settings.json",
            spawn=lambda c: PipedProcess(b"x" * 500_000 + b"\nafter the flood\n"),
        )
        recorder.start()
        assert recorder.wait_for_output(5.0)
        texts = [line["text"] for line in buffer.snapshot()]

    assert texts, "the flood must not swallow everything"
    assert max(len(text) for text in texts) < 5000, "one line held whole is the bug"
    assert any("after the flood" in text for text in texts)


class ChunkedStream:
    """Half a megabyte with no newline anywhere in it, handed over a chunk at a time.

    It records what had been emitted at the moment the pipe ended, which is the
    only way to tell a reader that reports as it goes from one that holds the
    whole flood and hands it over at the end.
    """

    def __init__(self, chunks: int, emitted: list[str]) -> None:
        self.remaining = chunks
        self._emitted = emitted
        self.emitted_before_the_end: list[str] | None = None

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            self.emitted_before_the_end = list(self._emitted)
            return b""
        self.remaining -= 1
        return b"x" * 8192


def test_a_child_that_never_sends_a_newline_is_not_held_whole(tmp_path: Path) -> None:
    """The ring buffer's capacity defends nothing if the reader in front of it
    accumulates. A child writing without a newline - a progress bar, a binary
    probe, an ffmpeg wedged on carriage returns - must be reported as it goes
    and forgotten, not gathered until it stops."""
    from vmd.desktop.services import CHILD_LINE_LIMIT, read_child_output

    emitted: list[str] = []
    stream = ChunkedStream(64, emitted)  # half a megabyte, not one newline in it
    read_child_output(stream, emitted.append)

    assert stream.emitted_before_the_end, "the reader held the whole flood until the pipe ended"
    assert emitted, "and it must still have said something"
    assert max(len(text) for text in emitted) <= CHILD_LINE_LIMIT + 40


def test_an_adopted_child_says_its_output_cannot_be_shown(tmp_path: Path) -> None:
    """It was started by a console that is gone, and its pipe went with it. An
    empty Logs tab would read as "the recorder has nothing to say", which is the
    opposite of the truth."""
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    with console_log_buffer() as buffer:
        recorder = RecorderProcess(
            tmp_path / "settings.json", pid_path=pid_file, spawn=lambda c: PipedProcess()
        )
        recorder.start()
        said = child_lines(buffer, "recorder")

    assert recorder.running is True
    assert said, "an adopted child must not be silent about being silent"
    told = " ".join(said).lower()
    assert "adopted" in told
    assert "output" in told
    assert "recorder" in told


def test_an_adopted_streaming_server_says_the_same(tmp_path: Path) -> None:
    """go2rtc is the one process that prints the camera's "401 Unauthorized".
    When the console adopted one it had not started, that line stopped arriving
    and nothing said why - which is the whole failure, one process along."""
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    (tmp_path / "streaming.json").write_text(
        json.dumps({"api_port": port, "rtsp_port": port, "streams": {}}), encoding="utf-8"
    )

    class Streaming:
        api_port = 1984
        rtsp_port = 8554

        def unadoptable(self, endpoint: dict) -> str:
            return ""  # it answers, and it is serving what was asked for

        def start(self) -> None:
            raise AssertionError("a live server must not be duplicated")

        def stop(self) -> None: ...

        @property
        def running(self) -> bool:
            return True

    try:
        with console_log_buffer() as buffer:
            services = ConsoleServices(
                settings=settings_for(tmp_path),
                settings_path=tmp_path / "settings.json",
                streaming=Streaming(),
                recorder=RecorderProcess(
                    tmp_path / "settings.json", spawn=lambda c: PipedProcess()
                ),
            )
            services.start()
            said = child_lines(buffer, "go2rtc")
    finally:
        listener.close()

    assert services.adopted_streaming is True
    told = " ".join(said).lower()
    assert "adopted" in told and "output" in told and "go2rtc" in told


def test_the_default_spawn_captures_the_child_s_output_instead_of_discarding_it(
    monkeypatch,
) -> None:
    """DEVNULL here is the defect. It is also invisible: everything still runs,
    and the operator simply never learns why."""
    seen: dict = {}

    def fake_popen(command, **kwargs):
        seen.update(kwargs)
        seen["command"] = command
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    _default_spawn([sys.executable, "-m", "vmd.record_main"])

    assert seen["stdout"] is subprocess.PIPE
    assert seen["stderr"] is subprocess.STDOUT, "logging goes to stderr; it must arrive too"
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen.get("bufsize") == 0, "a buffered pipe hides the child's output until it fills"


def test_the_child_is_started_unbuffered_so_its_output_arrives_while_it_matters(
    tmp_path: Path,
) -> None:
    """Python block-buffers stdout when it is a pipe. Without -u the operator
    watches an empty Logs tab while the child fills eight kilobytes, which for a
    recorder saying one line a minute is most of an hour."""
    spawned: list[list[str]] = []
    recorder = RecorderProcess(
        tmp_path / "settings.json",
        spawn=lambda command: (spawned.append(command), PipedProcess())[1],
    )
    recorder.start()
    assert "-u" in spawned[0]


def test_stopping_a_child_does_not_wedge_on_its_reader(tmp_path: Path) -> None:
    """stop() runs on the GUI thread as the window closes. Whatever the reader
    is doing, it may not be able to hold the close open."""
    stream = BlockingStream()
    process = PipedProcess()
    process.stdout = stream

    recorder = RecorderProcess(
        tmp_path / "settings.json", spawn=lambda c: process, kill_tree=lambda pid: True
    )
    recorder.start()
    began = time.perf_counter()
    recorder.stop()
    elapsed = time.perf_counter() - began

    assert elapsed < 5.0, f"stop() took {elapsed:.2f}s waiting on a reader"
    stream.release.set()


# --------------------------------------------- per-stream detection health
#
# `DetectionService.status()` knows, per stream, whether the capture opened and
# why not. It lives in the detector process; without a seam it never reaches the
# console, which can then only say "detection is running" at process level.


def write_detection_status(
    tmp_path: Path,
    streams: list[dict],
    written_at: float = 1_000_000.0,
    interval: float = 5.0,
) -> Path:
    path = tmp_path / "rec" / DETECTION_STATUS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "streams": streams,
                "detecting": sum(1 for stream in streams if stream.get("opened")),
                "configured": len(streams),
                "events": 0,
                "events_db": str(tmp_path / "rec" / "events.db"),
                "written_at": written_at,
                "interval": interval,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_the_console_names_the_stream_it_cannot_see_and_why(tmp_path: Path) -> None:
    """Detection continuing on the thermal while the visible is unreachable is a
    normal Tuesday on a 700 m link. The console must name which one and say why,
    and must never read it as detection having failed."""
    write_detection_status(
        tmp_path,
        [
            {"stream": "thermal", "opened": True, "reason": "detecting", "events": 3},
            {
                "stream": "visible",
                "opened": False,
                "reason": "the stream could not be opened - check the address",
                "events": 0,
            },
        ],
    )
    services = console_with_detector(
        tmp_path, lambda c: FakeProcess(), now=lambda: 1_000_002.0
    )
    services.start()
    detection = services.state()["detection"]

    assert detection["running"] is True, "one blind stream is not a dead detector"
    reason = detection["reason"]
    assert "thermal" in reason
    assert "visible" in reason
    assert "could not be opened" in reason
    assert detection["streams_known"] is True
    by_name = {stream["stream"]: stream for stream in detection["streams"]}
    assert by_name["thermal"]["opened"] is True
    assert by_name["visible"]["opened"] is False


def test_every_stream_open_reads_as_plain_detection(tmp_path: Path) -> None:
    write_detection_status(
        tmp_path,
        [
            {"stream": "thermal", "opened": True, "reason": "detecting"},
            {"stream": "visible", "opened": True, "reason": "detecting"},
        ],
    )
    services = console_with_detector(
        tmp_path, lambda c: FakeProcess(), now=lambda: 1_000_002.0
    )
    services.start()
    detection = services.state()["detection"]

    assert detection["running"] is True
    assert detection["streams_known"] is True
    assert "not " not in detection["reason"].lower()
    assert "thermal" in detection["reason"] and "visible" in detection["reason"]


def test_no_stream_open_is_reported_without_calling_the_process_dead(tmp_path: Path) -> None:
    """Both streams unreachable is bad and has to say so - but the process is up,
    and a console that called it "not running" would send the operator hunting
    for a crash that never happened."""
    write_detection_status(
        tmp_path,
        [
            {"stream": "thermal", "opened": False, "reason": "the stream could not be opened"},
            {"stream": "visible", "opened": False, "reason": "the stream could not be opened"},
        ],
    )
    services = console_with_detector(
        tmp_path, lambda c: FakeProcess(), now=lambda: 1_000_002.0
    )
    services.start()
    detection = services.state()["detection"]

    assert detection["running"] is True
    assert "thermal" in detection["reason"] and "visible" in detection["reason"]
    assert "could not be opened" in detection["reason"]


def test_a_stale_status_file_is_unknown_not_healthy(tmp_path: Path) -> None:
    """A detector wedged an hour ago left a file saying everything was fine. The
    console repeating that is worse than saying nothing."""
    write_detection_status(
        tmp_path, [{"stream": "thermal", "opened": True, "reason": "detecting"}]
    )
    services = console_with_detector(
        tmp_path, lambda c: FakeProcess(), now=lambda: 1_000_600.0
    )
    services.start()
    detection = services.state()["detection"]

    assert detection["streams_known"] is False
    assert detection["streams"] == [], "a stale file must not be repeated as if it were current"
    assert "unknown" in detection["reason"].lower()


def test_the_staleness_threshold_follows_the_detector_s_own_interval(tmp_path: Path) -> None:
    """A detector told to report once a minute must not read as permanently
    stale, or --interval would silently turn this off."""
    write_detection_status(
        tmp_path,
        [{"stream": "thermal", "opened": True, "reason": "detecting"}],
        interval=60.0,
    )
    fresh = console_with_detector(tmp_path, lambda c: FakeProcess(), now=lambda: 1_000_100.0)
    fresh.start()
    assert fresh.state()["detection"]["streams_known"] is True

    stale = console_with_detector(tmp_path, lambda c: FakeProcess(), now=lambda: 1_000_500.0)
    stale.start()
    assert stale.state()["detection"]["streams_known"] is False


def test_a_status_file_dated_in_the_future_is_not_treated_as_fresh(tmp_path: Path) -> None:
    """The laptop's clock is set by hand and gets set wrong. A file from next
    week is a clock that moved, not a healthy detector."""
    write_detection_status(
        tmp_path, [{"stream": "thermal", "opened": True, "reason": "detecting"}]
    )
    services = console_with_detector(tmp_path, lambda c: FakeProcess(), now=lambda: 900_000.0)
    services.start()
    assert services.state()["detection"]["streams_known"] is False


def test_a_missing_status_file_is_unknown_not_healthy(tmp_path: Path) -> None:
    """The console opens before the detector has written anything even once."""
    services = console_with_detector(tmp_path, lambda c: FakeProcess())
    services.start()
    detection = services.state()["detection"]

    assert detection["running"] is True
    assert detection["streams_known"] is False
    assert detection["streams"] == []
    assert "unknown" in detection["reason"].lower()


def test_a_status_file_the_console_cannot_make_sense_of_never_raises(tmp_path: Path) -> None:
    """Half written, truncated, empty, or written by an older version with
    different keys. Each has to produce a status line, not a traceback in the one
    place the operator can read."""
    path = tmp_path / "rec" / DETECTION_STATUS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each of these carries a timestamp the console would call fresh wherever
    # one is possible, so that the freshness check cannot quietly stand in for
    # the shape check and hide a payload nobody validated.
    broken = [
        '{"streams": [',                          # cut off mid-write
        "",                                       # created and not yet written
        "null",                                   # valid JSON, not an object
        "[]",                                     # valid JSON, wrong shape
        '{"written_at": 1000000.0, "interval": 5.0}',           # older version: no such key
        '{"detecting": 1, "configured": 2, "written_at": 1000000.0}',  # older version again
        '{"streams": "thermal", "written_at": 1000000.0, "interval": 5.0}',  # wrong type
        '{"streams": [], "written_at": "soon"}',  # right key, unusable timestamp
    ]
    for content in broken:
        path.write_text(content, encoding="utf-8")
        services = console_with_detector(tmp_path, lambda c: FakeProcess())
        services.start()
        detection = services.state()["detection"]
        assert detection["streams_known"] is False, content
        assert detection["streams"] == [], content
        assert detection["reason"], content


def test_a_status_path_that_cannot_be_read_at_all_is_not_an_exception(tmp_path: Path) -> None:
    """A directory where the file should be, a disk that has gone away. The
    status line has to survive both."""
    directory = tmp_path / "not-a-file"
    directory.mkdir()
    assert read_detection_status(directory) is None
    assert read_detection_status(tmp_path / "nothing-here.json") is None


# --------------------------------------------------- what a save has to reach
#
# go2rtc parses its configuration once, at startup, and the detector reads its
# own settings once. A save that only writes the file changes nothing that is
# running, on a machine whose operator has a keyboard and no terminal.


class RecordingStreaming:
    """A go2rtc that remembers what it was asked to do."""

    api_port = 1984
    rtsp_port = 8554

    def __init__(self) -> None:
        self.applied: list[Settings] = []
        self.starts = 0

    def apply(self, settings: Settings) -> None:
        self.applied.append(settings)

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None: ...

    @property
    def running(self) -> bool:
        return True


def test_a_saved_camera_address_reaches_the_streaming_server(tmp_path: Path) -> None:
    """The address is entered in Settings and nowhere else. go2rtc holds the
    one it was started with until it is restarted, so without this the corrected
    camera stays dark until the laptop is rebooted.

    The address that reaches go2rtc is the stream's own URL: that is what
    `build_config` puts in the config file. `camera.host` is where the PTZ and
    the radio are pointed and appears nowhere in it, so changing that alone must
    not cost the picture - and through go2rtc, the recorder's source - a restart.
    """
    streaming = RecordingStreaming()
    settings_path = tmp_path / "settings.json"
    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=settings_path,
        streaming=streaming,
        recorder=RecorderProcess(
            settings_path, pid_path=tmp_path / "recorder.pid", spawn=lambda c: FakeProcess()
        ),
    )
    services.start()

    corrected = settings_for(tmp_path)
    corrected.camera.streams[0].url = "rtsp://10.0.0.9/t"
    services.apply(corrected)

    assert [s.camera.streams[0].url for s in streaming.applied] == ["rtsp://10.0.0.9/t"]
    assert services.settings is corrected

    moved_ptz = corrected.model_copy(deep=True)
    moved_ptz.camera.host = "10.0.0.9"
    services.apply(moved_ptz)

    assert len(streaming.applied) == 1, "the video was restarted for a PTZ address"
    assert streaming.settings is moved_ptz, "but it must still hold what was saved"


def test_a_save_never_stops_the_recorder(tmp_path: Path) -> None:
    """The oldest requirement in the system. Recording is not a setting."""
    settings_path = tmp_path / "settings.json"
    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=settings_path,
        streaming=RecordingStreaming(),
        recorder=RecorderProcess(
            settings_path, pid_path=tmp_path / "recorder.pid", spawn=lambda c: FakeProcess()
        ),
    )
    services.start()
    running = services.recorder._process

    services.apply(settings_for(tmp_path))

    assert services.recorder.running is True
    assert services.recorder._process is running, "the recorder was restarted by a save"


def test_a_save_moves_the_detector_report_with_the_recording_folder(tmp_path: Path) -> None:
    """The detector writes its per-stream report beside events.db. Left pointing
    at the old folder the status line would read a file nobody writes again."""
    services = console_with_detector(tmp_path, lambda c: FakeProcess())
    services.start()

    moved = settings_for(tmp_path, detect=True)
    moved.storage.root = tmp_path / "elsewhere"
    services.apply(moved)

    assert services.detection_status_path == tmp_path / "elsewhere" / DETECTION_STATUS_FILENAME


def test_turning_detection_on_and_saving_starts_and_supervises_the_detector(
    tmp_path: Path,
) -> None:
    """Ticked in Settings, and nothing happened: the detector was never
    supervised, and the status line went on saying detection was off."""
    services = console_with_detector(tmp_path, lambda c: FakeProcess(), detect=False)
    services.start()
    assert services.detecting is False
    assert services.detector.running is False

    services.apply(settings_for(tmp_path, detect=True))

    assert services.detecting is True
    assert services.detector.running is True
    assert "detector" in [entry.name for entry in services.supervisor.managed]
    assert services.state()["detection"]["running"] is True


def test_turning_detection_off_and_saving_stops_the_detector(tmp_path: Path) -> None:
    services = console_with_detector(tmp_path, lambda c: FakeProcess(), detect=True)
    services.start()
    assert services.detector.running is True

    services.apply(settings_for(tmp_path, detect=False))

    assert services.detecting is False
    assert services.detector.running is False
    assert "detector" not in [entry.name for entry in services.supervisor.managed]
    # And the supervisor does not bring back what the operator turned off.
    services.tick()
    assert services.detector.running is False


def test_a_save_restarts_the_detector_so_it_reads_what_was_saved(tmp_path: Path) -> None:
    """Sensitivity, the sky line and the ignore mask are read once, when the
    detector starts. A save that left it running would leave the operator
    watching a setting that never took effect."""
    processes: list[FakeProcess] = []

    def spawn(command):
        processes.append(FakeProcess())
        return processes[-1]

    services = console_with_detector(tmp_path, spawn, detect=True)
    services.start()
    assert len(processes) == 1

    touchier = settings_for(tmp_path, detect=True)
    touchier.camera.streams[0].sensitivity = "high"
    services.apply(touchier)

    assert len(processes) == 2, "the detector kept the settings it started with"
    assert processes[0].alive is False
    assert services.detector.running is True


# ------------------------------------------- what "recording" is allowed to mean
#
# It used to mean "a process was alive when the console last looked". With the
# recordings folder on a drive that does not exist, that read "recording" in 41
# of 79 samples taken over forty seconds, while twenty recorder processes came
# and went and not one byte was ever written.


def recording_console(tmp_path: Path, settings: Settings, spawn=None, clock=None):
    settings_path = tmp_path / "settings.json"
    return ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=None,
        recorder=RecorderProcess(
            settings_path,
            pid_path=tmp_path / "recorder.pid",
            spawn=spawn or (lambda c: FakeProcess()),
            clock=clock,
        ),
        clock=clock or Clock(),
        disk=watching(settings),
    )


def test_recording_means_footage_is_reaching_the_disk(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.storage.root = tmp_path / "not-a-drive"  # nothing was ever written
    services = recording_console(tmp_path, settings)
    services.start()
    services.tick()

    state = services.state()
    assert services.recorder.running is True, "the process really is alive"
    assert state["recording"] is False, "a live process is not footage on a disk"
    assert "not recording" in state["recording_state"]["reason"].lower()


def test_footage_arriving_reads_as_recording(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    services = recording_console(tmp_path, settings)
    services.start()
    services.tick()

    state = services.state()
    assert state["recording"] is True
    assert state["recording_state"]["reason"] == "recording"


def test_a_folder_that_stopped_growing_stops_reading_as_recording(tmp_path: Path) -> None:
    """ffmpeg blocked on a dead RTSP socket keeps its process alive for ever."""
    settings = settings_for(tmp_path)
    recorded(settings.storage.root, "thermal", age=3 * settings.storage.segment_seconds)
    services = recording_console(tmp_path, settings)
    services.start()
    services.tick()

    assert services.recorder.running is True
    assert services.state()["recording"] is False


# ------------------------------------- a recorder that died and was restarted
#
# The detector has said this since it was written. The recorder, which matters
# more, said only "recording" or "NOT recording".


def test_a_recorder_that_will_not_stay_up_is_reported_not_hidden(tmp_path: Path) -> None:
    """A recorder restarted every few seconds is not recording: a process that
    lives two seconds writes no usable segment. The clock is hand-wound, so this
    cannot hang however the restart policy is broken."""
    clock = Clock()
    settings = settings_for(tmp_path)
    services = recording_console(
        tmp_path, settings, spawn=lambda c: DeadOnArrival(), clock=clock
    )
    services.start()
    for _ in range(6):
        clock.advance(3.0)  # past the supervisor's restart delay
        services.tick()

    state = services.state()
    recording = state["recording_state"]
    assert state["recording"] is False
    assert recording["restarts"] > FLAP_LIMIT
    assert "not recording" in recording["reason"].lower()
    assert str(recording["restarts"]) in recording["reason"], "the restarts must be named"


def test_a_recorder_that_died_once_is_still_recording_and_says_it_was_restarted(
    tmp_path: Path,
) -> None:
    """One death is not a failing recorder; it is what a supervisor is for. But
    the operator is still told, because a restart is a gap in the footage."""
    clock = Clock()
    processes: list[FakeProcess] = []

    def spawn(command):
        processes.append(FakeProcess())
        return processes[-1]

    settings = settings_for(tmp_path)
    services = recording_console(tmp_path, settings, spawn=spawn, clock=clock)
    services.start()
    processes[0].alive = False
    clock.advance(3.0)
    services.tick()

    recording = services.state()["recording_state"]
    assert len(processes) == 2
    assert recording["running"] is True
    assert recording["restarts"] == 1
    assert "restarted" in recording["reason"], "a gap in the footage is not nothing"
    assert "not recording" not in recording["reason"].lower()


def test_the_recorder_and_the_detector_are_judged_by_one_flap_rule(tmp_path: Path) -> None:
    """One rule, not two copies of it that drift apart."""
    clock = Clock()
    settings = settings_for(tmp_path, detect=True)
    settings_path = tmp_path / "settings.json"
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=None,
        recorder=RecorderProcess(
            settings_path, pid_path=tmp_path / "recorder.pid", spawn=lambda c: DeadOnArrival()
        ),
        detector=DetectorProcess(
            settings_path, pid_path=tmp_path / "detector.pid", spawn=lambda c: DeadOnArrival()
        ),
        clock=clock,
        now=lambda: 1_000_000.0,
        disk=watching(settings),
    )
    services.start()
    for _ in range(6):
        clock.advance(3.0)
        services.tick()

    state = services.state()
    tail = "in the last 2 minutes"
    assert tail in state["recording_state"]["reason"]
    assert tail in state["detection"]["reason"]


# ------------------------------- a recorder with nothing to record is not held
#
# vmd.record_main prints "no enabled streams; nothing to record" and exits 1, so
# a supervisor holding it respawns that exit for the life of the console.
# Measured on an unconfigured machine: 11 spawns in 30 seconds, which is about
# 43,000 overnight. Detection already had this guard.


def test_a_stream_with_no_address_is_nothing_to_record() -> None:
    """Every case here is about the streams. Playback is ticked on throughout,
    because with it off nothing is recorded whatever the streams say - which is
    the test below this one."""
    assert recordable(Settings()) is False
    assert (
        recordable(
            Settings(
                show_playback=True,
                camera=CameraSettings(
                    streams=[StreamSettings(name="thermal", url="", enabled=True)]
                )
            )
        )
        is False
    )
    assert (
        recordable(
            Settings(
                show_playback=True,
                camera=CameraSettings(
                    streams=[
                        StreamSettings(name="thermal", url="rtsp://x/t", enabled=False)
                    ]
                )
            )
        )
        is False
    )
    assert (
        recordable(
            Settings(
                show_playback=True,
                camera=CameraSettings(
                    streams=[StreamSettings(name="thermal", url="rtsp://x/t", enabled=True)]
                )
            )
        )
        is True
    )


def test_nothing_is_recorded_while_the_playback_tab_is_switched_off() -> None:
    """"When the playback is disabled don't record."

    Playback is the only way to watch recorded footage back, so with it off the
    recorder is filling a disk nobody can open. It is a real decision and it
    costs something real - which is why the console says so calmly rather than
    reporting it as a fault. See `recording_off_on_purpose`.
    """
    from vmd.desktop.services import recording_off_on_purpose

    watched = Settings(
        show_playback=True,
        camera=CameraSettings(
            streams=[StreamSettings(name="thermal", url="rtsp://x/t", enabled=True)]
        ),
    )
    assert recordable(watched) is True
    assert recording_off_on_purpose(watched) is False

    hidden = watched.model_copy(update={"show_playback": False})
    assert recordable(hidden) is False, "it went on recording with nowhere to watch it"
    assert recording_off_on_purpose(hidden) is True


def test_a_recorder_with_nothing_to_record_is_never_respawned(tmp_path: Path) -> None:
    clock = Clock()
    # Playback on, so that the reason below is about there being no stream
    # rather than about the Playback switch - which is a different test.
    settings = Settings(show_playback=True, storage=StorageSettings(root=tmp_path / "rec"))
    spawned: list = []
    settings_path = tmp_path / "settings.json"
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=None,
        recorder=RecorderProcess(
            settings_path,
            pid_path=tmp_path / "recorder.pid",
            spawn=lambda c: (spawned.append(c), DeadOnArrival())[1],
        ),
        clock=clock,
        disk=watching(settings),
    )
    services.start()
    for _ in range(15):
        clock.advance(3.0)
        services.tick()

    assert spawned == [], f"{len(spawned)} recorders started for nothing to record"
    assert "recorder" not in [entry.name for entry in services.supervisor.managed]
    state = services.state()
    assert state["recording"] is False
    assert "no stream" in state["recording_state"]["reason"].lower()


def test_entering_a_stream_and_saving_starts_the_recorder(tmp_path: Path) -> None:
    """The other side of the guard: the operator finishes setting up and the
    recorder must start without the console being restarted."""
    clock = Clock()
    settings = Settings(storage=StorageSettings(root=tmp_path / "rec"))
    settings_path = tmp_path / "settings.json"
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=None,
        recorder=RecorderProcess(
            settings_path, pid_path=tmp_path / "recorder.pid", spawn=lambda c: FakeProcess()
        ),
        clock=clock,
        disk=watching(settings),
    )
    services.start()
    assert services.recorder.running is False

    services.apply(settings_for(tmp_path))

    assert services.recorder.running is True
    assert "recorder" in [entry.name for entry in services.supervisor.managed]


# ---------------------------------- applying a save to a child that was adopted
#
# ChildProcess.stop() deliberately leaves an adopted child alone, so that
# recording survives THE WINDOW CLOSING - a passive event the operator did not
# intend as a configuration change. A Save is the opposite: an explicit
# instruction whose whole purpose is to change how the system runs. A child
# running settings the operator has just replaced is not a child worth
# protecting, and a brief gap is a far smaller harm than a system that silently
# ignores its operator.


class AdoptedChild:
    """A child inherited from an earlier console: a live PID and no process."""

    def __init__(self, tmp_path: Path, cls, pid_name: str, spawn=None):
        self.pid_path = tmp_path / pid_name
        self.pid_path.write_text("4242", encoding="utf-8")
        self.killed: list[int] = []
        self.spawned: list = []
        self.living = {4242}
        self.child = cls(
            tmp_path / "settings.json",
            pid_path=self.pid_path,
            spawn=spawn or (lambda c: (self.spawned.append(c), FakeProcess())[1]),
            kill_tree=self._kill,
            alive=lambda pid: pid in self.living,
        )

    def _kill(self, pid: int) -> bool:
        self.killed.append(pid)
        self.living.discard(pid)
        return True


def test_an_adopted_detector_is_restarted_when_the_settings_it_read_change(
    tmp_path: Path,
) -> None:
    """The QA finding: Save restarted the detector so settings took effect, and
    that restart did nothing at all when the detector had been adopted. The
    operator saw "Saved." and the old configuration kept running."""
    adopted = AdoptedChild(tmp_path, DetectorProcess, "detector.pid")
    adopted.child.start()
    assert adopted.child.running is True
    assert adopted.spawned == [], "it was adopted, not started"

    changed = settings_for(tmp_path, detect=True)
    changed.camera.streams[0].sensitivity = "high"
    assert adopted.child.restart("the movement-detection settings changed") is True

    assert adopted.killed == [4242], "the adopted detector was left running"
    assert len(adopted.spawned) == 1, "no fresh detector was started"
    assert adopted.child.running is True


def test_an_adopted_recorder_is_restarted_the_same_way(tmp_path: Path) -> None:
    adopted = AdoptedChild(tmp_path, RecorderProcess, "recorder.pid")
    adopted.child.start()
    assert adopted.child.restart("the recording settings changed") is True
    assert adopted.killed == [4242]
    assert len(adopted.spawned) == 1


def test_closing_the_window_still_leaves_an_adopted_child_alone(tmp_path: Path) -> None:
    """The other half of the decision, and the reason adoption exists at all."""
    adopted = AdoptedChild(tmp_path, RecorderProcess, "recorder.pid")
    adopted.child.start()
    adopted.child.stop()
    assert adopted.killed == [], "closing a window is not a configuration change"


def test_an_adopted_child_that_will_not_die_is_reported_not_replaced(
    tmp_path: Path,
) -> None:
    """Starting a second recorder on the same directory is the exact collision
    adoption exists to prevent. If the old one will not go, say so."""
    adopted = AdoptedChild(tmp_path, RecorderProcess, "recorder.pid")
    adopted.child.start()
    adopted.child._kill_tree = lambda pid: False  # taskkill refused
    adopted.living = {4242}

    assert adopted.child.restart("the recording settings changed") is False
    assert adopted.spawned == [], "a second recorder must never be started on top"


def test_a_restart_says_which_child_and_why_in_plain_language(
    tmp_path: Path, caplog
) -> None:
    adopted = AdoptedChild(tmp_path, RecorderProcess, "recorder.pid")
    adopted.child.start()
    with caplog.at_level(logging.INFO):
        adopted.child.restart("the recordings folder changed")
    said = " ".join(record.getMessage() for record in caplog.records)
    assert "recorder" in said
    assert "the recordings folder changed" in said
    assert "gap" in said.lower(), "the operator must know the gap was deliberate"


def test_a_restart_that_fails_says_the_saved_settings_are_not_in_effect(
    tmp_path: Path, caplog
) -> None:
    adopted = AdoptedChild(
        tmp_path, RecorderProcess, "recorder.pid", spawn=lambda c: DeadOnArrival()
    )
    adopted.child.start()
    with caplog.at_level(logging.ERROR):
        assert adopted.child.restart("the recording settings changed") is False
    said = " ".join(record.getMessage() for record in caplog.records)
    assert "not in effect" in said.lower() or "not running" in said.lower()


# ------------------------------------------------------------ what is material


def test_material_settings_are_the_ones_each_child_reads_at_startup(
    tmp_path: Path,
) -> None:
    """Which settings force which child to restart, spelled out and tested on
    both sides of the boundary."""
    base = settings_for(tmp_path, detect=True)

    def changed(**edit):
        copy = base.model_copy(deep=True)
        for path, value in edit.items():
            target = copy
            parts = path.split(".")
            for part in parts[:-1]:
                target = getattr(target, part)
            setattr(target, parts[-1], value)
        return copy

    # go2rtc parses its config once: the credentials and each stream's name,
    # address, tick and reader are in that config and nothing else is.
    assert streaming_fingerprint(changed(**{"camera.username": "root"})) != streaming_fingerprint(base)
    assert streaming_fingerprint(changed(**{"camera.host": "10.0.0.9"})) == streaming_fingerprint(base)
    assert streaming_fingerprint(changed(**{"storage.budget_gb": 5.0})) == streaming_fingerprint(base)
    assert streaming_fingerprint(changed(**{"radio.host": "10.0.0.3"})) == streaming_fingerprint(base)

    # The recorder reads the folder, the segment length and every retention rule
    # once, at startup, and the set of streams it is to record.
    assert recorder_fingerprint(changed(**{"storage.root": tmp_path / "moved"})) != recorder_fingerprint(base)
    assert recorder_fingerprint(changed(**{"storage.segment_seconds": 60})) != recorder_fingerprint(base)
    assert recorder_fingerprint(changed(**{"storage.budget_gb": 5.0})) != recorder_fingerprint(base)
    assert recorder_fingerprint(changed(**{"storage.retention_days": 3})) != recorder_fingerprint(base)
    # ...and nothing else. Detection and the radio are not its business, and
    # neither is which client go2rtc uses to read the camera.
    assert recorder_fingerprint(changed(**{"detection.classify": True})) == recorder_fingerprint(base)
    assert recorder_fingerprint(changed(**{"radio.host": "10.0.0.3"})) == recorder_fingerprint(base)
    assert recorder_fingerprint(changed(**{"camera.host": "10.0.0.9"})) == recorder_fingerprint(base)

    # The detector reads sensitivity, the sky line, the ignore mask and the
    # classifier switches once, at startup.
    assert detector_fingerprint(changed(**{"detection.classify": True})) != detector_fingerprint(base)
    assert detector_fingerprint(changed(**{"detection.min_travel_px": 12.0})) != detector_fingerprint(base)
    assert detector_fingerprint(changed(**{"storage.root": tmp_path / "moved"})) != detector_fingerprint(base)
    assert detector_fingerprint(changed(**{"storage.budget_gb": 5.0})) == detector_fingerprint(base)
    assert detector_fingerprint(changed(**{"radio.password": "x"})) == detector_fingerprint(base)

    sensitive = base.model_copy(deep=True)
    sensitive.camera.streams[0].sensitivity = "high"
    assert detector_fingerprint(sensitive) != detector_fingerprint(base)
    assert recorder_fingerprint(sensitive) == recorder_fingerprint(base)
    assert streaming_fingerprint(sensitive) == streaming_fingerprint(base)


def test_a_save_that_changes_nothing_material_restarts_nothing(tmp_path: Path) -> None:
    """Clicking Save twice must not cost two recording gaps."""
    settings = settings_for(tmp_path, detect=True)
    settings_path = tmp_path / "settings.json"
    streaming = RecordingStreaming()
    recorder_spawns: list = []
    detector_spawns: list = []
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=streaming,
        recorder=RecorderProcess(
            settings_path,
            pid_path=tmp_path / "recorder.pid",
            spawn=lambda c: (recorder_spawns.append(c), FakeProcess())[1],
        ),
        detector=DetectorProcess(
            settings_path,
            pid_path=tmp_path / "detector.pid",
            spawn=lambda c: (detector_spawns.append(c), FakeProcess())[1],
        ),
        clock=Clock(),
        now=lambda: 1_000_000.0,
        disk=watching(settings),
    )
    services.start()
    assert len(recorder_spawns) == 1 and len(detector_spawns) == 1

    same = settings_for(tmp_path, detect=True)
    assert services.apply(same) == []
    assert services.apply(same) == []

    assert len(recorder_spawns) == 1, "two Saves cost two recording gaps"
    assert len(detector_spawns) == 1, "the detector was restarted for nothing"
    assert streaming.applied == [], "go2rtc was restarted for nothing"


def test_a_save_that_moves_the_recordings_folder_restarts_the_recorder(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path, detect=True)
    settings_path = tmp_path / "settings.json"
    recorder_spawns: list = []
    detector_spawns: list = []
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=RecordingStreaming(),
        recorder=RecorderProcess(
            settings_path,
            pid_path=tmp_path / "recorder.pid",
            spawn=lambda c: (recorder_spawns.append(c), FakeProcess())[1],
        ),
        detector=DetectorProcess(
            settings_path,
            pid_path=tmp_path / "detector.pid",
            spawn=lambda c: (detector_spawns.append(c), FakeProcess())[1],
        ),
        clock=Clock(),
        now=lambda: 1_000_000.0,
        disk=watching(settings),
    )
    services.start()

    moved = settings_for(tmp_path, detect=True)
    moved.storage.root = tmp_path / "elsewhere"
    assert services.apply(moved) == []

    assert len(recorder_spawns) == 2, "the recorder kept writing to the old folder"
    assert len(detector_spawns) == 2, "the detector kept writing events to the old folder"


def test_a_save_that_only_changes_the_radio_leaves_every_child_alone(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path, detect=True)
    settings_path = tmp_path / "settings.json"
    streaming = RecordingStreaming()
    spawns: list = []
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=streaming,
        recorder=RecorderProcess(
            settings_path,
            pid_path=tmp_path / "recorder.pid",
            spawn=lambda c: (spawns.append(c), FakeProcess())[1],
        ),
        detector=DetectorProcess(
            settings_path,
            pid_path=tmp_path / "detector.pid",
            spawn=lambda c: (spawns.append(c), FakeProcess())[1],
        ),
        clock=Clock(),
        now=lambda: 1_000_000.0,
        disk=watching(settings),
    )
    services.start()
    assert len(spawns) == 2

    changed = settings_for(tmp_path, detect=True)
    changed.radio.host = "10.0.0.3"
    assert services.apply(changed) == []

    assert len(spawns) == 2
    assert streaming.applied == []
    assert services.settings is changed


def test_a_deliberate_restart_is_not_read_as_flapping(tmp_path: Path) -> None:
    """Four Saves in two minutes is an operator setting the system up, not a
    recorder that will not stay up."""
    clock = Clock()
    settings = settings_for(tmp_path)
    settings_path = tmp_path / "settings.json"
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=None,
        recorder=RecorderProcess(
            settings_path, pid_path=tmp_path / "recorder.pid", spawn=lambda c: FakeProcess()
        ),
        clock=clock,
        disk=watching(settings),
    )
    services.start()
    services.tick()

    for seconds in (60, 120, 180, 240):
        moved = settings_for(tmp_path)
        moved.storage.segment_seconds = seconds
        services.apply(moved)
        services.tick()

    state = services.state()
    assert state["recording_state"]["restarts"] == 0, "a Save is not a death"
    assert state["recording"] is True


def test_a_restart_that_fails_is_reported_back_to_the_save(tmp_path: Path) -> None:
    """The console must say so rather than reporting the new settings as live."""
    settings = settings_for(tmp_path)
    settings_path = tmp_path / "settings.json"
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=None,
        recorder=RecorderProcess(
            settings_path,
            pid_path=tmp_path / "recorder.pid",
            spawn=lambda c: FakeProcess(),
        ),
        clock=Clock(),
        disk=watching(settings),
    )
    services.start()
    services.recorder._spawn = lambda c: DeadOnArrival()  # it will not come back

    moved = settings_for(tmp_path)
    moved.storage.root = tmp_path / "elsewhere"
    problems = services.apply(moved)

    assert problems, "a failed restart reported as success is the worst answer"
    assert any("recorder" in problem for problem in problems)


def test_the_recorders_in_flight_segment_is_indexed_after_a_forced_restart(
    tmp_path: Path,
) -> None:
    """Verified rather than assumed, because the answer is not the obvious one.

    Killing the recorder tree leaves the segment ffmpeg had open on disk.
    `_adopt_orphans` does NOT cover it: it deliberately skips directories
    belonging to a currently configured recorder. What covers it is the next
    recorder's ordinary indexing pass, once that recorder has written a newer
    file beside it - so the file is not lost, but it is not indexed at the
    moment of the restart either.
    """
    from vmd.record_main import RecordingService

    settings = settings_for(tmp_path)
    directory = settings.storage.root / "thermal"
    directory.mkdir(parents=True, exist_ok=True)
    now = time.time()

    closed = directory / "2026-08-11_10-00-00.mp4"
    closed.write_bytes(b"\0" * 4096)
    os.utime(closed, (now - 600, now - 600))
    # What ffmpeg had open when taskkill /T reached it.
    in_flight = directory / "2026-08-11_10-05-00.mp4"
    in_flight.write_bytes(b"\0" * 2048)
    os.utime(in_flight, (now - 300, now - 300))

    service = RecordingService(
        settings, spawn=lambda *a, **k: FakeProcess(), settle_seconds=1.0
    )
    try:
        adopted = {Path(s.path).name for s in service.index.all()}
        assert in_flight.name not in adopted, (
            "if this ever passes, _adopt_orphans has changed and this note is stale"
        )

        # The fresh recorder starts writing.
        fresh = directory / "2026-08-11_10-10-00.mp4"
        fresh.write_bytes(b"\0" * 512)
        os.utime(fresh, (now, now))
        service.run_once(now=now)

        indexed = {Path(s.path).name for s in service.index.all()}
        assert in_flight.name in indexed, "the segment lost to the restart is unindexed"
        assert closed.name in indexed
    finally:
        service.stop()


# ------------------------------------------------------ adopting the right process
#
# `_pid_alive` only asks whether SOMETHING holds that PID. After a power cut -
# the event this always-on laptop will certainly see - recorder.pid survives on
# disk holding a dead PID, and Windows reuses PIDs from a small pool. The
# console then logged "a recorder is already running; adopting it", reported
# recording, and never started a recorder at all: nothing was written and the
# status line was green.


def test_a_pid_reused_by_something_else_is_not_adopted(tmp_path: Path) -> None:
    """The PID is alive - it is this test process - and it is not a recorder."""
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    (tmp_path / "recorder.pid.started").write_text(
        json.dumps({"pid": os.getpid(), "started_at": 1000.0}), encoding="utf-8"
    )

    spawned: list = []
    recorder = RecorderProcess(
        tmp_path / "settings.json",
        pid_path=pid_file,
        spawn=lambda c: (spawned.append(c), FakeProcess())[1],
    )
    recorder.start()

    assert len(spawned) == 1, "a stale PID file left the machine recording nothing"
    assert recorder.running is True


def test_the_process_that_really_is_ours_is_still_adopted(tmp_path: Path) -> None:
    """The other half: a child that genuinely survived the last console must not
    be duplicated, because two of them fight over the same files and index."""
    started_at = process_started_at(os.getpid())
    assert started_at is not None, "this platform cannot answer the question at all"
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    (tmp_path / "recorder.pid.started").write_text(
        json.dumps({"pid": os.getpid(), "started_at": started_at}), encoding="utf-8"
    )

    spawned: list = []
    recorder = RecorderProcess(
        tmp_path / "settings.json",
        pid_path=pid_file,
        spawn=lambda c: (spawned.append(c), FakeProcess())[1],
    )
    recorder.start()

    assert spawned == [], "the live child should have been adopted"
    assert recorder.running is True


def test_a_pid_file_from_an_older_console_is_adopted_but_said_to_be_unverified(
    tmp_path: Path, caplog
) -> None:
    """A bare number is what previous versions wrote. There is nothing to check
    it against, so it is adopted as before - and said to be unchecked, because
    the alternative is starting a second recorder on the same directory."""
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    spawned: list = []
    recorder = RecorderProcess(
        tmp_path / "settings.json",
        pid_path=pid_file,
        spawn=lambda c: (spawned.append(c), FakeProcess())[1],
    )
    with caplog.at_level(logging.WARNING):
        recorder.start()

    assert spawned == []
    said = " ".join(record.getMessage() for record in caplog.records)
    assert "could not be checked" in said or "not be verified" in said


def test_the_pid_file_records_when_the_process_started(tmp_path: Path) -> None:
    """The detector's, because it is the console that claims that one.

    The recorder claims its own file now - see the storm at the end of this
    file - so the only child the console still writes a PID for is this one.
    """

    class RealEnough:
        pid = os.getpid()

        def poll(self):
            return None

    detector = DetectorProcess(
        tmp_path / "settings.json",
        pid_path=tmp_path / "detector.pid",
        spawn=lambda c: RealEnough(),
    )
    detector.start()

    written = json.loads((tmp_path / "detector.pid.started").read_text(encoding="utf-8"))
    assert written["pid"] == os.getpid()
    assert written["started_at"] == process_started_at(os.getpid())


def test_when_a_process_started_can_be_read_for_this_process() -> None:
    started_at = process_started_at(os.getpid())
    assert started_at is not None
    assert 0 < started_at <= time.time() + 1


def test_asking_when_a_process_that_is_not_there_started_is_not_an_exception() -> None:
    assert process_started_at(999_999) is None


def test_the_claim_file_stays_a_bare_integer(tmp_path: Path) -> None:
    """Three programs read this file and two of them parse it as one number:
    `vmd.record_main.read_pid` does int(text.strip()) and
    scripts\\recorder_service.ps1 does [int]::TryParse over the whole file.
    Either of them failing to parse reads as "no recorder is running", and the
    answer to that is to start a second recorder on the same directory. So
    whatever else the console records about a child goes beside the file, never
    in it. Shown on the detector's, which is the one the console still writes;
    the recorder claims its own, and `vmd.record_main` keeps the same rule."""
    from vmd.record_main import read_pid

    class RealEnough:
        pid = os.getpid()

        def poll(self):
            return None

    pid_file = tmp_path / "detector.pid"
    detector = DetectorProcess(
        tmp_path / "settings.json", pid_path=pid_file, spawn=lambda c: RealEnough()
    )
    detector.start()

    assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())
    assert read_pid(pid_file) == os.getpid()


def test_the_start_time_does_not_collide_with_the_recorders_own_companion(
    tmp_path: Path,
) -> None:
    """`vmd.record_main` keeps what it knows about itself in
    `recorder.pid.json`, and the console now writes nothing there at all: not
    the claim, not a note beside it. It cannot - the number it has is its
    launcher's, not the recorder's - and the storm at the end of this file is
    what writing one anyway cost.

    The detector is the child the console does still claim, and the rule that
    the note lives beside the file rather than in it is shown on that one.
    """
    from vmd.record_main import identity_path

    class RealEnough:
        pid = os.getpid()

        def poll(self):
            return None

    pid_file = tmp_path / "recorder.pid"
    recorder = RecorderProcess(
        tmp_path / "settings.json", pid_path=pid_file, spawn=lambda c: RealEnough()
    )
    recorder.start()

    assert not identity_path(pid_file).exists(), "the console wrote the recorder's file"
    assert not pid_file.exists(), "the console claimed the recorder's file for it"
    assert not (tmp_path / "recorder.pid.started").exists()

    detector_pid = tmp_path / "detector.pid"
    detector = DetectorProcess(
        tmp_path / "settings.json", pid_path=detector_pid, spawn=lambda c: RealEnough()
    )
    detector.start()

    assert not identity_path(detector_pid).exists()
    written = json.loads((tmp_path / "detector.pid.started").read_text(encoding="utf-8"))
    assert written["pid"] == os.getpid()
    assert written["started_at"] == process_started_at(os.getpid())


def test_a_start_time_left_over_from_an_earlier_child_is_ignored(tmp_path: Path) -> None:
    """The recorder claims the file itself now, so the number in it can have
    been written by a process the console never spawned. A note about a
    different PID says nothing about this one."""
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    (tmp_path / "recorder.pid.started").write_text(
        json.dumps({"pid": 999_999, "started_at": 1000.0}), encoding="utf-8"
    )

    spawned: list = []
    recorder = RecorderProcess(
        tmp_path / "settings.json",
        pid_path=pid_file,
        spawn=lambda c: (spawned.append(c), FakeProcess())[1],
    )
    recorder.start()

    assert spawned == [], "an unrelated note must read as unverified, not as wrong"


# -------------------------------------- asking about an adopted child's PID
#
# A child adopted from an earlier console is a PID and nothing else, and on
# Windows the only way to ask about a PID is to shell out to `tasklist` - about
# 150 ms. That was paid on the GUI thread, on every heartbeat, for every adopted
# child, via `running` -> `state()` -> the status line. Every wait below is
# bounded independently of the code under test, so a regression fails rather
# than hangs.

LIVENESS_PATIENCE = 10.0
LIVENESS_CEILING = 5.0


def _tasklist(monkeypatch, stdout: str, seen: dict | None = None):
    """Stand in for the one command `_pid_alive` runs, on the platform it runs on."""
    monkeypatch.setattr(os, "name", "nt")

    def run(command, **kwargs):
        if seen is not None:
            seen["command"] = list(command)
            seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", run)


def test_asking_whether_a_pid_is_alive_cannot_wait_for_ever(monkeypatch) -> None:
    """`tasklist` was run with no timeout at all.

    It is run from a `BackgroundValue` reader thread, and that reader is the
    only thing that ever notices an adopted recorder has died. A `tasklist` that
    wedges - and the case it is asked about is a machine in trouble - takes the
    reader with it for good: `close` gives up after two seconds and abandons the
    thread, and nothing starts another. The console then reports the adopted
    recorder as running for the rest of the day on the strength of one answer
    from the morning.

    Every other caller of `tasklist` in this codebase passes a timeout. This one
    did not, and it is the one on the path that decides whether a recorder is
    started at all.
    """
    seen: dict = {}
    _tasklist(monkeypatch, '"recorder.exe","4242","Services","0","12,345 K"\n', seen)

    assert _pid_alive(4242) is True
    assert seen.get("timeout"), "a tasklist that wedges wedges the reader for ever"
    # And no console window flashing over the pictures every time it is asked,
    # which is what the two other callers of tasklist already avoid.
    assert seen.get("creationflags"), "the check flashes a console window at the operator"


def test_a_pid_is_looked_for_as_a_pid_and_not_as_digits_somewhere_in_the_answer(
    monkeypatch,
) -> None:
    """`str(pid) in result.stdout` proves something adjacent to what matters.

    `tasklist`'s answer is a table and every column in it is numbers: another
    process's PID, a session number, "12,345 K" of memory. Those digits appearing
    anywhere in the output was taken as proof that the process is alive - and
    this is the check that decides whether the console adopts a recorder it
    believes is already running or starts one, so being wrong means either two
    recorders on one directory or none at all.

    The answer below is a table the filter did not narrow, which is what makes
    the difference between the two readings visible: 45 is in "12,345 K" and is
    not a PID in it.
    """
    listing = (
        '"System Idle Process","0","Services","0","8 K"\n'
        '"recorder.exe","1234","Services","0","12,345 K"\n'
    )
    _tasklist(monkeypatch, listing)

    assert _pid_alive(1234) is True, "the PID that really is in the answer"
    assert _pid_alive(45) is False, "digits inside a memory figure are not a process"
    assert _pid_alive(8) is False, "digits inside a memory figure are not a process"


def wait_until(predicate, timeout: float = LIVENESS_PATIENCE) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class WedgedLiveness:
    """A `tasklist` that answers once and then stops coming back.

    Once, because adoption checks the PID synchronously before it will adopt at
    all - a machine where that never answered would never adopt anything, which
    is a different test. What is being measured is the heartbeat afterwards.
    """

    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.calls = 0
        self.entered = threading.Event()
        self.released = threading.Event()

    def __call__(self, pid: int) -> bool:
        self.calls += 1
        if self.calls == 1:
            return self.answer
        self.entered.set()
        self.released.wait(LIVENESS_CEILING)
        return self.answer


def adopted_recorder(tmp_path: Path, alive) -> RecorderProcess:
    pid_path = tmp_path / "recorder.pid"
    pid_path.write_text("4242", encoding="utf-8")
    return RecorderProcess(
        tmp_path / "settings.json",
        pid_path=pid_path,
        spawn=lambda command: FakeProcess(),
        kill_tree=lambda pid: True,
        alive=alive,
    )


def test_asking_whether_an_adopted_child_is_there_does_not_block(
    tmp_path: Path, monkeypatch
) -> None:
    # Every call asks again, so nothing here is answered out of a cache that
    # happens to be fresh - the point is that asking costs the caller nothing.
    monkeypatch.setattr("vmd.desktop.services.LIVENESS_SECONDS", 0.0)
    wedged = WedgedLiveness()
    recorder = adopted_recorder(tmp_path, wedged)
    recorder.start()
    assert recorder.running is True, "the live process should be adopted"
    try:
        started = time.monotonic()
        for _ in range(20):  # forty seconds of heartbeats
            assert recorder.running is True
        elapsed = time.monotonic() - started
    finally:
        wedged.released.set()
        recorder.stop()
    assert elapsed < 0.5, f"twenty heartbeats cost {elapsed:.2f} s asking about a PID"


def test_only_one_question_about_a_pid_is_ever_outstanding(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("vmd.desktop.services.LIVENESS_SECONDS", 0.0)
    wedged = WedgedLiveness()
    recorder = adopted_recorder(tmp_path, wedged)
    recorder.start()
    try:
        for _ in range(20):
            assert recorder.running is True
        assert wedged.entered.wait(LIVENESS_PATIENCE)
        time.sleep(0.2)
        assert wedged.calls == 2, (
            f"{wedged.calls - 1} process listings were started at once"
        )
    finally:
        wedged.released.set()
        recorder.stop()


def test_an_adopted_child_that_has_gone_is_noticed(tmp_path: Path, monkeypatch) -> None:
    """Off-thread must not mean never: a recorder that died has to be restarted."""
    monkeypatch.setattr("vmd.desktop.services.LIVENESS_SECONDS", 0.05)
    living = {4242}
    recorder = adopted_recorder(tmp_path, lambda pid: pid in living)
    recorder.start()
    assert recorder.running is True
    living.clear()
    assert wait_until(lambda: recorder.running is False), "a dead child was never noticed"


def test_a_pid_check_that_never_answers_is_not_reported_as_recording(
    tmp_path: Path, monkeypatch
) -> None:
    """The last answer, repeated for ever, is the console inventing health."""
    monkeypatch.setattr("vmd.desktop.services.LIVENESS_SECONDS", 0.0)
    monkeypatch.setattr("vmd.desktop.services.LIVENESS_UNANSWERED_SECONDS", 0.2)
    wedged = WedgedLiveness()
    recorder = adopted_recorder(tmp_path, wedged)
    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=tmp_path / "settings.json",
        streaming=None,
        recorder=recorder,
    )
    services.start()
    try:
        assert wait_until(
            lambda: "not been answered" in services.recording_state()["reason"]
        ), services.recording_state()["reason"]
    finally:
        wedged.released.set()
        recorder.stop()


def test_a_pid_that_cannot_be_asked_about_is_read_as_still_there(
    tmp_path: Path, monkeypatch
) -> None:
    """The two ways of being wrong are not symmetrical: believing a live
    recorder is gone starts a second one on the same directory and the same
    index, which is the collision adoption exists to prevent."""
    monkeypatch.setattr("vmd.desktop.services.LIVENESS_SECONDS", 0.0)
    calls = {"n": 0}

    def unanswerable(pid: int) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            return True
        raise OSError("the process list could not be read")

    recorder = adopted_recorder(tmp_path, unanswerable)
    recorder.start()

    def asked_again() -> bool:
        recorder.running  # asking is what starts the next read  # noqa: B018
        return calls["n"] > 1

    assert wait_until(asked_again)
    assert recorder.running is True


# ------------------------------- a streaming server this console did not start
#
# `adopted_streaming` was set from streaming.json, which carries ports and no
# PID, and Go2rtcService.stop() only ever stopped a process object it held. So
# a settings change left the adopted go2rtc running and started a SECOND one on
# another port - a second connection across the radio link, which is the one
# cost this whole architecture exists to avoid.


def live_endpoint(tmp_path: Path, streams: list[str] | None = None, serves=None):
    """A streaming server that answers, so streaming.json can be believed.

    Two ports and not one, because a streaming server is two listeners and the
    console now asks both: the API for what it knows about, and the RTSP port
    for the picture itself. `FakeGo2rtc` is the streaming module's own fake -
    the same one its tests adopt and refuse - so this file cannot drift into
    testing against a go2rtc that could not exist.
    """
    # Pointed where `settings_for` points, because a server pointed somewhere
    # else is not adoptable any more and must not be: that is a console adopting
    # a go2rtc holding an address the operator has since corrected.
    server = FakeGo2rtc(
        {name: "rtsp://10.0.0.2/t" for name in (["thermal"] if streams is None else streams)},
        serves=serves,
    )
    (tmp_path / "streaming.json").write_text(
        json.dumps(server.endpoint), encoding="utf-8"
    )
    return server, server.endpoint["api_port"]


def go2rtc_claim(tmp_path: Path, pid: int = 4242) -> None:
    (tmp_path / "go2rtc.pid").write_text(str(pid), encoding="utf-8")
    (tmp_path / "go2rtc.pid.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "executable": str(tmp_path / "go2rtc.exe"),
                "api_port": 1984,
                "rtsp_port": 8554,
                "written_at": time.time(),
            }
        ),
        encoding="utf-8",
    )


def test_an_adopted_streaming_server_is_stopped_when_the_settings_it_read_change(
    tmp_path: Path,
) -> None:
    """The defect, end to end: one go2rtc before the save and one after it."""
    from vmd.streaming.go2rtc import Go2rtcService

    listener, port = live_endpoint(tmp_path)
    go2rtc_claim(tmp_path)
    killed: list[int] = []
    living = {4242}
    spawned: list = []

    streaming = Go2rtcService(
        settings_for(tmp_path),
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        endpoint_path=tmp_path / "streaming.json",
        pid_path=tmp_path / "go2rtc.pid",
        spawn=lambda command: (spawned.append(command), FakeProcess())[1],
        image_of=lambda p: "go2rtc.exe" if p in living else None,
        kill_tree=lambda p: (killed.append(p), living.discard(p), True)[2],
        booted=lambda: None,
    )
    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=tmp_path / "settings.json",
        streaming=streaming,
        recorder=RecorderProcess(tmp_path / "settings.json", spawn=lambda c: FakeProcess()),
    )
    try:
        services.start()
        assert services.adopted_streaming is True
        assert spawned == [], "a live streaming server must not be duplicated"

        # And the tick that follows must not start one either.
        services.tick()
        assert spawned == []

        changed = settings_for(tmp_path)
        changed.camera.streams = [
            StreamSettings(name="visible", url="rtsp://camera/visible", enabled=True)
        ]
        problems = services.apply(changed)
    finally:
        listener.close()

    assert killed == [4242], "the adopted streaming server was left running"
    assert len(spawned) == 1, "a second go2rtc was started beside the first"
    assert services.adopted_streaming is False
    assert problems == []


def test_a_streaming_server_nobody_can_name_is_reported_rather_than_duplicated(
    tmp_path: Path,
) -> None:
    """No claim on disk - a go2rtc left by a console older than this. It cannot
    be stopped, and a second one on the same camera is worse than saying so."""
    from vmd.streaming.go2rtc import Go2rtcService

    listener, port = live_endpoint(tmp_path)
    spawned: list = []
    streaming = Go2rtcService(
        settings_for(tmp_path),
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        endpoint_path=tmp_path / "streaming.json",
        pid_path=tmp_path / "go2rtc.pid",
        spawn=lambda command: (spawned.append(command), FakeProcess())[1],
        image_of=lambda p: None,
        kill_tree=lambda p: True,
        booted=lambda: None,
    )
    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=tmp_path / "settings.json",
        streaming=streaming,
        recorder=RecorderProcess(tmp_path / "settings.json", spawn=lambda c: FakeProcess()),
    )
    try:
        services.start()
        changed = settings_for(tmp_path)
        changed.camera.streams = [
            StreamSettings(name="visible", url="rtsp://camera/visible", enabled=True)
        ]
        problems = services.apply(changed)
    finally:
        listener.close()

    assert spawned == [], "a second go2rtc was started on top of a live one"
    assert any("could not be stopped" in problem for problem in problems), problems


# ------------------------------------------------- the recorder respawn storm
#
# From the operator's Logs tab, with the repeats removed:
#
#   10:45:50 INFO  recorder started
#   10:45:51 INFO  recorder: a recorder is already running (pid 2712); leaving
#                  it alone rather than recording the same folder twice
#   10:45:53 INFO  recorder started
#   10:45:54 INFO  recorder: a recorder is already running (pid 55704); ...
#
# every two seconds for as long as the console stayed open - sixteen recorder
# processes in thirty seconds, and a different pid named every single time.
#
# The reason is that the console cannot see the process it starts. It spawns
# `sys.executable -u -m vmd.record_main`, and `sys.executable` in the
# environment uv builds is .venv\Scripts\python.exe, which is a trampoline: it
# runs the real interpreter as a CHILD of itself. `Popen.pid` is therefore the
# trampoline's number and `os.getpid()` inside the recorder is a different one.
# Measured on this machine: Popen.pid 50468, the recorder's own pid 49500.
#
# The console wrote the trampoline's number into recorder.pid. The recorder read
# it, found a live process behind it - its own launcher - running python.exe,
# and correctly concluded that another recorder already held the claim. So it
# stood down and exited; the trampoline exited with it; the console saw a dead
# child and started another one, for ever, and nothing was ever recorded.
#
# The claim belongs to the process that is recording, because it is the only one
# that knows its own number. See the comment above PID_FILENAME in
# vmd\record_main.py, which says exactly that and was already true.


class Launcher(FakeProcess):
    """What Popen hands back for a trampoline: a pid that is not the child's."""

    pid = 4242


def test_the_console_does_not_claim_the_recorder_s_pid_for_it(tmp_path: Path) -> None:
    """The storm, reproduced across the two files that make it.

    The console starts a recorder and the recorder claims the file, using
    `vmd.record_main`'s own claim code - and it must not find the console
    standing in the way of it, wearing a number the console guessed.
    """
    from vmd.record_main import RecorderIdentity, claim_recorder, read_pid

    recorder_pid = Launcher.pid + 1  # the interpreter behind the trampoline
    pid_file = tmp_path / "recorder.pid"
    child = RecorderProcess(
        tmp_path / "settings.json", pid_path=pid_file, spawn=lambda c: Launcher()
    )
    child.start()

    holder = claim_recorder(
        pid_file,
        RecorderIdentity(
            pid=recorder_pid, executable="python.exe", written_at=time.time()
        ),
        image_of=lambda pid: "python.exe" if pid in (Launcher.pid, recorder_pid) else None,
        booted=lambda: 0.0,
    )

    assert holder is None, (
        "the recorder stood down to the launcher that started it, so it recorded "
        "nothing and the console started another one two seconds later"
    )
    assert read_pid(pid_file) == recorder_pid, "the claim must name the recorder"


class StoodDown:
    """A recorder that found another one holding the claim and exited saying so."""

    pid = 4242

    def poll(self):
        from vmd.record_main import ALREADY_RECORDING_EXIT

        return ALREADY_RECORDING_EXIT

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout=None):
        return self.poll()


def test_a_recorder_that_stood_down_is_adopted_rather_than_started_again(
    tmp_path: Path,
) -> None:
    """The exit that says "somebody else is recording" is a success.

    Read as a death it is the whole storm: the console starts another recorder,
    which stands down as well, for ever. What it means is that the claim file
    now names the recorder that is really running, and that one is what to
    adopt.
    """
    holder = 4243
    pid_file = tmp_path / "recorder.pid"
    spawned: list = []

    def spawn(command):
        spawned.append(command)
        # The recorder that holds the claim wrote this. The one we just started
        # read it, said so and exited.
        pid_file.write_text(str(holder), encoding="utf-8")
        (tmp_path / "recorder.pid.json").write_text(
            json.dumps(
                {"pid": holder, "executable": "python.exe", "written_at": time.time()}
            ),
            encoding="utf-8",
        )
        return StoodDown()

    child = RecorderProcess(
        tmp_path / "settings.json",
        pid_path=pid_file,
        spawn=spawn,
        alive=lambda pid: pid == holder,
    )
    with console_log_buffer() as buffer:
        child.start()
        assert child.running is False, "the child we started is gone"

        child.start()  # the next heartbeat
        said = " ".join(child_lines(buffer, "recorder")).lower()

    assert child.running is True, "the recorder that is recording must be adopted"
    assert len(spawned) == 1, "a second recorder was started on the same folder"
    assert "stood down" in said, (
        "the Logs tab must say that this was the recorder deferring to the one "
        "that is recording, not a recorder dying: they look identical otherwise"
    )


def test_a_recorder_that_exits_at_once_over_and_over_stops_being_started_again(
    tmp_path: Path,
) -> None:
    """The backstop, which the detector has had and the recorder has not.

    Whatever the reason - an unwritable folder, a claim that will not clear, a
    fault nobody has thought of - a child that dies as soon as it is started
    must stop being started, and be reported as not running. Thirty spawns a
    minute is not recording; it is a Logs tab in which nothing else survives.
    The clock is hand-wound, so this cannot hang however the policy is broken.
    """
    clock = Clock()
    spawned: list = []
    services = recording_console(
        tmp_path,
        settings_for(tmp_path),
        spawn=lambda c: (spawned.append(c), DeadOnArrival())[1],
        clock=clock,
    )
    services.start()
    for _ in range(30):
        clock.advance(3.0)  # past the supervisor's restart delay
        services.tick()

    assert len(spawned) <= SPAWN_LIMIT, (
        f"{len(spawned)} recorders in a minute and a half: the console never "
        "stopped trying"
    )
    state = services.state()
    assert state["recording"] is False
    assert "not being started again" in state["recording_state"]["reason"].lower()


def test_a_recorder_that_is_healthy_is_still_started_after_a_quiet_spell(
    tmp_path: Path,
) -> None:
    """The other half: giving up must not be permanent.

    A recorder that failed at breakfast and would work now has to be tried
    again, or the backstop is just a slower way of not recording.
    """
    clock = Clock()
    spawned: list = []
    services = recording_console(
        tmp_path,
        settings_for(tmp_path),
        spawn=lambda c: (spawned.append(c), DeadOnArrival())[1],
        clock=clock,
    )
    services.start()
    for _ in range(20):
        clock.advance(3.0)
        services.tick()
    held = len(spawned)

    clock.advance(FLAP_WINDOW + 1.0)  # a quiet spell longer than the window
    services.tick()

    assert len(spawned) > held, "it must be tried again once the flapping is history"


# ------------------------------- a streaming server adopted without evidence
#
# The operator's Logs tab, in this order, inside two seconds:
#
#   a streaming server is already running; adopting it
#   showing rtsp://127.0.0.1:8554/thermal
#   live555 demux error: Failed to connect with rtsp://127.0.0.1:8554/thermal
#
# streaming.json records ports and no process, so adoption was trusting a file
# a previous run left behind. Whether anything is listening, and whether what is
# listening serves the streams this settings file asks for, are questions with
# real answers - go2rtc has an API on the loopback - and adoption now waits for
# them.


class GhostStreaming:
    """A go2rtc from an earlier run that cannot be shown to be serving anything."""

    api_port = 1984
    rtsp_port = 8554
    adopted = False

    def __init__(self, why: str = "") -> None:
        self.why = why
        self.replaced: list[str] = []
        self.started = 0

    def unadoptable(self, endpoint: dict) -> str:
        return self.why

    def adopt(self, endpoint: dict) -> bool:
        self.adopted = True
        return True

    def replace(self, why: str) -> None:
        self.replaced.append(why)

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None: ...

    @property
    def running(self) -> bool:
        return True


def test_a_streaming_server_that_is_not_serving_these_streams_is_replaced(
    tmp_path: Path,
) -> None:
    """No picture at all is the worst state this console can be in, and a file
    left behind by a previous run must not be able to put it there silently."""
    listener, _port = live_endpoint(tmp_path)
    streaming = GhostStreaming(why="it is serving door, not thermal")
    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=tmp_path / "settings.json",
        streaming=streaming,
        recorder=RecorderProcess(tmp_path / "settings.json", spawn=lambda c: FakeProcess()),
    )
    try:
        with console_log_buffer() as buffer:
            services.start()
            told = " ".join(child_lines(buffer, "go2rtc")).lower()
    finally:
        listener.close()

    assert streaming.adopted is False, "a server nothing can vouch for was adopted"
    assert streaming.replaced == ["it is serving door, not thermal"], (
        "the console must hand on the reason, so what the operator is told is "
        "what was actually found: " + told
    )
    assert services.adopted_streaming is False


def test_a_streaming_server_that_is_serving_these_streams_is_still_adopted(
    tmp_path: Path,
) -> None:
    """The other half, and the reason adoption exists: closing the console must
    not stop the recording that go2rtc is feeding."""
    listener, _port = live_endpoint(tmp_path)
    streaming = GhostStreaming(why="")
    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=tmp_path / "settings.json",
        streaming=streaming,
        recorder=RecorderProcess(tmp_path / "settings.json", spawn=lambda c: FakeProcess()),
    )
    try:
        services.start()
    finally:
        listener.close()

    assert streaming.adopted is True
    assert streaming.replaced == []
    assert streaming.started == 0, "a live streaming server must not be duplicated"
    assert services.adopted_streaming is True


# --------------------------------------- the answer the recording light reads
#
# `state()["recording"]` drives an indicator the operator reads from across the
# room, on a timer, for months. Two things are therefore required of it: that it
# means footage is reaching the disk rather than a process having been alive
# when it was asked - which is what recording_state() is for - and that asking
# costs nothing, because the thread that asks is the thread that draws.


def test_asking_whether_it_is_recording_never_waits_for_the_operating_system(
    tmp_path: Path,
) -> None:
    """An adopted recorder is a PID, and asking about a PID on Windows means
    shelling out to `tasklist` - about 150 ms. Paid per heartbeat that is the
    window stuttering; paid per call it is a window that does not repaint.

    The bound here is deliberately far looser than the thing it is measuring:
    a `tasklist` per call would be twenty seconds, and this fails at one.
    """
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text("4242", encoding="utf-8")
    settings = settings_for(tmp_path)
    recorder = RecorderProcess(
        tmp_path / "settings.json",
        pid_path=pid_file,
        spawn=lambda c: FakeProcess(),
        alive=lambda pid: (time.sleep(0.1), True)[1],  # a slow tasklist
    )
    services = ConsoleServices(
        settings=settings,
        settings_path=tmp_path / "settings.json",
        streaming=None,
        recorder=recorder,
        disk=watching(settings),
    )
    services.start()
    assert recorder.running is True, "it should have adopted the live pid"

    started = time.monotonic()
    for _ in range(200):
        assert services.state()["recording"] is True
    spent = time.monotonic() - started

    assert spent < 1.0, f"200 answers took {spent:.1f}s: the window would not repaint"


def test_a_folder_of_empty_files_is_not_recording(tmp_path: Path) -> None:
    """What the deployment laptop actually had: 24 files, all zero bytes.

    ffmpeg was creating a segment, failing to write a header because the camera
    sends audio MP4 cannot carry, and exiting - every five seconds, for a day.
    A file that exists is not footage, and the indicator the operator reads from
    across the room must not go green for one.
    """
    settings = settings_for(tmp_path)
    directory = Path(settings.storage.root) / "thermal"
    for path in directory.glob("*.mp4"):
        path.unlink()
    for name in ("2026-08-11_08-24-36.mp4", "2026-08-11_08-24-41.mp4"):
        (directory / name).write_bytes(b"")

    services = recording_console(tmp_path, settings)
    services.start()
    services.tick()

    state = services.state()
    assert services.recorder.running is True, "the process is alive; that is the trap"
    assert state["recording"] is False
    assert "not recording" in state["recording_state"]["reason"].lower()


# --------------------------------- "recording" against a folder that proves it
#
# `recording_state` skipped the footage check entirely when the disk had never
# been polled, and answered "recording". So the FIRST reading after startup -
# the one the operator sees while he is standing there wondering whether the
# thing works - was the one that could not fail, against a storage root of
# `Q:/never-existed`.
#
# It survived because thirteen `ConsoleServices(...)` sites in this file omitted
# `disk=`, so every `assert state["recording"] is True` in the suite proved only
# that a process object existed. These drive a real, polled DiskWatcher.


def polled(settings: Settings, now: float | None = None) -> DiskWatcher:
    """A watcher that has actually read the folder, at a stated moment."""
    at = time.time() if now is None else now
    watcher = DiskWatcher(
        settings, executor=lambda work: work(), clock=Clock(), now=lambda: at
    )
    watcher.poll()
    return watcher


def recording_services(tmp_path: Path, settings: Settings, disk) -> ConsoleServices:
    recorder = RecorderProcess(tmp_path / "settings.json", spawn=lambda c: FakeProcess())
    recorder.start()
    return ConsoleServices(
        settings=settings,
        settings_path=tmp_path / "settings.json",
        streaming=None,
        recorder=recorder,
        disk=disk,
    )


def test_a_live_recorder_over_a_folder_that_cannot_prove_it_is_not_recording(
    tmp_path: Path, monkeypatch
) -> None:
    """Four folders, one live recorder process, and not one of them is footage.

    A process is not footage. Each of these has to say `running is False` and
    has to name what is wrong, because the operator has this line and nothing
    else.
    """
    now = time.time()

    # 1. the date on this machine was typed wrong and stepped back an hour
    stepped = settings_for(tmp_path / "stepped")
    services = recording_services(tmp_path, stepped, polled(stepped, now=now - 3600.0))
    state = services.recording_state()
    assert state["running"] is False, state
    assert "date on this machine is wrong" in state["reason"], state

    # 2. a folder of files too small to hold any video
    headers = tmp_path / "headers"
    (headers / "thermal").mkdir(parents=True)
    for n in range(10):
        one = headers / "thermal" / f"2026-08-11_10-{n:02d}-00.mp4"
        one.write_bytes(b"\0" * 900)
        os.utime(one, (now - 5, now - 5))
    small = _rooted(tmp_path / "headers-settings", headers)
    services = recording_services(tmp_path, small, polled(small, now=now))
    state = services.recording_state()
    assert state["running"] is False, state
    assert "nothing has ever been written" in state["reason"], state

    # 3. a storage root that does not exist
    missing = _rooted(tmp_path / "missing-settings", tmp_path / "never-existed")
    services = recording_services(tmp_path, missing, polled(missing, now=now))
    state = services.recording_state()
    assert state["running"] is False, state
    assert "not there" in state["reason"], state

    # 4. a stream folder this machine may not read
    refused = settings_for(tmp_path / "refused")
    real_scandir = os.scandir

    def scandir(path=".", *args, **kwargs):
        if str(path).endswith("thermal"):
            raise PermissionError(13, "Access is denied")
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", scandir)
    services = recording_services(tmp_path, refused, polled(refused, now=now))
    state = services.recording_state()
    monkeypatch.undo()
    assert state["running"] is False, state
    assert "nothing has ever been written" in state["reason"], state


def test_a_folder_nobody_has_read_yet_is_not_reported_as_recording(
    tmp_path: Path
) -> None:
    """The first answer after startup, which is the one he is looking at."""
    settings = _rooted(tmp_path / "unread", Path("Q:/never-existed"))
    recorder = RecorderProcess(tmp_path / "settings.json", spawn=lambda c: FakeProcess())
    recorder.start()
    services = ConsoleServices(
        settings=settings,
        settings_path=tmp_path / "settings.json",
        streaming=None,
        recorder=recorder,
        disk=DiskWatcher(settings, executor=lambda work: work(), clock=Clock()),
    )
    state = services.recording_state()
    assert state["running"] is False, state
    assert "has not been read yet" in state["reason"], state
    assert services.state()["recording"] is False


def test_a_folder_that_really_is_filling_still_reads_as_recording(
    tmp_path: Path
) -> None:
    """The other half. A guard that answered "not recording" to everything would
    be exactly as useless as one that answered "recording"."""
    settings = settings_for(tmp_path / "good")
    services = recording_services(tmp_path, settings, polled(settings))
    state = services.recording_state()
    assert state["running"] is True, state
    assert state["reason"] == "recording"


def _rooted(where: Path, root: Path) -> Settings:
    """Settings for a recorder that is configured, pointed at this folder."""
    where.mkdir(parents=True, exist_ok=True)
    return Settings(
        show_playback=True,
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(name="thermal", url="rtsp://10.0.0.2/t", enabled=True)
            ],
        ),
        storage=StorageSettings(root=root),
    )


# ------------------------------------- a stream crossing the radio link twice
#
# The founding constraint of this whole system is that the camera is pulled
# exactly once across a >15 km, ~5 Mb/s link. Three components can each fall
# back to the camera's own address when the local server does not look right,
# and the detector's fallback is sticky: one go2rtc restart - which the console
# itself performs on every material settings change - can move detection onto a
# second direct connection for the life of the process.
#
# Both children now publish which side of the link each stream is coming from.
# Until this, that fact reached the operator as one logger.warning in a ring of
# five hundred lines, which is evicted in minutes: the one failure that can
# quietly halve his bandwidth for months was announced where nobody would see
# it.


class Pulling:
    """A streaming server that is up, and therefore holding the camera itself."""

    api_port = 1984
    rtsp_port = 8554

    def __init__(self, up: bool = True) -> None:
        self.up = up

    def unadoptable(self, endpoint: dict) -> str:
        return ""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    @property
    def running(self) -> bool:
        return self.up


def write_recording_status(
    tmp_path: Path,
    streams: list[dict],
    written_at: float = 1_000_000.0,
    interval: float = 5.0,
) -> Path:
    from vmd.desktop.services import RECORDING_STATUS_FILENAME

    path = tmp_path / "rec" / RECORDING_STATUS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"streams": streams, "written_at": written_at, "interval": interval}),
        encoding="utf-8",
    )
    return path


def console_with_streaming(tmp_path: Path, streaming, now=None):
    settings_path = tmp_path / "settings.json"
    settings = settings_for(tmp_path, detect=True)
    return ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=streaming,
        recorder=RecorderProcess(
            settings_path, pid_path=tmp_path / "recorder.pid", spawn=lambda c: FakeProcess()
        ),
        detector=DetectorProcess(
            settings_path, pid_path=tmp_path / "detector.pid", spawn=lambda c: FakeProcess()
        ),
        now=now or (lambda: 1_000_002.0),
        disk=watching(settings),
        executor=lambda work: work(),
    )


def test_a_detector_reading_from_the_camera_is_a_stream_carried_twice(
    tmp_path: Path,
) -> None:
    """go2rtc is up, so it is holding its own connection to that camera. The
    detector falling back to the camera's own address is the second one."""
    write_detection_status(
        tmp_path,
        [{"stream": "thermal", "opened": True, "reason": "detecting", "source": "camera"}],
    )
    services = console_with_streaming(tmp_path, Pulling())
    assert services.link_doubled() == ["thermal"]


def test_a_recorder_reading_from_the_camera_is_a_stream_carried_twice(
    tmp_path: Path,
) -> None:
    """The recorder's own file, read the same way. The two processes spell the
    stream's name differently and mean the same thing by it."""
    write_recording_status(
        tmp_path, [{"name": "thermal", "running": True, "source": "camera"}]
    )
    services = console_with_streaming(tmp_path, Pulling())
    assert services.link_doubled() == ["thermal"]


def test_streams_read_through_the_local_server_are_not_reported(tmp_path: Path) -> None:
    """The ordinary, healthy arrangement: one connection crosses the link and
    everything on this machine reads that copy. Saying anything about it would
    teach the operator to ignore the chip that one day says something true."""
    write_detection_status(
        tmp_path,
        [{"stream": "thermal", "opened": True, "reason": "detecting", "source": "local"}],
    )
    write_recording_status(
        tmp_path, [{"name": "thermal", "running": True, "source": "local"}]
    )
    services = console_with_streaming(tmp_path, Pulling())
    assert services.link_doubled() == []


def test_one_child_on_the_camera_with_no_local_server_is_not_called_twice(
    tmp_path: Path,
) -> None:
    """A stream with no streaming server behind it is read from the camera on
    purpose, and that is one copy, not two. A console that called it doubled
    would be crying wolf about the ordinary state of a machine with go2rtc off."""
    write_detection_status(
        tmp_path,
        [{"stream": "thermal", "opened": True, "reason": "detecting", "source": "camera"}],
    )
    services = console_with_streaming(tmp_path, Pulling(up=False))
    assert services.link_doubled() == []


def test_both_children_on_the_camera_is_twice_with_no_server_at_all(
    tmp_path: Path,
) -> None:
    """Two direct connections to the same camera is two crossings of the link,
    whatever the streaming server is doing."""
    write_detection_status(
        tmp_path,
        [{"stream": "thermal", "opened": True, "reason": "detecting", "source": "camera"}],
    )
    write_recording_status(
        tmp_path, [{"name": "thermal", "running": True, "source": "camera"}]
    )
    services = console_with_streaming(tmp_path, Pulling(up=False))
    assert services.link_doubled() == ["thermal"]


def test_a_report_nobody_has_written_for_an_hour_says_nothing(tmp_path: Path) -> None:
    """Stale is unknown, not healthy and not a fault. A detector that wedged an
    hour ago left a file saying it was on the camera; repeating that as the
    state of the link now is the console inventing news."""
    write_detection_status(
        tmp_path,
        [{"stream": "thermal", "opened": True, "reason": "detecting", "source": "camera"}],
        written_at=1_000_000.0,
    )
    services = console_with_streaming(tmp_path, Pulling(), now=lambda: 1_003_600.0)
    assert services.link_doubled() == []


def test_a_missing_recorder_report_costs_the_recorders_half_and_no_more(
    tmp_path: Path,
) -> None:
    """The recorder may not be publishing yet, or at all. What the detector says
    still has to reach the band."""
    write_detection_status(
        tmp_path,
        [{"stream": "thermal", "opened": True, "reason": "detecting", "source": "camera"}],
    )
    services = console_with_streaming(tmp_path, Pulling())
    assert services.link_doubled() == ["thermal"]


def test_both_children_naming_the_same_stream_name_it_once(tmp_path: Path) -> None:
    write_detection_status(
        tmp_path,
        [
            {"stream": "thermal", "opened": True, "source": "camera"},
            {"stream": "visible", "opened": True, "source": "camera"},
        ],
    )
    write_recording_status(
        tmp_path,
        [
            {"name": "thermal", "running": True, "source": "camera"},
            {"name": "visible", "running": True, "source": "camera"},
        ],
    )
    services = console_with_streaming(tmp_path, Pulling())
    assert services.link_doubled() == ["thermal", "visible"]


def test_the_state_the_window_reads_carries_it(tmp_path: Path) -> None:
    write_detection_status(
        tmp_path,
        [{"stream": "thermal", "opened": True, "reason": "detecting", "source": "camera"}],
    )

    class Answering(Pulling):
        def status(self):
            from vmd.streaming.go2rtc import StreamingStatus

            return StreamingStatus(True, "streaming", "http://127.0.0.1:1984", [], False)

    services = console_with_streaming(tmp_path, Answering())
    assert services.state()["on_camera"] == ["thermal"]


def test_a_moved_recordings_folder_moves_the_recorders_report_with_it(
    tmp_path: Path,
) -> None:
    """Left pointing at the old folder it would read a file nobody writes any
    more, and go on reporting last week's link for ever."""
    from vmd.desktop.services import RECORDING_STATUS_FILENAME

    services = console_with_streaming(tmp_path, Pulling())
    moved = settings_for(tmp_path, detect=True)
    moved.storage.root = str(tmp_path / "elsewhere")
    services.apply(moved)
    assert (
        services.recording_status_path == tmp_path / "elsewhere" / RECORDING_STATUS_FILENAME
    )


# ----------------------------------------- two children, one camera connection
#
# The operator killed go2rtc.exe, opened the console again, and got exactly the
# same failure from a server that had never seen the mistyped password. That
# rules out the config: a FRESH go2rtc, built from the settings on disk, also
# could not serve either stream.
#
# What was in the log all along is the line above it - `a recorder is already
# running (pid 11152); adopting it`. The logon task starts the recorder first,
# it finds no streaming server, and it reads STRAIGHT FROM THE CAMERA. Many IP
# cameras hand out only a small number of RTSP sessions at once; the recorder
# takes one and holds it for as long as it records, and every go2rtc after it is
# refused BY THE CAMERA. Killing go2rtc cannot fix that, and neither can
# correcting the password, which is why every round of this failed the same way.
#
# It is a deadlock, and the resource is the camera. The recorder moves to this
# machine's copy as soon as there is one - that part is already built - but
# there can never be one while the recorder holds the only connection the camera
# will give. Something has to let go, and only the console can see both sides.


class Blind(Pulling):
    """A streaming server that is up and has no picture in it.

    `held_by` is the thing that is holding the camera, if anything: while it
    answers true this server can get nothing, and the moment it lets go the
    pictures arrive. That is the camera's session limit, modelled at the only
    point where its effect is visible to this console.
    """

    def __init__(self, why: str = "streams: wrong user/pass", held_by=None) -> None:
        super().__init__(True)
        self.why = why
        self.held_by = held_by
        self.asked = 0

    def without_a_picture(self, names=None, rtsp_port=None, api_port=None, timeout=None):
        self.asked += 1
        if self.held_by is not None and not self.held_by():
            return {}
        return {"thermal": self.why}


def recording_from_the_camera(tmp_path: Path) -> None:
    write_recording_status(
        tmp_path, [{"name": "thermal", "running": True, "source": "camera"}]
    )


def said_in(caplog) -> str:
    return " ".join(record.getMessage() for record in caplog.records)


def test_the_console_says_which_of_its_children_is_holding_the_camera(
    tmp_path: Path, caplog
) -> None:
    """`Failed to setup RTSP session` is what the operator was left with, for
    two days. The console knows both halves of this - that go2rtc cannot pull,
    and that the recorder it adopted is on the camera - and there is exactly one
    plain-language explanation of that pair."""
    recording_from_the_camera(tmp_path)
    services = console_with_streaming(tmp_path, Blind())
    services.handover_seconds = 0.5

    with caplog.at_level(logging.WARNING):
        services.start()

    said = said_in(caplog).lower()
    assert "recorder" in said, said
    assert "camera" in said, said
    assert "wrong user/pass" in said, "go2rtc's own words are the useful part"


def test_the_recorder_stands_down_so_the_streaming_server_can_take_the_camera(
    tmp_path: Path, caplog
) -> None:
    """Breaking the deadlock, and the direction it must be broken in: the
    recorder is the one holding the resource, and it is the one that can get it
    back through the local server afterwards. go2rtc cannot - it has nowhere
    else to read from."""
    recording_from_the_camera(tmp_path)
    streaming = Blind()
    services = console_with_streaming(tmp_path, streaming)
    # The camera gives one session: while the recorder holds it, nothing else
    # can have it.
    streaming.held_by = lambda: services.recorder.running
    services.handover_seconds = 3.0

    with caplog.at_level(logging.WARNING):
        services.start()

    assert services.recorder.running, "footage must never be what this costs"
    assert streaming.asked >= 2, "it has to look again after the recorder let go"
    said = said_in(caplog).lower()
    assert "recording" in said, said


def test_recording_comes_back_even_when_the_camera_was_not_the_problem(
    tmp_path: Path, caplog
) -> None:
    """A streaming server that still cannot pull with the recorder stood down
    has some other fault, and the console has just stopped recording for it.
    Footage matters more than the live picture: it comes back either way, and
    the console says which of the two it turned out to be."""
    recording_from_the_camera(tmp_path)
    services = console_with_streaming(tmp_path, Blind())
    services.handover_seconds = 0.5

    with caplog.at_level(logging.WARNING):
        services.start()

    assert services.recorder.running
    said = said_in(caplog).lower()
    assert "recorder" in said and "not" in said


def test_a_recorder_on_the_local_server_is_not_stood_down(tmp_path: Path, caplog) -> None:
    """No contention: the recorder is reading this machine's copy, so whatever
    is wrong with the streaming server, stopping the recorder cannot fix it -
    and a recording gap that fixes nothing is footage thrown away."""
    write_recording_status(
        tmp_path, [{"name": "thermal", "running": True, "source": "local"}]
    )
    streaming = Blind(why="streams: 401 Unauthorized")
    services = console_with_streaming(tmp_path, streaming)
    services.handover_seconds = 0.5
    with caplog.at_level(logging.WARNING):
        services.start()

    assert services.recorder.running
    assert streaming.asked == 1, (
        "the recorder was stood down for something it was not part of: a second "
        "look means the console waited for a handover it had no reason to make"
    )
    assert "401 Unauthorized" in said_in(caplog), (
        "the reason must still reach the Logs tab even when nobody is to blame"
    )


def test_a_streaming_server_with_a_picture_in_it_costs_nothing(
    tmp_path: Path, caplog
) -> None:
    """The ordinary case, which runs at every console start in front of a blank
    window: nothing is stopped, nothing is waited for, and nothing is said."""
    recording_from_the_camera(tmp_path)

    class Serving(Blind):
        def without_a_picture(self, names=None, rtsp_port=None, api_port=None, timeout=None):
            self.asked += 1
            return {}

    streaming = Serving()
    services = console_with_streaming(tmp_path, streaming)
    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        services.start()
    took = time.monotonic() - started

    assert streaming.asked == 1
    assert took < 2.0, f"a healthy start waited {took:.1f}s"
    assert "holding the camera" not in said_in(caplog)


def test_a_save_that_leaves_the_camera_held_is_settled_too(tmp_path: Path, caplog) -> None:
    """A Save restarts the streaming server, and a streaming server restarted
    while the recorder is on the camera is refused by the camera exactly as it
    is at start-up. The operator who has just corrected the password would
    otherwise be left with the same blank panes and nothing said."""
    recording_from_the_camera(tmp_path)

    class Restartable(Blind):
        def apply(self, settings) -> None:
            self.applied = True

    streaming = Restartable()
    services = console_with_streaming(tmp_path, streaming)
    services.handover_seconds = 0.5
    services.start()
    caplog.clear()

    changed = settings_for(tmp_path, detect=True)
    changed.camera.password = "corrected"
    with caplog.at_level(logging.WARNING):
        services.apply(changed)

    said = said_in(caplog).lower()
    assert "camera" in said and "recorder" in said, said


# ------------------------------------------ matching the picture to the link
#
# The loop itself is `tests/test_autobitrate.py`. These are about where it runs:
# on the console's own heartbeat, off the thread that draws the window, and only
# when there is both a camera to write to and a radio to read.


class Radio:
    def __init__(self, reading: dict) -> None:
        self.reading = reading
        self.asked = 0

    def status(self) -> dict:
        self.asked += 1
        return dict(self.reading)


class Ptz:
    def __init__(self) -> None:
        self.fitted: list[int] = []

    def fit_encoders_to_link(self, ceiling_kbps: int) -> dict:
        self.fitted.append(ceiling_kbps)
        return {"ok": True, "changed": [], "applied": {}, "refused": []}


def console_with_a_link(tmp_path: Path, reading: dict, clock=None):
    settings_path = tmp_path / "settings.json"
    settings = settings_for(tmp_path)
    radio, ptz = Radio(reading), Ptz()
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=None,
        recorder=RecorderProcess(
            settings_path, pid_path=tmp_path / "recorder.pid", spawn=lambda c: FakeProcess()
        ),
        disk=watching(settings),
        clock=clock or Clock(),
        # On the caller's thread: no test in this suite waits on a worker.
        executor=lambda work: work(),
        ptz=ptz,
        radio=radio,
    )
    return services, radio, ptz


def test_the_heartbeat_is_what_drives_the_bitrate_loop(tmp_path: Path) -> None:
    """One heartbeat for the whole console. A loop with a timer of its own is a
    second thing to stop when the window closes and a second thing to forget."""
    clock = Clock()
    services, radio, ptz = console_with_a_link(
        tmp_path,
        {
            "connected": True,
            "airtime_percent": 92.0,
            "signal_dbm": -66.0,
            "age_seconds": 1.0,
        },
        clock=clock,
    )

    for _ in range(20):
        services.tick()
        clock.advance(2.0)

    assert radio.asked >= 5, "the loop reads the link the console already has"
    assert ptz.fitted, "a link that busy for that long should have been acted on"


def test_a_console_with_no_radio_never_touches_the_camera(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings = settings_for(tmp_path)
    ptz = Ptz()
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=None,
        recorder=RecorderProcess(
            settings_path, pid_path=tmp_path / "recorder.pid", spawn=lambda c: FakeProcess()
        ),
        disk=watching(settings),
        executor=lambda work: work(),
        ptz=ptz,
        radio=None,
    )

    for _ in range(20):
        services.tick()

    assert ptz.fitted == []
    assert services.state()["link_bitrate"]["running"] is False


def test_the_link_loop_is_in_what_the_console_reports(tmp_path: Path) -> None:
    """The operator has no terminal. Whether this is running at all has to be
    something the console can be asked, not something only the log knows."""
    services, _, _ = console_with_a_link(
        tmp_path, {"connected": False, "reason": "the radio is not set up"}
    )
    services.tick()

    reported = services.state()["link_bitrate"]
    assert reported["running"] is False
    assert "radio" in reported["reason"]


def test_saving_the_switch_off_reaches_the_loop_without_a_restart(tmp_path: Path) -> None:
    clock = Clock()
    services, _, ptz = console_with_a_link(
        tmp_path,
        {
            "connected": True,
            "airtime_percent": 92.0,
            "signal_dbm": -66.0,
            "age_seconds": 1.0,
        },
        clock=clock,
    )
    changed = settings_for(tmp_path)
    changed.bitrate.mode = "manual"
    services.apply(changed)

    for _ in range(20):
        services.tick()
        clock.advance(2.0)

    assert ptz.fitted == []


def test_a_bad_link_reading_cannot_take_the_heartbeat_down(tmp_path: Path) -> None:
    """The heartbeat restarts the children. Nothing hung off it may raise."""

    class Broken:
        def status(self) -> dict:
            raise RuntimeError("the radio reader fell over")

    settings_path = tmp_path / "settings.json"
    settings = settings_for(tmp_path)
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=None,
        recorder=RecorderProcess(
            settings_path, pid_path=tmp_path / "recorder.pid", spawn=lambda c: FakeProcess()
        ),
        disk=watching(settings),
        executor=lambda work: work(),
        ptz=Ptz(),
        radio=Broken(),
    )

    services.tick()  # must not raise
    assert services.state()["link_bitrate"]["running"] is False



# --------------------------------------- a console that has not been set up yet
#
# "After the cameras.bat and the autostart-on.bat I ran the VMD.exe and got
# errors."
#
# `cameras.bat` makes a camera folder with no stream addresses in it - the
# operator types those into the Settings tab next. In the meantime
# `Go2rtcService.start` sensibly refuses to spawn a server with nothing in its
# configuration, the supervisor counts each refusal as a start, and after three
# it reports an escalating failure every two seconds for as long as the console
# is open. It is the first impression a new installation makes and it is not
# true.


def test_a_console_with_no_stream_addresses_does_not_supervise_the_streamer() -> None:
    from vmd.desktop.services import streamable

    nothing = Settings(camera=CameraSettings(streams=[]))
    assert streamable(nothing) is False

    typed = Settings(
        camera=CameraSettings(
            streams=[StreamSettings(name="thermal", url="", enabled=True)]
        )
    )
    assert streamable(typed) is False, "a stream with no address is nothing to serve"

    ready = Settings(
        camera=CameraSettings(
            streams=[StreamSettings(name="thermal", url="rtsp://x/t", enabled=True)]
        )
    )
    assert streamable(ready) is True


def test_nothing_is_reported_as_flapping_before_the_addresses_are_typed(
    tmp_path: Path,
) -> None:
    """The whole fault, at the console's own level: fifteen heartbeats on a
    freshly made camera folder must produce no start attempts and no failure."""
    clock = Clock()
    settings = Settings(
        show_playback=True,
        camera=CameraSettings(host="10.0.0.2", streams=[]),
        storage=StorageSettings(root=tmp_path / "rec"),
    )
    started: list = []

    class CountingStreamer:
        settings = None

        def start(self) -> None:
            started.append(1)

        def stop(self) -> None: ...

        def status(self):
            from types import SimpleNamespace

            return SimpleNamespace(running=False, reason="no enabled streams")

        @property
        def running(self) -> bool:
            return False

    services = ConsoleServices(
        settings=settings,
        settings_path=tmp_path / "settings.json",
        streaming=CountingStreamer(),
        recorder=RecorderProcess(
            tmp_path / "settings.json",
            pid_path=tmp_path / "recorder.pid",
            spawn=lambda c: DeadOnArrival(),
        ),
        clock=clock,
        disk=watching(settings),
    )
    services.start()
    for _ in range(15):
        clock.advance(2.0)
        services.tick()

    assert started == [], f"{len(started)} attempts to start a server with nothing to serve"
    assert "streaming" not in [entry.name for entry in services.supervisor.managed]
    # And it is reported as off rather than as broken: `_streaming_state` in the
    # window draws exactly this word quietly.
    assert services.state()["streaming"] == "not enabled"


def test_typing_the_addresses_in_starts_the_streamer(tmp_path: Path) -> None:
    """The other half, and the one that matters on the first Save a new
    installation ever gets: the supervisor has to be rebuilt at that moment or
    the pictures never start."""
    clock = Clock()
    settings = Settings(
        show_playback=True,
        camera=CameraSettings(host="10.0.0.2", streams=[]),
        storage=StorageSettings(root=tmp_path / "rec"),
    )

    class Streamer:
        def __init__(self) -> None:
            self.settings = None
            self.starts = 0

        def start(self) -> None:
            self.starts += 1

        def stop(self) -> None: ...

        def status(self):
            from types import SimpleNamespace

            return SimpleNamespace(running=True, reason="streaming")

        @property
        def running(self) -> bool:
            return self.starts > 0

    streamer = Streamer()
    services = ConsoleServices(
        settings=settings,
        settings_path=tmp_path / "settings.json",
        streaming=streamer,
        recorder=RecorderProcess(
            tmp_path / "settings.json",
            pid_path=tmp_path / "recorder.pid",
            spawn=lambda c: DeadOnArrival(),
        ),
        clock=clock,
        disk=watching(settings),
    )
    services.start()
    assert "streaming" not in [entry.name for entry in services.supervisor.managed]

    filled = settings.model_copy(
        update={
            "camera": CameraSettings(
                host="10.0.0.2",
                streams=[StreamSettings(name="thermal", url="rtsp://x/t", enabled=True)],
            )
        }
    )
    services.apply(filled)
    assert "streaming" in [entry.name for entry in services.supervisor.managed], (
        "the addresses were typed in and the pictures never started"
    )
