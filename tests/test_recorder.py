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
    recorder, _ = build(tmp_path)
    command = recorder.build_command()
    assert "-c" in command
    assert command[command.index("-c") + 1] == "copy"
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
