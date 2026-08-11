import subprocess

import pytest

from vmd.storage import recorder as recorder_module
from vmd.storage.recorder import SegmentRecorder


class FakeProcess:
    def __init__(self, exit_codes=None):
        self._exit_codes = list(exit_codes or [])
        self.terminated = False

    def poll(self):
        return self._exit_codes.pop(0) if self._exit_codes else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def build(tmp_path, url="rtsp://example/stream", processes=None):
    spawned = []

    def spawn(command, log_path=None):
        process = (processes or []).pop(0) if processes else FakeProcess()
        spawned.append(command)
        return process

    recorder = SegmentRecorder(
        stream="thermal",
        source_url=url,
        output_dir=tmp_path / "thermal",
        segment_seconds=300,
        spawn=spawn,
    )
    return recorder, spawned


def test_command_copies_without_reencoding(tmp_path):
    """Copied, never re-encoded: this laptop cannot transcode four streams, and
    the picture the camera sends is the picture worth keeping. The codec is
    named per stream now - see the audio section at the end of this file."""
    recorder, _ = build(tmp_path)
    command = recorder.build_command()
    assert command[command.index("-c:v") + 1] == "copy"
    assert "libx264" not in command


def test_command_uses_rtsp_over_tcp_for_rtsp_urls(tmp_path):
    recorder, _ = build(tmp_path, url="rtsp://example/stream")
    command = recorder.build_command()
    assert "-rtsp_transport" in command
    assert command[command.index("-rtsp_transport") + 1] == "tcp"


def test_command_omits_rtsp_options_for_file_sources(tmp_path):
    recorder, _ = build(tmp_path, url=str(tmp_path / "clip.mp4"))
    assert "-rtsp_transport" not in recorder.build_command()


def test_command_sets_segment_duration_and_naming(tmp_path):
    recorder, _ = build(tmp_path)
    command = recorder.build_command()
    assert command[command.index("-segment_time") + 1] == "300"
    assert command[command.index("-f") + 1] == "segment"
    assert command[-1].endswith("%Y-%m-%d_%H-%M-%S.mp4")


def test_start_creates_output_directory(tmp_path):
    recorder, _ = build(tmp_path)
    recorder.start()
    assert (tmp_path / "thermal").is_dir()


def test_start_spawns_the_process(tmp_path):
    recorder, spawned = build(tmp_path)
    recorder.start()
    assert len(spawned) == 1
    assert recorder.running is True


def test_running_is_false_after_the_process_exits(tmp_path):
    recorder, _ = build(tmp_path, processes=[FakeProcess(exit_codes=[1])])
    recorder.start()
    assert recorder.running is False


def test_stop_terminates_the_process(tmp_path):
    process = FakeProcess()
    recorder, _ = build(tmp_path, processes=[process])
    recorder.start()
    recorder.stop()
    assert process.terminated is True
    assert recorder.running is False


def test_starting_twice_does_not_spawn_twice(tmp_path):
    recorder, spawned = build(tmp_path)
    recorder.start()
    recorder.start()
    assert len(spawned) == 1


def test_running_is_false_before_start(tmp_path):
    recorder, _ = build(tmp_path)
    assert recorder.running is False


def test_default_spawn_pins_the_timezone_to_utc_and_avoids_a_stderr_pipe(monkeypatch, tmp_path):
    from vmd.storage import recorder as recorder_module

    captured = {}

    def fake_popen(command, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(recorder_module.subprocess, "Popen", fake_popen)
    recorder_module._default_spawn(["ffmpeg", "-version"], tmp_path / "x.ffmpeg.log")
    assert captured["env"]["TZ"] == "UTC"
    assert captured["stderr"] is not recorder_module.subprocess.PIPE


def test_stop_kills_a_process_that_ignores_terminate(tmp_path):
    class Stubborn(FakeProcess):
        def __init__(self):
            super().__init__()
            self.killed = False
            self._waits = 0

        def wait(self, timeout=None):
            self._waits += 1
            if self._waits == 1:
                raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
            return 0

        def kill(self):
            self.killed = True

    process = Stubborn()
    recorder, _ = build(tmp_path, processes=[process])
    recorder.start()
    recorder.stop()
    assert process.killed is True
    assert recorder.running is False


def test_a_process_that_survives_kill_keeps_running_true(tmp_path):
    class Immortal(FakeProcess):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)

        def kill(self):
            pass

    recorder, _ = build(tmp_path, processes=[Immortal()])
    recorder.start()
    recorder.stop()
    # It could not be killed, so the handle is deliberately kept: reporting False here
    # would let the supervisor start a second ffmpeg into the same directory.
    assert recorder.running is True


