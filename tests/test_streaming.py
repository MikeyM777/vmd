"""The streaming server: its config, its lifecycle, and what it says when it cannot run."""

from __future__ import annotations

import io
import json
import logging
import shutil
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from pydantic import ValidationError

from vmd.settings import CameraSettings, Settings, StreamSettings
from vmd.streaming import go2rtc
from vmd.streaming.go2rtc import (
    Go2rtcService,
    build_config,
    find_binary,
    source_for,
    write_config,
)


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


def unvalidated_stream(name: str, url: str) -> StreamSettings:
    """A stream carrying a source the operator is no longer allowed to type.

    `exec:` is how the integration test manufactures a camera out of ffmpeg, and
    it is also the reason StreamSettings now refuses everything but rtsp and a
    local path - go2rtc's `exec:` runs a command line. The test harness may
    build one; the Settings tab may not, which is the whole point.
    """
    return StreamSettings.model_construct(name=name, url=url, enabled=True)


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


def test_nothing_in_the_config_reaches_out_of_this_machine() -> None:
    """Listening on loopback says nothing about what the server dials.

    Left with a WebRTC listener, go2rtc gathers ICE candidates from its
    compiled-in defaults - Google's, Cloudflare's and Amazon's STUN servers.
    That is outbound traffic from a machine that is supposed to be air-gapped,
    for a transport nothing here uses: every video pane plays rtsp://127.0.0.1
    through VLC, and the browser console this was built for no longer exists.
    """
    config = build_config(settings_with(("thermal", "rtsp://cam/t", True)), 1984, 8554)
    assert config["webrtc"]["listen"] == "", "a WebRTC listener means outbound STUN"
    assert config["webrtc"]["ice_servers"] == []
    # And nothing already running on this machine may drive the streaming
    # server from a web page: the wildcard was for the console page that is gone.
    assert config["api"].get("origin") != "*"


def test_the_config_names_no_host_outside_this_machine() -> None:
    """A blunt read of the whole document, so a future key cannot smuggle one in."""
    settings = settings_with(("thermal", "rtsp://10.0.0.5/ch1", True))
    text = json.dumps(build_config(settings, 1984, 8554))
    for outside in ("stun:", "turn:", "google", "cloudflare", "amazonaws", "0.0.0.0"):
        assert outside not in text.lower(), f"{outside} appears in the go2rtc config"


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
        Settings(camera=CameraSettings(streams=[unvalidated_stream("thermal", source)])),
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


def test_camera_credentials_are_put_into_the_stream_url() -> None:
    """The operator types the password in its own field; RTSP carries it in the
    URL. Without this the camera answers "user/pass not provided"."""
    settings = Settings(
        camera=CameraSettings(
            host="192.168.1.64",
            username="admin",
            password="p@ss:w/rd",
            streams=[StreamSettings(name="visible", url="rtsp://192.168.1.64:554/live", enabled=True)],
        )
    )
    url = build_config(settings, 1984, 8554)["streams"]["visible"]
    assert url == "rtsp://admin:p%40ss%3Aw%2Frd@192.168.1.64:554/live"


def test_a_url_with_its_own_credentials_is_left_alone() -> None:
    settings = Settings(
        camera=CameraSettings(
            username="admin",
            password="fromform",
            streams=[StreamSettings(name="t", url="rtsp://own:creds@10.0.0.2/s", enabled=True)],
        )
    )
    assert build_config(settings, 1984, 8554)["streams"]["t"] == "rtsp://own:creds@10.0.0.2/s"


def test_non_rtsp_sources_are_untouched() -> None:
    """The URL builder must leave a scheme it does not understand exactly alone.

    Still the right behaviour at this layer - splicing credentials into a source
    that has nowhere to put them would corrupt it - even though the settings
    model now refuses to let such a source be typed in the first place.
    """
    from vmd.streaming.go2rtc import with_credentials

    assert with_credentials("exec:ffmpeg -i thing {output}", "admin", "x") == (
        "exec:ffmpeg -i thing {output}"
    )


def test_a_stream_address_may_only_be_a_camera_or_a_file() -> None:
    """One paste into the address box must not turn this into a cloud client.

    go2rtc's source parser understands far more than RTSP: `exec:` runs a
    command line, and `ring:`, `wyze:`, `tapo:`, `hass:` and `http(s):` reach
    out to a vendor's servers. On a machine that is deliberately air-gapped,
    those are not features, and nothing downstream would object - passing an
    unknown scheme through untouched is what the URL builder is for.
    """
    for refused in (
        "exec:ffmpeg -i thing {output}",
        "ring:whatever",
        "wyze:whatever",
        "http://api.example.com/stream",
        "https://api.example.com/stream",
        "hass:camera.front",
        "ngrok:1234",
    ):
        with pytest.raises(ValidationError):
            StreamSettings(name="t", url=refused)

    # What the operator really types, and what the recorder is tested with,
    # both still load.
    assert StreamSettings(name="t", url="rtsp://10.0.0.5:554/ch1").url
    assert StreamSettings(name="t", url="rtsps://10.0.0.5:322/ch1").url
    assert StreamSettings(name="t", url=r"C:\footage\clip.mp4").url
    assert StreamSettings(name="t", url="/footage/clip.mp4").url
    assert StreamSettings(name="t", url="").url == ""


def test_a_dead_server_is_restarted(tmp_path: Path) -> None:
    """Nothing was restarting go2rtc, so one exit meant no video until the whole
    console was restarted by hand."""
    spawned: list = []
    svc = service(settings_with(("thermal", "rtsp://cam/t", True)), tmp_path, spawned)
    svc.start()
    svc._process.alive = False  # it died on its own
    assert not svc.running
    svc.ensure_running()
    assert svc.running
    assert len(spawned) == 2


class DeadOnArrival(FakeProcess):
    """go2rtc as a corrupt binary or an unparseable config leaves it: spawned,
    and gone before anyone looks."""

    def __init__(self) -> None:
        super().__init__()
        self.alive = False


