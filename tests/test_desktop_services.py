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

from vmd.desktop.logs import LogBuffer, attach
from vmd.desktop.services import (
    DETECTION_STATUS_FILENAME,
    ConsoleServices,
    DetectorProcess,
    RecorderProcess,
    _creation_flags,
    _default_spawn,
    _taskkill_tree,
    read_detection_status,
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
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(
                    name="thermal", url="rtsp://10.0.0.2/t", enabled=True, detect=detect
                )
            ],
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
    return ConsoleServices(
        settings=settings_for(tmp_path, detect=detect),
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
