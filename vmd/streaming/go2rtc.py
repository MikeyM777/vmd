"""Runs go2rtc, which turns the camera's RTSP into something a browser can play.

The camera speaks RTSP and no browser has ever played RTSP. go2rtc sits between
them: it holds one connection to the camera and re-serves it as WebRTC and as
fragmented MP4, so several browser tabs cost the camera and the radio link
nothing extra. On a 5 Mb/s link shared with recording, that is the difference
between working and not.

This module owns the process and the config file. It deliberately does not own
what the page plays - the page picks WebRTC or MP4 for itself.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import socket
import sys
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from vmd.settings import Settings

logger = logging.getLogger(__name__)
# Its own name in the Logs tab, so the camera's answers are distinguishable from
# ours at a glance.
stream_logger = logging.getLogger("go2rtc")

BINARY_NAMES = ("go2rtc.exe", "go2rtc")


def find_binary(project_root: Path | None = None) -> Path | None:
    """The go2rtc binary: beside the program first, then anywhere on PATH.

    The bundled copy wins. On the deployment laptop there is no internet and
    whatever the installer put in bin\\ is the version that was tested.
    """
    root = project_root or _project_root()
    for name in BINARY_NAMES:
        candidate = root / "bin" / name
        if candidate.is_file():
            return candidate
    for name in BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def free_port(preferred: int) -> int:
    """The preferred port if it is free, otherwise one the OS picks.

    Adding a listener to a program that was working is a good way to stop it
    working: go2rtc exits if any port it was told to bind is taken, and it takes
    the whole live picture with it. Nothing here is a fixed requirement - the
    console tells the page which ports to use.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _project_root() -> Path:
    """Where bin\\ lives: beside the executable when frozen, else the package parent."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def with_credentials(url: str, username: str, password: str) -> str:
    """Put the camera's username and password into an RTSP URL that lacks them.

    The operator types the address in one field and the credentials in another,
    which is the sane way round - but RTSP carries them in the URL, and a camera
    handed a bare rtsp://host/path answers "user/pass not provided" and nothing
    plays. So they are joined here.

    A URL that already carries its own credentials is left exactly as typed:
    what the operator wrote for that specific stream wins.
    """
    if not username and not password:
        return url
    parsed = urlsplit(url)
    if parsed.scheme not in ("rtsp", "rtsps", "http", "https"):
        return url  # exec: and other source kinds have no place for credentials
    if "@" in parsed.netloc or not parsed.hostname:
        return url

    # Percent-encoded: camera passwords contain @ : / and # often enough that
    # not encoding them produces a URL pointing at the wrong host entirely.
    credentials = quote(username, safe="") + ":" + quote(password, safe="")
    host = parsed.hostname
    if ":" in host:  # IPv6
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, f"{credentials}@{host}", parsed.path, parsed.query, parsed.fragment))


def build_config(settings: Settings, api_port: int, rtsp_port: int, webrtc_port: int = 8555) -> dict:
    """The go2rtc config for the streams the operator has enabled.

    Everything listens on loopback only. This machine is air-gapped and the
    console is for the person sitting at it; a streaming server reachable from
    the network is not a feature here, it is a hole.
    """
    streams = {
        stream.name: with_credentials(
            stream.url, settings.camera.username, settings.camera.password
        )
        for stream in settings.camera.streams
        if stream.enabled and stream.url
    }
    return {
        "api": {
            "listen": f"127.0.0.1:{api_port}",
            # The console runs on a different port, so its WebSocket to this
            # server is cross-origin and go2rtc refuses it with a 403 unless
            # told otherwise. That WebSocket is the WebRTC signalling channel -
            # without it there is no low-latency video. Both ends are bound to
            # loopback on an offline machine, so the only thing that can reach
            # this is something already running on it.
            "origin": "*",
        },
        # The RTSP listener is not for anyone else to connect to; go2rtc uses it
        # internally when a source has to be re-published. Loopback, always.
        "rtsp": {"listen": f"127.0.0.1:{rtsp_port}"},
        # WebRTC needs a listener to offer host candidates from. Loopback only:
        # the browser is on this machine and nothing else may reach the video.
        # Port 0 means "do not offer WebRTC at all", which is the fallback when
        # go2rtc will not start with it.
        "webrtc": {"listen": f"127.0.0.1:{webrtc_port}" if webrtc_port else ""},
        "log": {"level": "warn"},
        "streams": streams,
    }


def write_config(config: dict, path: Path) -> Path:
    """Write the config as JSON. go2rtc accepts JSON wherever it accepts YAML,
    and JSON removes a whole class of quoting bugs from RTSP URLs, which are
    full of colons, slashes, @ signs and passwords with punctuation in them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


