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