def test_exit_code_is_captured(tmp_path):
    recorder, _ = build(tmp_path, processes=[FakeProcess(exit_codes=[3])])
    recorder.start()
    assert recorder.running is False
    assert recorder.exit_code == 3


def test_log_path_sits_beside_the_segment_directory(tmp_path):
    recorder, _ = build(tmp_path)
    assert recorder.log_path == tmp_path / "thermal.ffmpeg.log"
    assert recorder.log_path.parent == recorder.output_dir.parent


def test_log_file_is_truncated_not_appended(monkeypatch, tmp_path):
    from vmd.storage import recorder as recorder_module

    log = tmp_path / "old.ffmpeg.log"
    log.write_bytes(b"stale output from a previous run\n")
    monkeypatch.setattr(recorder_module.subprocess, "Popen", lambda command, **kw: FakeProcess())
    recorder_module._default_spawn(["ffmpeg", "-version"], log)
    # A restarted recorder must not keep growing the same file forever.
    assert log.read_bytes() == b""


def test_stop_keeps_running_true_when_kill_raises_oserror(tmp_path):
    class Hostile(FakeProcess):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)

        def kill(self):
            raise OSError("access denied")

    recorder, _ = build(tmp_path, processes=[Hostile()])
    recorder.start()
    recorder.stop()
    # Death could not be confirmed, so the handle is kept deliberately.
    assert recorder.running is True


def test_ffmpeg_is_found_where_the_install_instructions_put_it(tmp_path, monkeypatch):
    """INSTALL.md says to copy ffmpeg.exe into C:\\VMD\\bin\\ for the offline
    machine, exactly as go2rtc lives there - and nothing looked. The recorder
    ran the bare name and let PATH resolve it, so following the instructions as
    written meant recording never started, with nothing on screen saying why.
    """
    from vmd.storage.recorder import find_tool

    monkeypatch.setattr(recorder_module.shutil, "which", lambda name: None)
    bundled = tmp_path / "bin" / "ffmpeg.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")

    assert find_tool("ffmpeg", project_root=tmp_path) == str(bundled)


def test_the_bundled_copy_wins_over_one_on_the_path(tmp_path, monkeypatch):
    """The bundled copy is the version that was carried over and tested."""
    from vmd.storage.recorder import find_tool

    monkeypatch.setattr(
        recorder_module.shutil, "which", lambda name: r"C:\somewhere\else\ffmpeg.exe"
    )
    bundled = tmp_path / "bin" / "ffmpeg.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")

    assert find_tool("ffmpeg", project_root=tmp_path) == str(bundled)


def test_ffmpeg_on_the_path_still_works(tmp_path, monkeypatch):
    """How it resolves on a development machine, and it must keep resolving."""
    from vmd.storage.recorder import find_tool

    monkeypatch.setattr(recorder_module.shutil, "which", lambda name: r"C:\tools\ffmpeg.exe")
    assert find_tool("ffmpeg", project_root=tmp_path) == r"C:\tools\ffmpeg.exe"


def test_a_missing_ffmpeg_still_fails_by_its_own_name(tmp_path, monkeypatch):
    """Nowhere at all: the bare name, so the spawn fails saying "ffmpeg"."""
    from vmd.storage.recorder import find_tool

    monkeypatch.setattr(recorder_module.shutil, "which", lambda name: None)
    assert find_tool("ffmpeg", project_root=tmp_path) == "ffmpeg"


def test_the_recorder_runs_the_binary_it_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder_module, "find_ffmpeg", lambda: r"C:\VMD\bin\ffmpeg.exe")
    recorder = SegmentRecorder("thermal", "rtsp://cam/t", tmp_path)
    assert recorder.build_command()[0] == r"C:\VMD\bin\ffmpeg.exe"