def test_a_go2rtc_that_will_not_stay_up_does_not_cost_the_heartbeat(
    tmp_path: Path,
) -> None:
    """Confirming a launch used to mean sleeping 0.8 s inside start().

    start() is what the supervisor calls on every tick, and the supervisor ticks
    on the thread that draws the window. A go2rtc that exits immediately - a
    corrupt binary, a config it will not parse - therefore held the window for
    0.8 s out of every 2 s, for as long as the console was open. Nothing
    repainted for 40% of the operator's day, and the alarm strip could not
    appear during any of it.
    """
    svc = Go2rtcService(
        settings_with(("thermal", "rtsp://cam/t", True)),
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        spawn=lambda command: DeadOnArrival(),
    )
    started = time.monotonic()
    for _ in range(5):  # five heartbeats' worth
        svc.start()
    elapsed = time.monotonic() - started
    assert elapsed < 0.5, f"five ticks against a dead go2rtc cost {elapsed:.2f} s"


def test_a_go2rtc_that_will_not_stay_up_still_says_so(tmp_path: Path, caplog) -> None:
    """Not waiting for the bad news must not mean never hearing it."""
    svc = Go2rtcService(
        settings_with(("thermal", "rtsp://cam/t", True)),
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        spawn=lambda command: DeadOnArrival(),
    )
    with caplog.at_level(logging.WARNING, logger="vmd.streaming.go2rtc"):
        svc.start()
        svc.start()  # the next tick is where it is noticed
    assert not svc.running
    assert any("exited" in record.getMessage() for record in caplog.records)
    assert "stopped" in svc.status().reason


def test_a_go2rtc_that_did_not_stay_up_leaves_no_address_behind(tmp_path: Path) -> None:
    """A streaming.json naming a port nothing listens on would have the recorder
    believe there is a local copy of the stream to read."""
    svc = Go2rtcService(
        settings_with(("thermal", "rtsp://cam/t", True)),
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        endpoint_path=tmp_path / "streaming.json",
        spawn=lambda command: DeadOnArrival(),
    )
    svc.start()
    assert svc.status().running is False, "a process that is gone is not streaming"
    assert not (tmp_path / "streaming.json").exists()


def test_a_live_go2rtc_publishes_where_it_is_listening(tmp_path: Path) -> None:
    """The recorder reads this instead of opening its own connection to the
    camera, so it has to be there before the recorder starts - not one
    heartbeat later."""
    spawned: list = []
    svc = Go2rtcService(
        settings_with(("thermal", "rtsp://cam/t", True)),
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        endpoint_path=tmp_path / "streaming.json",
        spawn=lambda command: (spawned.append(command), FakeProcess())[1],
    )
    svc.start()
    written = json.loads((tmp_path / "streaming.json").read_text(encoding="utf-8"))
    assert written["rtsp_port"] == svc.rtsp_port
    assert written["streams"]["thermal"] == svc.local_rtsp_url("thermal")


def test_ensure_running_does_nothing_when_it_is_running(tmp_path: Path) -> None:
    spawned: list = []
    svc = service(settings_with(("thermal", "rtsp://cam/t", True)), tmp_path, spawned)
    svc.start()
    svc.ensure_running()
    assert len(spawned) == 1


def test_a_death_is_explained_with_the_exit_code(tmp_path: Path) -> None:
    spawned: list = []
    svc = service(settings_with(("thermal", "rtsp://cam/t", True)), tmp_path, spawned)
    svc.start()
    svc._process.alive = False
    svc._exit_code = 1
    svc._process = None
    reason = svc.status().reason
    assert "stopped" in reason and "exit 1" in reason


# ------------------------------------------------- what go2rtc says out loud
#
# The camera answers "401 Unauthorized" and go2rtc is the only thing that
# repeats it. That line reaching the operator's Logs tab, tagged so they know
# what said it, is the whole reason its output is piped rather than discarded.


class TalkingProcess(FakeProcess):
    """A go2rtc whose stdout is under the test's control, as Popen's would be."""

    def __init__(self, output: str = "") -> None:
        super().__init__()
        self.stdout = io.StringIO(output)


def talking_service(tmp_path: Path, process: TalkingProcess) -> Go2rtcService:
    return Go2rtcService(
        settings_with(("thermal", "rtsp://cam/t", True)),
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        spawn=lambda command: process,
    )


