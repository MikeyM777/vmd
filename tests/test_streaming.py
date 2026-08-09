"""The streaming server: its config, its lifecycle, and what it says when it cannot run."""

from __future__ import annotations

import json
import shutil
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from vmd.settings import CameraSettings, Settings, StreamSettings
from vmd.streaming.go2rtc import Go2rtcService, build_config, find_binary, write_config


class FakeProcess:
    """A process that is alive until told otherwise."""

    def __init__(self) -> None:
        self.alive = True
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def kill(self) -> None:
        self.killed = True
        self.alive = False

    def wait(self, timeout: float | None = None) -> int:
        return 0


def settings_with(*streams: tuple[str, str, bool]) -> Settings:
    return Settings(
        camera=CameraSettings(
            host="192.168.1.64",
            streams=[StreamSettings(name=n, url=u, enabled=e) for n, u, e in streams],
        )
    )


def service(settings: Settings, tmp_path: Path, spawned: list) -> Go2rtcService:
    def spawn(command: list[str]) -> FakeProcess:
        spawned.append(command)
        return FakeProcess()

    return Go2rtcService(
        settings,
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        spawn=spawn,
    )


def test_config_lists_only_enabled_streams_with_addresses() -> None:
    settings = settings_with(
        ("thermal", "rtsp://cam/thermal", True),
        ("visible", "rtsp://cam/visible", False),
        ("spare", "", True),
    )
    config = build_config(settings, api_port=1984, rtsp_port=8554)
    assert config["streams"] == {"thermal": "rtsp://cam/thermal"}


def test_everything_listens_on_loopback_only() -> None:
    """This machine is air-gapped. A streaming server on 0.0.0.0 is a hole."""
    config = build_config(settings_with(("thermal", "rtsp://cam/t", True)), 1984, 8554)
    assert config["api"]["listen"].startswith("127.0.0.1:")
    assert config["rtsp"]["listen"].startswith("127.0.0.1:")


def test_config_is_written_as_valid_json(tmp_path: Path) -> None:
    """RTSP URLs carry colons, slashes and punctuation-heavy passwords; JSON
    quoting removes a class of bugs that YAML quoting invites."""
    settings = settings_with(("thermal", "rtsp://admin:p@ss:word!@10.0.0.2:554/h264", True))
    path = write_config(build_config(settings, 1984, 8554), tmp_path / "go2rtc.json")
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["streams"]["thermal"] == "rtsp://admin:p@ss:word!@10.0.0.2:554/h264"


def test_start_spawns_the_binary_with_the_config(tmp_path: Path) -> None:
    spawned: list = []
    svc = service(settings_with(("thermal", "rtsp://cam/t", True)), tmp_path, spawned)
    svc.start()
    assert svc.running
    assert len(spawned) == 1
    assert str(svc.config_path) in spawned[0]


def test_start_is_idempotent(tmp_path: Path) -> None:
    spawned: list = []
    svc = service(settings_with(("thermal", "rtsp://cam/t", True)), tmp_path, spawned)
    svc.start()
    svc.start()
    assert len(spawned) == 1, "a second start must not leave two servers fighting for the port"


def test_no_streams_means_no_process_and_a_reason(tmp_path: Path) -> None:
    """An operator who has not entered a camera yet gets the console, not an error."""
    spawned: list = []
    svc = service(Settings(), tmp_path, spawned)
    svc.start()
    assert not svc.running
    assert spawned == []
    assert "Settings" in svc.status().reason


def test_missing_binary_is_reported_not_crashed(tmp_path: Path) -> None:
    svc = Go2rtcService(
        settings_with(("thermal", "rtsp://cam/t", True)),
        config_path=tmp_path / "go2rtc.json",
        binary=None,
    )
    svc.start()
    assert not svc.running
    assert "install" in svc.status().reason


def test_a_failed_spawn_does_not_raise(tmp_path: Path) -> None:
    """The console must survive anything the streaming server does."""

    def spawn(command: list[str]):
        raise OSError("no such file")

    svc = Go2rtcService(
        settings_with(("thermal", "rtsp://cam/t", True)),
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        spawn=spawn,
    )
    svc.start()
    assert not svc.running
    assert svc.status().reason