# ------------------------------------------------- the audio nobody listens to
#
# From the deployment laptop, in recordings\thermal.ffmpeg.log, after a day of a
# console that said it was recording:
#
#   [mp4 @ ...] Could not find tag for codec pcm_mulaw in stream #1, codec not
#       currently supported in container
#   [out#0/segment @ ...] Could not write header (incorrect codec parameters ?):
#       Invalid argument
#
# and 24 files of zero bytes, one every five seconds - the supervision interval,
# not the segment length. The camera sends pcm_mulaw audio, MP4 cannot carry it,
# and `-c copy` copied everything the source offered. Nothing on this machine
# has ever listened to that audio: the video panes pass --no-audio to libVLC
# with the note "never listened to: one less decode, one less failure", and the
# recorder should have held the same position.


def test_the_command_records_no_audio(tmp_path):
    """The whole of the defect, in one flag."""
    recorder, _ = build(tmp_path)
    command = recorder.build_command()
    assert "-an" in command, "audio is why recording produced nothing for a day"


def test_the_command_says_which_stream_it_copies(tmp_path):
    """Explicit, so the next thing the camera offers that MP4 cannot hold is a
    message about a stream that was asked for rather than a silent loop."""
    recorder, _ = build(tmp_path)
    command = recorder.build_command()
    assert "-map" in command
    assert command[command.index("-map") + 1] == "0:v:0"
    assert command[command.index("-c:v") + 1] == "copy"
    assert "libx264" not in command


def test_what_ffmpeg_said_can_be_read_back_line_by_line(tmp_path):
    """Its stderr goes to a file - a pipe nobody reads fills and wedges it - and
    that file reached nobody. The one explanation of a total failure sat on the
    laptop all day while the console said "recording"."""
    recorder, _ = build(tmp_path)
    recorder.log_path.parent.mkdir(parents=True, exist_ok=True)
    recorder.log_path.write_bytes(b"Could not write header\nInvalid argument\n")

    assert recorder.new_log_lines() == ["Could not write header", "Invalid argument"]
    assert recorder.new_log_lines() == [], "the same lines must not be said twice"

    recorder.log_path.write_bytes(b"a fresh run\n")  # truncated by the next start
    assert recorder.new_log_lines() == ["a fresh run"]


class Clock:
    """A hand-wound clock, so no test here waits for real seconds."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class DeadOnArrival:
    """An ffmpeg that exits before it has written anything."""

    def poll(self):
        return 1

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout=None):
        return 1


def test_an_ffmpeg_that_dies_on_startup_stops_being_restarted(tmp_path):
    """Restarting something 24 times in four minutes while it fails identically
    every time is not supervision. The same rule the detector has had."""
    clock = Clock()
    spawned = []

    def spawn(command, log_path=None):
        spawned.append(command)
        return DeadOnArrival()

    recorder = SegmentRecorder(
        stream="thermal",
        source_url="rtsp://example/stream",
        output_dir=tmp_path / "thermal",
        segment_seconds=300,
        spawn=spawn,
        clock=clock,
    )
    for _ in range(20):
        clock.advance(5.0)  # the recording service's own pass interval
        if not recorder.running:
            recorder.start()

    assert len(spawned) <= recorder_module.RESTART_LIMIT, (
        f"{len(spawned)} ffmpegs in {20 * 5} seconds, every one of them dead on "
        "arrival"
    )
    assert recorder.held_back is True
    assert recorder.running is False

    # And giving up is not permanent: whatever is wrong may be fixed while
    # nobody is watching, and a camera that starts working again must record.
    clock.advance(recorder_module.RESTART_WINDOW_SECONDS + 1.0)
    recorder.start()
    assert len(spawned) == recorder_module.RESTART_LIMIT + 1


def test_an_ffmpeg_that_ran_for_a_while_is_always_restarted(tmp_path):
    """The other half, and the common case on a radio link: a stream that
    connects, records, and drops an hour later must come back every time."""
    clock = Clock()
    spawned = []

    def spawn(command, log_path=None):
        spawned.append(command)
        return DeadOnArrival()

    recorder = SegmentRecorder(
        stream="thermal",
        source_url="rtsp://example/stream",
        output_dir=tmp_path / "thermal",
        segment_seconds=300,
        spawn=spawn,
        clock=clock,
    )
    for _ in range(10):
        recorder.start()
        clock.advance(600.0)  # it recorded for ten minutes, then the link dropped
        assert recorder.running is False

    assert len(spawned) == 10, "a stream that records must never stop being restarted"
    assert recorder.held_back is False