def wait_for_pump(predicate, timeout: float = 5.0) -> bool:
    """Bounded, always: a pump that never runs must fail the test, not hang it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_go2rtc_s_own_words_reach_the_log_tagged_with_its_name(tmp_path: Path, caplog) -> None:
    """"401 Unauthorized" from nowhere is a line the operator cannot act on."""
    caplog.set_level(logging.INFO, logger="go2rtc")
    svc = talking_service(tmp_path, TalkingProcess("[rtsp] 401 Unauthorized\n"))
    svc.start()

    assert wait_for_pump(lambda: any("401" in record.getMessage() for record in caplog.records))
    said = [r for r in caplog.records if "401" in r.getMessage()][0]
    assert "go2rtc" in said.getMessage(), "the line must name what said it"
    assert said.levelno >= logging.WARNING, "a refused login is not routine chatter"


def test_one_enormous_go2rtc_line_is_cut_rather_than_kept_whole(tmp_path: Path, caplog) -> None:
    """The ring buffer's capacity is no defence against a single line that is
    half a megabyte long."""
    caplog.set_level(logging.INFO, logger="go2rtc")
    svc = talking_service(tmp_path, TalkingProcess("y" * 500_000 + "\n"))
    svc.start()

    assert wait_for_pump(lambda: any("yyy" in record.getMessage() for record in caplog.records))
    longest = max(len(r.getMessage()) for r in caplog.records if "yyy" in r.getMessage())
    assert longest < 5000, "one line held whole is the bug"


# ------------------------------------------------------- whose process is that
#
# An adopted go2rtc could not be stopped at all. streaming.json carries the
# ports and no PID, and stop() only ever stopped a process object this service
# held - so a settings change left the adopted one running and started a SECOND
# go2rtc on a different port. That is a second connection across the radio link,
# which is the one cost this whole architecture exists to avoid.
#
# The claim is shaped after the recorder's, in vmd/record_main.py, and for the
# same reason: the file holds a bare integer because several readers parse it
# that way, and anything richer goes in a companion beside it.


def claiming_service(
    tmp_path: Path,
    spawn=None,
    image_of=None,
    kill_tree=None,
    booted=None,
    pid: int = 4242,
) -> Go2rtcService:
    class Spawned:
        def __init__(self) -> None:
            self.pid = pid
            self.alive = True

        def poll(self):
            return None if self.alive else 0

        def terminate(self) -> None:
            self.alive = False

        def kill(self) -> None:
            self.alive = False

        def wait(self, timeout=None):
            return 0

    return Go2rtcService(
        settings_with(("thermal", "rtsp://cam/t", True)),
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        endpoint_path=tmp_path / "streaming.json",
        spawn=spawn or (lambda command: Spawned()),
        image_of=image_of or (lambda p: "go2rtc.exe"),
        kill_tree=kill_tree,
        booted=booted or (lambda: None),
    )


def test_a_running_go2rtc_says_which_process_it_is(tmp_path: Path) -> None:
    svc = claiming_service(tmp_path)
    svc.start()
    claim = tmp_path / "go2rtc.pid"
    assert int(claim.read_text(encoding="utf-8").strip()) == 4242


def test_the_claim_file_holds_a_bare_integer_and_nothing_else(tmp_path: Path) -> None:
    """Anything richer would make a reader that parses the whole file as a
    number read "nothing is running", whose remedy is to start a second one."""
    svc = claiming_service(tmp_path)
    svc.start()
    text = (tmp_path / "go2rtc.pid").read_text(encoding="utf-8")
    assert text.strip().isdigit()
    written = json.loads((tmp_path / "go2rtc.pid.json").read_text(encoding="utf-8"))
    assert written["pid"] == 4242
    assert written["rtsp_port"] == svc.rtsp_port


def test_stopping_a_go2rtc_this_console_started_drops_the_claim(tmp_path: Path) -> None:
    svc = claiming_service(tmp_path)
    svc.start()
    svc.stop()
    assert not (tmp_path / "go2rtc.pid").exists()
    assert not (tmp_path / "go2rtc.pid.json").exists()


def adopting_service(tmp_path: Path, killed: list, **kwargs) -> Go2rtcService:
    """A console that finds a live go2rtc from an earlier run and takes it on."""
    (tmp_path / "go2rtc.pid").write_text("4242", encoding="utf-8")
    (tmp_path / "go2rtc.pid.json").write_text(
        json.dumps(
            {
                "pid": 4242,
                "executable": str(tmp_path / "go2rtc.exe"),
                "api_port": 1984,
                "rtsp_port": 8554,
                "written_at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    living = {4242}

    def kill_tree(pid: int) -> bool:
        killed.append(pid)
        living.discard(pid)
        return True

    kwargs.setdefault("kill_tree", kill_tree)
    kwargs.setdefault("image_of", lambda p: "go2rtc.exe" if p in living else None)
    return claiming_service(tmp_path, **kwargs)


def test_an_adopted_go2rtc_is_running_so_nothing_starts_a_second_one(
    tmp_path: Path,
) -> None:
    """The supervisor starts anything that is not running, every two seconds."""
    spawned: list = []
    svc = adopting_service(
        tmp_path,
        killed=[],
        spawn=lambda command: pytest.fail("a live go2rtc must not be duplicated"),
    )
    assert svc.adopt({"api_port": 1984, "rtsp_port": 8554}) is True
    assert svc.running is True
    svc.start()  # what the supervisor does on every tick
    assert spawned == []


def test_an_adopted_go2rtc_can_be_stopped_and_replaced(tmp_path: Path) -> None:
    """The defect: the adopted one was left running and a second one started."""
    killed: list = []
    spawned: list = []
    svc = adopting_service(tmp_path, killed)
    svc._spawn = lambda command: (spawned.append(command), _AliveProcess(7000))[1]
    svc.adopt({"api_port": 1984, "rtsp_port": 8554})

    svc.apply(settings_with(("visible", "rtsp://cam/v", True)))

    assert killed == [4242], "the adopted streaming server was left running"
    assert len(spawned) == 1, "a second go2rtc was started beside the first"
    assert svc.running is True
    assert svc.adopted is False


def test_closing_the_console_leaves_an_adopted_go2rtc_alone(tmp_path: Path) -> None:
    """It serves the recorder too, and the recorder outlives the window."""
    killed: list = []
    svc = adopting_service(tmp_path, killed)
    svc.adopt({"api_port": 1984, "rtsp_port": 8554})
    (tmp_path / "streaming.json").write_text("{\"rtsp_port\": 8554}", encoding="utf-8")
    svc.stop()
    assert killed == [], "closing a window is not a configuration change"
    assert (tmp_path / "streaming.json").exists(), (
        "the address of a server that is still serving was thrown away"
    )
    assert (tmp_path / "go2rtc.pid").exists(), "so was the claim naming it"


def test_an_adopted_go2rtc_that_will_not_stop_is_reported_not_replaced(
    tmp_path: Path, caplog
) -> None:
    """Two of them on one camera is worse than a setting that did not apply."""
    spawned: list = []
    svc = adopting_service(
        tmp_path,
        killed=[],
        kill_tree=lambda pid: True,  # accepted, and nothing dies
        image_of=lambda p: "go2rtc.exe",
    )
    svc._spawn = lambda command: (spawned.append(command), _AliveProcess(7000))[1]
    svc.adopt({"api_port": 1984, "rtsp_port": 8554})

    with caplog.at_level(logging.ERROR):
        svc.apply(settings_with(("visible", "rtsp://cam/v", True)))

    assert spawned == [], "a second go2rtc was started on top of a live one"
    assert svc.adopted is True
    assert any("NOT in effect" in r.getMessage() for r in caplog.records)


def test_a_claim_naming_something_that_is_not_go2rtc_is_not_adopted(
    tmp_path: Path,
) -> None:
    """Windows hands PIDs out again from a small pool, and this file survives a
    power cut."""
    svc = adopting_service(tmp_path, killed=[], image_of=lambda p: "notepad.exe")
    svc.adopt({"api_port": 1984, "rtsp_port": 8554})
    assert svc.claimed_pid() is None


def test_a_claim_written_before_the_last_boot_is_not_believed(tmp_path: Path) -> None:
    svc = adopting_service(
        tmp_path, killed=[], booted=lambda: time.time() + 60.0  # booted after it was written
    )
    assert svc.claimed_pid() is None


def test_an_adopted_go2rtc_nobody_can_name_is_still_not_duplicated(
    tmp_path: Path, caplog
) -> None:
    """No claim file at all - a go2rtc started by a console older than this
    change. Stopping it is impossible, and starting a second one is worse than
    saying so."""
    spawned: list = []
    svc = claiming_service(tmp_path, spawn=lambda c: (spawned.append(c), _AliveProcess(1))[1])
    with caplog.at_level(logging.WARNING):
        assert svc.adopt({"api_port": 1984, "rtsp_port": 8554}) is True
    assert svc.running is True
    svc.start()
    assert spawned == []
    assert any("cannot stop it" in r.getMessage() for r in caplog.records)


class _AliveProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self) -> None:
        self.alive = False

    def kill(self) -> None:
        self.alive = False

    def wait(self, timeout=None):
        return 0


# ------------------------------------ adopting a go2rtc on evidence, not a file
#
# From the operator's laptop, in this order and within two seconds:
#
#   a streaming server is already running; adopting it
#   showing rtsp://127.0.0.1:8554/thermal
#   live555 demux error: Failed to connect with rtsp://127.0.0.1:8554/thermal
#
# streaming.json records ports. It does not record a process, and a port is a
# claim rather than an answer. Two ways that claim goes wrong, and both leave
# the console with no picture at all and nothing said about it:
#
#   * the server it names is gone - the file outlived it, through a crash, a
#     power cut or a taskkill - and something else has the port, or nothing has;
#   * the server is alive but was started before these streams were configured.
#     go2rtc reads its config once, so it is serving other names, or none, and
#     every pane asks it for a stream it has never heard of.
#
# go2rtc answers both questions itself, on the loopback API this file already
# talks to. Adoption is now on that answer.


class FakeGo2rtc:
    """A go2rtc that answers on both of its ports, because the real one does.

    The API port answers `/api/streams` and `/api/log`; the RTSP port answers
    DESCRIBE. The two ports are the whole point of this fake: on the operator's
    laptop the API listed both stream names all morning while every DESCRIBE
    against the RTSP port was refused, and the console adopted on the strength
    of the first without ever making the second.

    `serves` is the set of names DESCRIBE answers 200 for. `said` is what the
    server has written in its own log, in the shape `/api/log` really uses -
    one JSON object per line, `error` and `stream` among the keys.
    """

    SDP = (
        "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=go2rtc\r\nt=0 0\r\n"
        "m=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\na=control:trackID=0\r\n"
    )

    def __init__(
        self,
        streams,
        serves=None,
        said=(),
        answers: bool = True,
    ) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from socketserver import StreamRequestHandler, ThreadingTCPServer

        if not isinstance(streams, dict):
            streams = {name: "" for name in streams}
        self.streams = dict(streams)
        self.serves = set(self.streams if serves is None else serves)
        self.said = list(said)
        # Every name DESCRIBE was asked about, so a test can prove the proof was
        # actually made rather than assumed.
        self.described: list[str] = []
        outer = self

        api_body = {
            name: {
                "producers": [{"url": source}] if source else [{}],
                "consumers": None,
            }
            for name, source in self.streams.items()
        }

        class Api(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server's name
                if self.path.startswith("/api/log"):
                    body = "".join(
                        json.dumps(entry) + "\n" for entry in outer.said
                    ).encode("utf-8")
                else:
                    body = json.dumps(api_body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:  # keep pytest's output readable
                return

        class Rtsp(StreamRequestHandler):
            def handle(self) -> None:
                self.connection.settimeout(5.0)
                try:
                    head = b""
                    while b"\r\n\r\n" not in head and len(head) < 8192:
                        chunk = self.connection.recv(1024)
                        if not chunk:
                            return
                        head += chunk
                except OSError:
                    return
                request = head.decode("utf-8", "replace")
                name = request.split(" ", 2)[1].rsplit("/", 1)[-1] if " " in request else ""
                outer.described.append(name)
                if not outer.answers:
                    # A server that accepts the connection and then says
                    # nothing. Bounded here by the socket's own timeout above,
                    # so this fake can never wedge the suite.
                    try:
                        self.connection.recv(1)
                    except OSError:
                        pass
                    return
                cseq = "1"
                for line in request.splitlines():
                    if line.lower().startswith("cseq:"):
                        cseq = line.split(":", 1)[1].strip()
                if name in outer.serves:
                    body = outer.SDP
                    answer = (
                        f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\n"
                        f"Content-Type: application/sdp\r\n"
                        f"Content-Length: {len(body)}\r\n\r\n{body}"
                    )
                else:
                    answer = f"RTSP/1.0 404 Not Found\r\nCSeq: {cseq}\r\n\r\n"
                try:
                    self.connection.sendall(answer.encode("utf-8"))
                except OSError:
                    return

        self.answers = answers
        self._api = ThreadingHTTPServer(("127.0.0.1", 0), Api)
        self._rtsp = ThreadingTCPServer(("127.0.0.1", 0), Rtsp)
        self._rtsp.daemon_threads = True
        for server in (self._api, self._rtsp):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @property
    def endpoint(self) -> dict:
        return {
            "api_port": self._api.server_address[1],
            "rtsp_port": self._rtsp.server_address[1],
            "streams": {},
        }

    def close(self) -> None:
        for server in (self._api, self._rtsp):
            server.shutdown()
            server.server_close()


def test_a_server_that_is_serving_these_streams_can_be_adopted(tmp_path: Path) -> None:
    """The half that must not break: adoption is why closing the console does
    not stop recording."""
    server = FakeGo2rtc({"thermal": "rtsp://cam/t"})
    try:
        svc = claiming_service(tmp_path)
        assert svc.unadoptable(server.endpoint) == ""
        assert server.described == ["thermal"], (
            "adoption must prove the video, not the name"
        )
    finally:
        server.close()


def test_a_server_serving_other_streams_is_not_adopted(tmp_path: Path) -> None:
    """Alive, listening, and holding a config written before these streams
    existed. Adopting it points every pane at a name it has never heard of."""
    server = FakeGo2rtc({"door": "rtsp://cam/d", "gate": "rtsp://cam/g"})
    try:
        svc = claiming_service(tmp_path)
        why = svc.unadoptable(server.endpoint)
    finally:
        server.close()

    assert why, "a server that does not serve thermal must not be adopted"
    assert "thermal" in why, why


# ------------------------------------------- and that it can produce a picture
#
# The operator's second morning, and the reason this file grew the fake above.
# The console adopted a go2rtc that was listening on 8554 and did list both
# stream names - and every pane got `Failed to setup RTSP session`, for three
# minutes, indefinitely. Nothing was wrong with the port and nothing was wrong
# with the names. What was wrong was that the server could not get the picture
# from the camera at all: it had been started before the password was corrected
# in one round of this, and in the next round the camera would not give it a
# session because the recorder already held the only one it offers.
#
# A name is not a picture. `/api/streams` cannot tell the two apart - it lists a
# producer with a URL and nothing else whether that producer has ever connected
# or not, which was measured against the bundled go2rtc 1.9.14 and is why none
# of this reads that list for health. DESCRIBE can: it is the first thing VLC
# sends, it makes go2rtc go to the camera, and it answers 404 when go2rtc
# cannot.


def test_a_server_that_cannot_produce_the_picture_is_not_adopted(tmp_path: Path) -> None:
    """Listening, and it knows the name. It still has no video to give."""
    server = FakeGo2rtc({"thermal": "rtsp://cam/t"}, serves=set())
    try:
        svc = claiming_service(tmp_path)
        why = svc.unadoptable(server.endpoint)
    finally:
        server.close()

    assert why, "a server with no picture in it must not be adopted"
    assert "thermal" in why, why


def test_the_reason_is_go2rtcs_own_words(tmp_path: Path) -> None:
    """"401 Unauthorized" is the single most useful string this system can show,
    and on an adopted server it is trapped inside a process whose output goes to
    a console that has closed. `/api/log` is where it can still be read."""
    server = FakeGo2rtc(
        {"thermal": "rtsp://cam/t"},
        serves=set(),
        said=[
            {"level": "warn", "error": "streams: dial tcp: refused", "stream": "other"},
            {"level": "warn", "error": "streams: 401 Unauthorized", "stream": "thermal"},
        ],
    )
    try:
        svc = claiming_service(tmp_path)
        why = svc.unadoptable(server.endpoint)
    finally:
        server.close()

    assert "401 Unauthorized" in why, why


def test_a_stream_nobody_has_asked_for_yet_is_not_called_broken(tmp_path: Path) -> None:
    """Idle is not broken, and go2rtc does not connect to a camera until
    something subscribes: a server whose producers have never run must still be
    adoptable if it can produce when it is asked. That is exactly why the proof
    is a DESCRIBE and not a reading of `/api/streams`."""
    server = FakeGo2rtc({"thermal": ""}, serves={"thermal"})
    try:
        svc = claiming_service(tmp_path)
        assert svc.unadoptable(server.endpoint) == ""
    finally:
        server.close()


# ------------------------------------------- and that it read these settings
#
# The general case of the same defect. go2rtc reads its configuration once, at
# startup, and this console rewrites go2rtc.json every time it starts - so the
# file on disk and the settings inside the running process are two different
# things, and nothing compared them. A corrected password, a corrected address,
# a renamed stream and a changed reader all land in the same state: a server
# that is listening, knows the names, and is running something else.
#
# It is not asked of the API. `/api/config` looks like the answer and is a trap:
# measured against the bundled 1.9.14, it re-reads the file from disk, so it
# reported the corrected password and a stream that had been added since -
# neither of which the running process had ever seen. It would agree with
# whatever the console had just written, every time.
#
# So the console writes down what it started the server with, and compares that.


def claim_with(tmp_path: Path, fingerprint: str, api_port: int, rtsp_port: int) -> None:
    (tmp_path / "go2rtc.pid").write_text("4242", encoding="utf-8")
    (tmp_path / "go2rtc.pid.json").write_text(
        json.dumps(
            {
                "pid": 4242,
                "executable": str(tmp_path / "go2rtc.exe"),
                "api_port": api_port,
                "rtsp_port": rtsp_port,
                "written_at": time.time(),
                "streams_fingerprint": fingerprint,
            }
        ),
        encoding="utf-8",
    )


def test_a_server_started_before_the_password_was_corrected_is_not_adopted(
    tmp_path: Path,
) -> None:
    """The whole fault in one test.

    This server is listening, it knows the name, and it would even hand over a
    picture - it is simply running the mistyped password the operator has since
    corrected, and no probe of it can ever say so. What says so is that this
    console wrote down what it started that server with.
    """
    from vmd.streaming.go2rtc import config_fingerprint

    settings = settings_with(("thermal", "rtsp://cam/t", True))
    settings.camera.username = "admin"
    settings.camera.password = "mistyped"
    was_started_with = config_fingerprint(build_config(settings, 1984, 8554))

    server = FakeGo2rtc({"thermal": "rtsp://admin:mistyped@cam/t"})
    try:
        svc = claiming_service(tmp_path)
        svc.settings.camera.username = "admin"
        svc.settings.camera.password = "corrected"
        claim_with(
            tmp_path,
            was_started_with,
            server.endpoint["api_port"],
            server.endpoint["rtsp_port"],
        )
        why = svc.unadoptable(server.endpoint)
    finally:
        server.close()

    assert why, "a server running settings that have been replaced was adopted"
    assert "settings" in why.lower(), why


def test_the_console_writes_down_what_it_started_the_server_with(tmp_path: Path) -> None:
    """And the other half: the same settings must still be adoptable, or every
    console start replaces a working server and the picture goes for nothing."""
    from vmd.streaming.go2rtc import config_fingerprint

    svc = claiming_service(tmp_path)
    svc.start()
    written = json.loads((tmp_path / "go2rtc.pid.json").read_text(encoding="utf-8"))
    expected = config_fingerprint(
        build_config(svc.settings, svc.api_port, svc.rtsp_port)
    )
    assert written["streams_fingerprint"] == expected

    server = FakeGo2rtc({"thermal": "rtsp://cam/t"})
    try:
        claim_with(
            tmp_path,
            expected,
            server.endpoint["api_port"],
            server.endpoint["rtsp_port"],
        )
        assert svc.unadoptable(server.endpoint) == ""
    finally:
        server.close()


def test_the_password_is_never_weighed_against_what_the_api_says(tmp_path: Path) -> None:
    """A server started by a console older than this has nothing written down,
    so the only thing left to compare is what the API reports - and a go2rtc
    that redacts the password in that answer would then disagree with every
    correct config for ever, and be stopped and started for ever with it. The
    credentials are taken out of both sides before anything is compared."""
    server = FakeGo2rtc({"thermal": "rtsp://admin:***@cam/t"})
    try:
        svc = claiming_service(tmp_path)
        svc.settings.camera.username = "admin"
        svc.settings.camera.password = "secret"
        assert svc.unadoptable(server.endpoint) == ""
    finally:
        server.close()


def test_a_server_pointed_at_another_address_is_not_adopted(tmp_path: Path) -> None:
    """Nothing written down, and what it is holding is a different camera."""
    server = FakeGo2rtc({"thermal": "rtsp://10.9.9.9/old"})
    try:
        svc = claiming_service(tmp_path)
        why = svc.unadoptable(server.endpoint)
    finally:
        server.close()

    assert why, "a server pointed at another address was adopted"
    assert "thermal" in why, why


def test_the_proof_cannot_hang_the_console(tmp_path: Path) -> None:
    """It runs at every console start, in front of a blank window. A server that
    accepts the connection and never answers must cost the probe's own bound and
    not a second more."""
    server = FakeGo2rtc({"thermal": "rtsp://cam/t"}, answers=False)
    try:
        svc = claiming_service(tmp_path)
        started = time.monotonic()
        why = svc.unadoptable(server.endpoint, probe_timeout=0.5)
        took = time.monotonic() - started
    finally:
        server.close()

    assert why, "a server that will not answer must not be adopted"
    assert took < 3.0, f"the proof took {took:.1f}s in front of the operator"


def test_a_port_nothing_answers_on_is_not_adopted(tmp_path: Path) -> None:
    """The file outlived the process. Nothing is listening, and the console
    must find that out before it hands the panes an address."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    svc = claiming_service(tmp_path)

    why = svc.unadoptable({"api_port": port, "rtsp_port": port, "streams": {}})

    assert why, "a dead endpoint must not be adopted"
    assert str(port) in why, why