def test_stop_terminates_and_forgets(tmp_path: Path) -> None:
    spawned: list = []
    svc = service(settings_with(("thermal", "rtsp://cam/t", True)), tmp_path, spawned)
    svc.start()
    process = svc._process
    svc.stop()
    assert process.terminated
    assert not svc.running


def test_stop_keeps_the_handle_when_death_is_unconfirmed(tmp_path: Path) -> None:
    """A forgotten go2rtc holds the camera connection and the port."""

    class Undying(FakeProcess):
        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float | None = None) -> int:
            import subprocess

            raise subprocess.TimeoutExpired("go2rtc", timeout or 0)

    svc = Go2rtcService(
        settings_with(("thermal", "rtsp://cam/t", True)),
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        spawn=lambda command: Undying(),
    )
    svc.start()
    svc.stop()
    assert svc._process is not None, "an unconfirmed kill must stay tracked"


def test_apply_restarts_with_the_new_streams(tmp_path: Path) -> None:
    spawned: list = []
    svc = service(settings_with(("thermal", "rtsp://cam/t", True)), tmp_path, spawned)
    svc.start()
    svc.apply(settings_with(("visible", "rtsp://cam/v", True)))
    assert svc.running
    assert len(spawned) == 2
    written = json.loads(svc.config_path.read_text(encoding="utf-8"))
    assert written["streams"] == {"visible": "rtsp://cam/v"}


def test_apply_with_nothing_enabled_stops_streaming(tmp_path: Path) -> None:
    spawned: list = []
    svc = service(settings_with(("thermal", "rtsp://cam/t", True)), tmp_path, spawned)
    svc.start()
    svc.apply(Settings())
    assert not svc.running


def test_status_names_the_streams_the_page_can_ask_for(tmp_path: Path) -> None:
    spawned: list = []
    svc = service(
        settings_with(("thermal", "rtsp://cam/t", True), ("visible", "rtsp://cam/v", True)),
        tmp_path,
        spawned,
    )
    svc.start()
    status = svc.status()
    assert status.streams == ["thermal", "visible"]
    assert status.api_base.startswith("http://127.0.0.1:")
    assert status.running


def test_the_bundled_binary_is_found_first(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    bundled = tmp_path / "bin" / "go2rtc.exe"
    bundled.write_bytes(b"")
    assert find_binary(tmp_path) == bundled


# --------------------------------------------------------------------------
# The real thing: the actual binary, a real video source, a real HTTP request.
# Marked integration because it spawns processes and takes a few seconds.
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_a_real_stream_reaches_a_browser_playable_url(tmp_path: Path) -> None:
    """End to end: go2rtc ingests video and serves MP4 a browser can play.

    The source is a generated test pattern rather than a camera, so this runs
    anywhere. What it proves is the part that is ours: the config we write, the
    process we spawn, and the URL the page will ask for.
    """
    binary = find_binary()
    if binary is None or shutil.which("ffmpeg") is None:
        pytest.skip("needs the go2rtc binary and ffmpeg")

    api_port, rtsp_port = _free_port(), _free_port()
    source = (
        "exec:ffmpeg -hide_banner -re -f lavfi -i testsrc=size=320x180:rate=15 "
        "-c:v libx264 -preset ultrafast -tune zerolatency -g 15 -f rtsp {output}"
    )
    svc = Go2rtcService(
        settings_with(("thermal", source, True)),
        config_path=tmp_path / "go2rtc.json",
        binary=binary,
        api_port=api_port,
        rtsp_port=rtsp_port,
    )
    svc.start()
    try:
        assert svc.running
        deadline = time.monotonic() + 25
        payload = b""
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"{svc.api_base}/api/frame.jpeg?src=thermal", timeout=5
                ) as response:
                    payload = response.read()
                if payload:
                    break
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(1)
        assert payload.startswith(b"\xff\xd8"), "expected a JPEG frame out of the live stream"
    finally:
        svc.stop()
    assert not svc.running


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
