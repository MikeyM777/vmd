"""Runs go2rtc, which holds the one connection this system makes to the camera.

It sits between the camera and everything on this machine that wants the
picture: it opens a single RTSP connection across the radio link and re-serves
it on loopback, so the console's video panes and the recorder together still
cost the camera and the link one stream. On a 5 Mb/s link shared with recording,
that is the difference between working and not.

Only the RTSP re-publisher is used. WebRTC and the fragmented-MP4 path existed
for the browser console, which no longer exists - the desktop console plays
rtsp://127.0.0.1 through VLC - and WebRTC is switched off in the config because
go2rtc's defaults would otherwise have it talking to public STUN servers from a
machine that is meant to be air-gapped.

This module owns the process and the config file.
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

# The longest single line kept from go2rtc. Matches the limit the console puts
# on the recorder and the detector, for the same reason.
MAX_LINE_CHARS = 2000

# How soon after being spawned a death counts as "it never started" rather than
# "it ran for a while and stopped". The console used to find this out by
# sleeping for it, on the thread that draws the window; now it is only the line
# it writes when the next tick finds the process gone.
SETTLE_SECONDS = 0.8


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


def source_for(stream, username: str, password: str) -> str:
    """The source string go2rtc is given for one stream.

    With reader "ffmpeg" the stream is read by ffmpeg rather than by go2rtc's
    own RTSP client - the same demuxer VLC is built on, which keeps reading
    through errors that a stricter client treats as the end of the stream.
    Copied, never re-encoded: the point is a more forgiving reader, not a
    different picture, and transcoding a 4K stream would cost more than it
    saves.
    """
    url = with_credentials(stream.url, username, password)
    if getattr(stream, "reader", "auto") == "ffmpeg" and url.startswith(("rtsp://", "rtsps://")):
        return f"ffmpeg:{url}#video=copy#audio=copy"
    return url


def probe_target(source: str) -> str:
    """The plain address inside a go2rtc source string.

    `source_for` wraps a stream in `ffmpeg:...#video=copy#audio=copy` when the
    operator picks the ffmpeg reader. That string is a go2rtc instruction, not a
    URL, so anything that hands it to `urlsplit` gets no host back - which is how
    both diagnostic tools came to answer "this address has no host in it" for
    exactly the stream that had been switched to ffmpeg because it was the one
    misbehaving. Unwrapping it here keeps the wrapper in one place.
    """
    if source.startswith("ffmpeg:"):
        # The options are appended by source_for after a '#'; nothing that can
        # legally appear before it survives quoting, because with_credentials
        # percent-encodes the credentials.
        return source[len("ffmpeg:") :].split("#", 1)[0]
    return source


def build_config(settings: Settings, api_port: int, rtsp_port: int) -> dict:
    """The go2rtc config for the streams the operator has enabled.

    Everything listens on loopback only, and nothing reaches outward. This
    machine is air-gapped and the console is for the person sitting at it; a
    streaming server reachable from the network is not a feature here, it is a
    hole, and one that dials out is worse.
    """
    streams = {
        stream.name: source_for(stream, settings.camera.username, settings.camera.password)
        for stream in settings.camera.streams
        if stream.enabled and stream.url
    }
    return {
        # No `origin` wildcard. It was here for the browser console's WebSocket,
        # which was cross-origin because the page came from a different port.
        # There is no page any more - the console is a desktop application and
        # VLC pulls RTSP from the loopback listener below - so the only thing a
        # wildcard still does is let anything else on this machine drive the
        # streaming server through a web page.
        "api": {"listen": f"127.0.0.1:{api_port}"},
        # The RTSP listener is what the console's VLC panes and the recorder
        # both read. Loopback, always.
        "rtsp": {"listen": f"127.0.0.1:{rtsp_port}"},
        # Off, and explicitly so. Nothing in the desktop console speaks WebRTC:
        # every pane plays rtsp://127.0.0.1. Left enabled, go2rtc's compiled-in
        # defaults have it gathering ICE candidates from Google's, Cloudflare's
        # and Amazon's STUN servers - outbound traffic from a machine that is
        # supposed to be air-gapped, for a transport nothing here uses. An empty
        # listen turns the module off; the empty `ice_servers` is belt and
        # braces, so that a future build which honours one but not the other
        # still cannot dial out.
        "webrtc": {"listen": "", "ice_servers": []},
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
        endpoint_path: Path | None = None,
        api_port: int = 1984,
        rtsp_port: int = 8554,
        spawn=None,
    ) -> None:
        self.settings = settings
        self.config_path = Path(config_path)
        # Where the ports it actually took are written down, so the recording
        # service - a separate process - can pull from here instead of opening
        # its own connection across the radio link.
        self.endpoint_path = Path(endpoint_path) if endpoint_path else self.config_path.parent / "streaming.json"
        self.api_port = api_port
        self.rtsp_port = rtsp_port
        self.binary = binary
        self._spawn = spawn or _default_spawn
        self._process: subprocess.Popen | None = None
        # Why it died, in its own words. A status line saying only "not running"
        # tells the operator nothing they can act on.
        self._recent: deque[str] = deque(maxlen=8)
        self._exit_code: int | None = None
        # When the process now held was spawned, so that a death found on a
        # later tick can still be told apart from one that never started.
        self._launched_at = 0.0

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
        self._reap()
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
        self._reap()
        if self.running or self.binary is None or not self.stream_names:
            return
        self.start()

    def _reap(self) -> None:
        """Notice, and say, that the process we launched has gone.

        This is what used to be a `time.sleep(0.8)` inside the launch itself.
        The launch is called by the supervisor's tick, and the supervisor ticks
        on the thread that draws the window, so a go2rtc that exits immediately
        - a corrupt binary, a config it will not parse - held that thread for
        0.8 s out of every 2 s for as long as the console stayed open. Nothing
        repainted for two fifths of the operator's day, and while nothing
        repaints the alarm strip cannot appear.

        Asking afterwards costs nothing and answers the same question, one tick
        later. The endpoint file goes with it: a streaming.json naming a port
        that nothing is listening on would have the recorder believe there is a
        local copy of the stream to read, and open its own connection to the
        camera when it found otherwise.
        """
        process = self._process
        if process is None:
            return
        code = process.poll()
        if code is None:
            return
        self._exit_code = code
        self._process = None
        self._clear_endpoint()
        if time.monotonic() - self._launched_at < SETTLE_SECONDS:
            logger.error(
                "go2rtc exited immediately (%s): %s",
                code,
                " | ".join(self._recent) or "no output",
            )
        else:
            logger.warning("go2rtc exited with %s; restarting", code)

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Start go2rtc. Doing nothing is the right answer when there is nothing
        to stream: an operator who has not entered a camera yet should see the
        console, not an error."""
        self._reap()
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
        # console with no video at all: go2rtc exits if any port it was told to
        # bind is taken, which is what a third listener once did here.
        self.api_port = free_port(self.api_port)
        self.rtsp_port = free_port(self.rtsp_port)

        # One attempt, because there is now only one configuration to try. The
        # second attempt existed to come up with WebRTC disabled after a first
        # try with it enabled had failed; WebRTC is disabled outright now, so
        # retrying the identical config would only spend another second of the
        # console's heartbeat failing the same way.
        self._launch()

    def stop(self) -> None:
        self._clear_endpoint()
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

    def _launch(self) -> bool:
        """Spawn go2rtc and write down where it will be listening.

        Whether it stayed up is asked on the next tick, by `_reap`, and never
        waited for here - see that method for what waiting cost.

        The endpoint is written now rather than after any confirmation because
        the recorder is started moments later in the same breath and reads this
        file once. A file written a tick later is a recorder that opened its own
        connection to the camera, which doubles what crosses the radio link and
        is the one cost this whole arrangement exists to avoid. A file left
        behind by a go2rtc that did not stay up is cleared by `_reap`, and the
        recorder probes the port before believing it either way.
        """
        write_config(
            build_config(self.settings, self.api_port, self.rtsp_port),
            self.config_path,
        )
        try:
            process = self._spawn([str(self.binary), "-c", str(self.config_path)])
        except OSError:
            logger.exception("could not start go2rtc")
            self._process = None
            return False

        self._process = process
        self._launched_at = time.monotonic()
        self._pump_output(process)
        self._write_endpoint()
        logger.info(
            "go2rtc started on %s for %s", self.api_base, ", ".join(self.stream_names)
        )
        return True

    def local_rtsp_url(self, name: str) -> str:
        """Where this machine can get the stream without touching the camera."""
        return f"rtsp://127.0.0.1:{self.rtsp_port}/{name}"

    def _write_endpoint(self) -> None:
        payload = {
            "api_port": self.api_port,
            "rtsp_port": self.rtsp_port,
            "streams": {name: self.local_rtsp_url(name) for name in self.stream_names},
        }
        try:
            self.endpoint_path.parent.mkdir(parents=True, exist_ok=True)
            self.endpoint_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            # Not fatal: the recorder falls back to the camera, which costs
            # bandwidth but still records.
            logger.warning("could not write %s", self.endpoint_path, exc_info=True)

    def _clear_endpoint(self) -> None:
        try:
            self.endpoint_path.unlink(missing_ok=True)
        except OSError:
            pass

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

        Every line is tagged with "go2rtc", because the Logs tab shows the
        message and not the logger it came from, and "401 Unauthorized" from
        nowhere is a line the operator cannot act on. Lines are also cut to a
        length: go2rtc does not normally write a line a megabyte long, but the
        ring buffer's capacity is no defence against one that does.
        """
        stream = getattr(process, "stdout", None)
        if stream is None:
            return

        def pump() -> None:
            try:
                for line in stream:
                    text = line.rstrip()[:MAX_LINE_CHARS]
                    if not text:
                        continue
                    self._recent.append(text)
                    lowered = text.lower()
                    if "err" in lowered or "unauthorized" in lowered or "401" in lowered:
                        stream_logger.warning("go2rtc: %s", text)
                    else:
                        stream_logger.info("go2rtc: %s", text)
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