@dataclass
class StreamingStatus:
    """What the console needs to know to show a picture, or to explain why not."""

    running: bool
    reason: str
    api_base: str
    streams: list[str]


class Go2rtcService:
    """Owns the go2rtc process. Shaped to fit the supervisor's Service protocol.

    `binary` is explicit, and None means "there isn't one" rather than "go and
    look". A service that searches the filesystem when handed None cannot be
    told that the binary is missing, which is exactly the state the console has
    to be able to report.  Use `find_binary()` at the call site.
    """

    def __init__(
        self,
        settings: Settings,
        config_path: Path,
        binary: Path | None,
        api_port: int = 1984,
        rtsp_port: int = 8554,
        webrtc_port: int = 8555,
        spawn=None,
    ) -> None:
        self.settings = settings
        self.config_path = Path(config_path)
        self.api_port = api_port
        self.rtsp_port = rtsp_port
        self.webrtc_port = webrtc_port
        self.binary = binary
        self._spawn = spawn or _default_spawn
        self._process: subprocess.Popen | None = None
        # Why it died, in its own words. A status line saying only "not running"
        # tells the operator nothing they can act on.
        self._recent: deque[str] = deque(maxlen=8)
        self._exit_code: int | None = None

    # ------------------------------------------------------------------ state

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def stream_names(self) -> list[str]:
        return [s.name for s in self.settings.camera.streams if s.enabled and s.url]

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    def status(self) -> StreamingStatus:
        """Why there is no picture, in words, rather than a blank panel."""
        if self.running:
            reason = "streaming"
        elif self.binary is None:
            reason = "go2rtc is not installed - run install.bat"
        elif not self.stream_names:
            reason = "no stream addresses set - enter them in Settings"
        else:
            last = next((line for line in reversed(self._recent) if line), "")
            code = self._exit_code
            reason = "the streaming server stopped"
            if code is not None:
                reason += f" (exit {code})"
            if last:
                reason += f": {last}"
        return StreamingStatus(self.running, reason, self.api_base, self.stream_names)

    def ensure_running(self) -> None:
        """Start it if it is not running. Called on every status poll.

        go2rtc can exit for reasons that have nothing to do with us - a port it
        wanted taken, a camera that hung up in a way it did not survive. Nothing
        was restarting it, so one exit meant no video until someone restarted
        the whole console.
        """
        if self.running or self.binary is None or not self.stream_names:
            return
        process = self._process
        if process is not None:
            self._exit_code = process.poll()
            self._process = None
            logger.warning("go2rtc exited with %s; restarting", self._exit_code)
        self.start()

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Start go2rtc. Doing nothing is the right answer when there is nothing
        to stream: an operator who has not entered a camera yet should see the
        console, not an error."""
        if self.running:
            return
        if self.binary is None:
            logger.warning("go2rtc binary not found; live video unavailable")
            return
        if not self.stream_names:
            logger.info("no enabled streams; not starting go2rtc")
            return

        # Ports are checked rather than assumed. A leftover go2rtc from a previous
        # run, or anything else on the machine, must not be able to leave the
        # console with no video at all.
        self.api_port = free_port(self.api_port)
        self.rtsp_port = free_port(self.rtsp_port)
        self.webrtc_port = free_port(self.webrtc_port)

        if not self._launch(with_webrtc=True):
            # WebRTC is the fast path, not the only one. If go2rtc will not come
            # up with it, come up without it and serve MP4 rather than nothing.
            logger.warning("go2rtc would not start with WebRTC enabled; retrying without it")
            self._launch(with_webrtc=False)

    def stop(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Never clear the handle while the process may still be alive:
                    # a forgotten go2rtc holds the camera connection and the next
                    # start would fight it for the port.
                    logger.error("go2rtc did not die; leaving it tracked")
                    return
        self._process = None

    def _launch(self, with_webrtc: bool) -> bool:
        """Spawn go2rtc and confirm it is still alive a moment later.

        A process that exits immediately is the failure that matters here, and
        Popen reports that as success. Waiting briefly turns "started" into
        "running", which is what the console actually claims on screen.
        """
        write_config(
            build_config(
                self.settings,
                self.api_port,
                self.rtsp_port,
                self.webrtc_port if with_webrtc else 0,
            ),
            self.config_path,
        )
        try:
            process = self._spawn([str(self.binary), "-c", str(self.config_path)])
        except OSError:
            logger.exception("could not start go2rtc")
            self._process = None
            return False

        self._process = process
        self._pump_output(process)
        time.sleep(0.8)
        code = process.poll()
        if code is not None:
            self._exit_code = code
            self._process = None
            logger.error(
                "go2rtc exited immediately (%s): %s", code, " | ".join(self._recent) or "no output"
            )
            return False

        logger.info(
            "go2rtc started on %s for %s%s",
            self.api_base,
            ", ".join(self.stream_names),
            "" if with_webrtc else " (WebRTC disabled)",
        )
        return True

    def sources(self) -> dict:
        """What go2rtc says about each stream: is the camera side connected?

        This separates two failures that look identical in the browser - the
        camera having dropped us, and the browser having stalled on a stream
        that is still arriving. Best effort: the console works without it.
        """
        if not self.running:
            return {}
        try:
            with urllib.request.urlopen(f"{self.api_base}/api/streams", timeout=2) as response:
                raw = json.loads(response.read().decode("utf-8", "replace"))
        except (OSError, ValueError):
            return {}
        summary: dict[str, dict] = {}
        for name, entry in (raw or {}).items():
            producers = (entry or {}).get("producers") or []
            summary[name] = {
                "connected": bool(producers),
                "consumers": len((entry or {}).get("consumers") or []),
            }
        return summary

    def _pump_output(self, process: subprocess.Popen) -> None:
        """Forward go2rtc's own output into the console log, on a daemon thread.

        A pipe nobody reads eventually fills and blocks the child, so this is not
        optional once stdout is a pipe.
        """
        stream = getattr(process, "stdout", None)
        if stream is None:
            return

        def pump() -> None:
            try:
                for line in stream:
                    text = line.rstrip()
                    if not text:
                        continue
                    self._recent.append(text)
                    lowered = text.lower()
                    if "err" in lowered or "unauthorized" in lowered or "401" in lowered:
                        stream_logger.warning("%s", text)
                    else:
                        stream_logger.info("%s", text)
            except Exception:  # noqa: BLE001 - the pump must never take the console with it
                logger.debug("go2rtc output pump stopped", exc_info=True)

        threading.Thread(target=pump, name="go2rtc-log", daemon=True).start()

    def apply(self, settings: Settings) -> None:
        """Take new settings. go2rtc reads its config once, so changing streams
        means restarting it - which is why this is one call and not two."""
        self.settings = settings
        was_running = self.running
        self.stop()
        if was_running or self.stream_names:
            self.start()


def _default_spawn(command: list[str]) -> subprocess.Popen:
    creation_flags = 0
    if os.name == "nt":
        # No console window: the operator double-clicked one thing and should get
        # one window, not a second black box they are afraid to close.
        creation_flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return subprocess.Popen(
        command,
        # Captured, not discarded. When the camera answers "401 Unauthorized" or
        # "connection refused", this is the only place that says so - and it has
        # to reach the operator's Logs tab, not /dev/null.
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        errors="replace",
        bufsize=1,
        creationflags=creation_flags,
    )