def test_replacing_a_ghost_stops_the_process_the_claim_names(
    tmp_path: Path, caplog
) -> None:
    """It is holding the port the fresh one wants, and it is the only thing on
    disk that can be stopped: the claim written when it was started."""
    killed: list[int] = []
    spawned: list = []
    svc = adopting_service(tmp_path, killed)
    svc._spawn = lambda command: (spawned.append(command), _AliveProcess(7000))[1]
    (tmp_path / "streaming.json").write_text("{}", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        svc.replace("it is not serving thermal")

    assert killed == [4242], "the ghost was left holding the camera and the port"
    assert len(spawned) == 1, "the console must not be left with no video"
    assert svc.adopted is False
    assert svc.running is True
    said = " ".join(record.getMessage() for record in caplog.records)
    assert "it is not serving thermal" in said, (
        "the operator watches the picture disappear and come back; the Logs tab "
        "is the only place that can say why: " + said
    )


def test_replacing_a_ghost_nobody_can_name_still_leaves_a_picture(tmp_path: Path) -> None:
    """No claim on disk, so it cannot be stopped. Starting a fresh one anyway is
    right here and only here: the alternative is a console with no video at all,
    which is where this defect was found."""
    spawned: list = []
    svc = claiming_service(tmp_path, spawn=lambda c: (spawned.append(c), _AliveProcess(7000))[1])

    svc.replace("nothing answered its API")

    assert len(spawned) == 1
    assert svc.running is True


def test_the_ffmpeg_reader_does_not_pull_audio_across_the_link(tmp_path: Path) -> None:
    """Nothing on this machine has ever listened to the camera's audio.

    The panes pass --no-audio to libVLC and the recorder now passes -an to
    ffmpeg, and this is the third place the same decision belongs: a source
    string that says `#audio=copy` has go2rtc pull an audio track over a radio
    link with five megabits on it, decode nothing with it, and hand it to a
    recorder that throws it away.
    """
    stream = StreamSettings(name="thermal", url="rtsp://cam/thermal", enabled=True)
    stream.reader = "ffmpeg"

    source = source_for(stream, "root", "secret")

    assert source.startswith("ffmpeg:")
    assert "#video=copy" in source
    assert "audio" not in source, source


# ------------------------------------ the one supervised child with no give-up
#
# Every other restart loop in this codebase learned this and wrote it down:
# SPAWN_LIMIT for the console's children, RESTART_LIMIT for ffmpeg,
# RESTART_BACKOFF_MAX for the video panes. `Go2rtcService` had none, and the
# supervisor calls start() on anything not running every two seconds. A go2rtc
# that exits immediately - a half-copied binary, a config it will not parse, a
# port collision it loses - was therefore spawned every two seconds for months,
# with an ERROR written every time: thirty lines a minute into a 500-line ring.
#
# That is not merely noisy. It empties the Logs tab of everything else in about
# seventeen minutes, and the Logs tab is the only thing on this machine the
# operator can read. The line explaining a mistyped camera password was lost
# exactly this way and it cost the owner hours.


class FakeClock:
    """A clock the test moves. Bounded by construction: nothing waits on it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def flapping_service(tmp_path: Path, clock: FakeClock, spawned: list) -> Go2rtcService:
    def spawn(command: list[str]) -> DeadOnArrival:
        spawned.append(command)
        return DeadOnArrival()

    return Go2rtcService(
        settings_with(("thermal", "rtsp://cam/t", True)),
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        endpoint_path=tmp_path / "streaming.json",
        spawn=spawn,
        clock=clock,
    )


def test_a_go2rtc_that_will_not_stay_up_stops_being_started(tmp_path: Path) -> None:
    """Restarting something two dozen times while it fails identically every
    time is not supervision."""
    clock, spawned = FakeClock(), []
    svc = flapping_service(tmp_path, clock, spawned)

    for _ in range(30):  # a minute of the supervisor's heartbeat
        svc.start()
        clock.now += 2.0

    assert len(spawned) <= go2rtc.RESTART_LIMIT, (
        f"go2rtc was started {len(spawned)} times in a minute of failing identically"
    )
    assert svc.held_back is True
    assert svc.running is False


def test_it_says_it_is_not_running_rather_than_implying_it_will_come_back(
    tmp_path: Path,
) -> None:
    """"the streaming server stopped" reads as something being done about it.
    Nothing is being done about it, and saying so is the whole point."""
    clock, spawned = FakeClock(), []
    svc = flapping_service(tmp_path, clock, spawned)
    for _ in range(30):
        svc.start()
        clock.now += 2.0

    status = svc.status()
    assert status.running is False
    assert status.held_back is True
    assert "not being started again" in status.reason, status.reason
    # Shouted, the way the console shouts "NOT recording": this is the state an
    # operator reading past it would take for a temporary one.
    assert "not running" in status.reason.lower(), status.reason
    assert "stopped" not in status.reason, status.reason


def test_the_same_death_does_not_empty_the_log_buffer(tmp_path: Path, caplog) -> None:
    """Thirty lines a minute into a 500-line ring is the fault destroying the
    one diagnostic the operator has."""
    clock, spawned = FakeClock(), []
    svc = flapping_service(tmp_path, clock, spawned)

    with caplog.at_level(logging.WARNING):
        for _ in range(60):  # two minutes of heartbeats
            svc.start()
            clock.now += 2.0

    said = [r for r in caplog.records if "exited" in r.getMessage()]
    assert len(said) <= 4, (
        f"{len(said)} lines about one death in two minutes; a 500-line ring is "
        "empty of everything else inside seventeen minutes at that rate"
    )
    assert said, "going quiet about it is the other way to lose the diagnosis"


def test_the_count_is_said_rather_than_the_line_repeated(tmp_path: Path, caplog) -> None:
    """Dropping the repeats silently would leave the operator reading three
    lines and believing it happened three times."""
    clock, spawned = FakeClock(), []
    svc = flapping_service(tmp_path, clock, spawned)

    with caplog.at_level(logging.WARNING):
        for _ in range(60):
            svc.start()
            clock.now += 2.0
        clock.now += go2rtc.RESTART_WINDOW_SECONDS + 1.0
        for _ in range(60):
            svc.start()
            clock.now += 2.0

    said = " ".join(r.getMessage() for r in caplog.records)
    assert "has now happened" in said, said


def test_giving_up_on_go2rtc_is_never_permanent(tmp_path: Path) -> None:
    """A binary that is replaced, a port that is freed, a config that is fixed:
    nobody visits this machine, so it has to come back on its own."""
    clock, spawned = FakeClock(), []
    svc = flapping_service(tmp_path, clock, spawned)
    for _ in range(30):
        svc.start()
        clock.now += 2.0
    assert svc.held_back is True
    stopped_at = len(spawned)

    clock.now += go2rtc.RESTART_WINDOW_SECONDS + 1.0
    assert svc.held_back is False
    svc.start()
    assert len(spawned) == stopped_at + 1, "it never tried again"


def test_a_go2rtc_that_ran_for_a_while_is_always_restarted(tmp_path: Path) -> None:
    """A server that streamed for an hour and then dropped is the ordinary life
    of this machine and must be restarted every time. Only the ones that were
    dead the first time anybody looked count against it."""
    clock, spawned = FakeClock(), []

    def spawn(command: list[str]) -> FakeProcess:
        spawned.append(command)
        return FakeProcess()

    svc = Go2rtcService(
        settings_with(("thermal", "rtsp://cam/t", True)),
        config_path=tmp_path / "go2rtc.json",
        binary=tmp_path / "go2rtc.exe",
        endpoint_path=tmp_path / "streaming.json",
        spawn=spawn,
        clock=clock,
    )
    for _ in range(20):
        svc.start()
        clock.now += 3600.0  # an hour of streaming
        svc._process.alive = False
        svc.ensure_running()
        clock.now += 3600.0

    assert svc.held_back is False, "an hour of service was counted as a stillbirth"
    assert len(spawned) >= 20


def test_repeated_identical_output_is_counted_rather_than_repeated(
    tmp_path: Path, caplog
) -> None:
    """A go2rtc that is up and refusing the camera's login writes the same line
    for ever. Keeping every copy is how the line that explains it is lost."""
    caplog.set_level(logging.INFO, logger="go2rtc")
    same = "[rtsp] 401 Unauthorized\n" * 200
    svc = talking_service(tmp_path, TalkingProcess(same))
    svc.start()

    assert wait_for_pump(
        lambda: any("has now happened" in r.getMessage() for r in caplog.records)
    ), "two hundred copies of one line, or nothing said about the repeats"
    written = [r for r in caplog.records if "401" in r.getMessage()]
    assert len(written) < 30, f"{len(written)} copies of one line reached the log"


# ------------------------------------------- a camera that gives one session
#
# The deadlock, with the real binary at both ends of it.
#
# The recorder starts first - the logon task guarantees that - finds no
# streaming server and reads straight from the camera. If that camera hands out
# one RTSP session at a time, every go2rtc started afterwards is refused BY THE
# CAMERA, and no amount of killing go2rtc, correcting the password or restarting
# the console can change it. That was the operator's second day.
#
# What is proved here and what is not. The camera in this test is a real go2rtc
# serving a generated pattern behind a proxy that gives out one session at a
# time, so what is proved is the mechanism: a camera with a session limit, a
# real go2rtc refused by it, the console noticing, and the picture arriving the
# moment the holder lets go. Whether the FLIR at the far end of that radio link
# behaves this way is not proved by anything that can run here - nothing in this
# repository can reach it. It remains the hypothesis that fits every round of
# this fault, and the console now reports the state rather than asserting the
# cause.


class OneSessionAtATime:
    """A camera that will give out exactly one RTSP session at a time.

    A byte proxy in front of a real RTSP server: the first connection is passed
    through, and anything arriving while that one is open is answered `453 Not
    Enough Bandwidth`, which is what RTSP has for a server with no session left
    to give. Loopback at both ends, and every thread here is a daemon.
    """

    def __init__(self, upstream_port: int) -> None:
        import threading

        self.upstream_port = upstream_port
        self.refused = 0
        self.sessions = 0
        self._closed = False
        self._busy = threading.Lock()
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self.port = self._listener.getsockname()[1]
        threading.Thread(target=self._accept, name="one-session-camera", daemon=True).start()

    def _accept(self) -> None:
        import threading

        while not self._closed:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        import threading

        if not self._busy.acquire(blocking=False):
            self.refused += 1
            with conn:
                try:
                    conn.settimeout(2.0)
                    conn.recv(4096)
                    conn.sendall(b"RTSP/1.0 453 Not Enough Bandwidth\r\nCSeq: 1\r\n\r\n")
                except OSError:
                    pass
            return
        self.sessions += 1
        try:
            with conn, socket.create_connection(
                ("127.0.0.1", self.upstream_port), timeout=5
            ) as upstream:
                done = threading.Event()

                def pump(source: socket.socket, sink: socket.socket) -> None:
                    try:
                        while not done.is_set():
                            block = source.recv(65536)
                            if not block:
                                break
                            sink.sendall(block)
                    except OSError:
                        pass
                    finally:
                        done.set()

                threads = [
                    threading.Thread(target=pump, args=pair, daemon=True)
                    for pair in ((conn, upstream), (upstream, conn))
                ]
                for thread in threads:
                    thread.start()
                done.wait(120)
        except OSError:
            pass
        finally:
            self._busy.release()

    def close(self) -> None:
        self._closed = True
        try:
            self._listener.close()
        except OSError:
            pass


def _bundled_ffmpeg() -> str | None:
    for name in ("ffmpeg.exe", "ffmpeg"):
        candidate = Path(__file__).resolve().parents[1] / "bin" / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg")


def _wait_for(predicate, seconds: float, step: float = 0.25) -> bool:
    """Bounded polling: a regression fails this test rather than hanging it."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return bool(predicate())


@pytest.mark.integration
def test_a_camera_already_held_leaves_go2rtc_with_no_picture(tmp_path: Path) -> None:
    """The whole fault, end to end, with the real binary at both ends."""
    import subprocess

    from vmd.streaming.endpoint import is_live

    binary = find_binary()
    ffmpeg = _bundled_ffmpeg()
    if binary is None or ffmpeg is None:
        pytest.skip("needs the go2rtc binary and ffmpeg")

    camera_api, camera_rtsp = _free_port(), _free_port()
    camera_config = tmp_path / "camera.json"
    camera_config.write_text(
        json.dumps(
            {
                "api": {"listen": f"127.0.0.1:{camera_api}"},
                "rtsp": {"listen": f"127.0.0.1:{camera_rtsp}"},
                "webrtc": {"listen": "", "ice_servers": []},
                "log": {"level": "warn"},
                "streams": {
                    "cam": (
                        f"exec:{ffmpeg} -hide_banner -re -f lavfi "
                        "-i testsrc=size=320x180:rate=15 -c:v libx264 -preset ultrafast "
                        "-tune zerolatency -g 15 -f rtsp {output}"
                    )
                },
            }
        ),
        encoding="utf-8",
    )
    camera = subprocess.Popen(
        [str(binary), "-c", str(camera_config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    limiter = OneSessionAtATime(camera_rtsp)
    holder = None
    svc = None
    try:
        assert _wait_for(
            lambda: is_live({"rtsp_port": camera_rtsp}, timeout=0.5), 20
        ), "the stand-in camera never came up"

        # The recorder, as the logon task leaves it: already reading the camera
        # itself, because when it started there was no streaming server.
        holder = subprocess.Popen(
            [
                ffmpeg, "-hide_banner", "-rtsp_transport", "tcp",
                "-i", f"rtsp://127.0.0.1:{limiter.port}/cam",
                "-t", "60", "-f", "null", "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert _wait_for(lambda: limiter.sessions >= 1, 20), "nothing took the session"

        # And now the console starts its streaming server on the same camera.
        settings = Settings(
            camera=CameraSettings(
                streams=[
                    StreamSettings(
                        name="thermal",
                        url=f"rtsp://127.0.0.1:{limiter.port}/cam",
                        enabled=True,
                    )
                ]
            )
        )
        svc = Go2rtcService(
            settings,
            config_path=tmp_path / "go2rtc.json",
            binary=binary,
            endpoint_path=tmp_path / "streaming.json",
            pid_path=tmp_path / "go2rtc.pid",
            api_port=_free_port(),
            rtsp_port=_free_port(),
        )
        svc.start()
        assert svc.wait_until_listening(10.0), "the streaming server never listened"
        endpoint = {"api_port": svc.api_port, "rtsp_port": svc.rtsp_port, "streams": {}}

        why = svc.unadoptable(endpoint)
        assert why, (
            "a streaming server the camera will not talk to was adopted, and "
            "every pane pointed at it"
        )
        assert "thermal" in why, why
        assert limiter.refused >= 1, "the camera never actually refused it"

        # The holder lets go - which is what standing the recorder down does -
        # and the same server can suddenly serve the same stream.
        holder.terminate()
        holder.wait(timeout=15)
        assert _wait_for(lambda: not svc.without_a_picture(), 30, step=1.0), (
            "the streaming server never got the camera even after it was free: "
            f"{svc.without_a_picture()}"
        )
        assert svc.unadoptable(endpoint) == ""
    finally:
        if holder is not None and holder.poll() is None:
            holder.kill()
        if svc is not None:
            svc.stop()
        limiter.close()
        camera.terminate()
        try:
            camera.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - a stubborn child
            camera.kill()
