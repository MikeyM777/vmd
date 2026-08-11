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

import hashlib
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

from vmd.background import BackgroundValue
from vmd.settings import Settings
from vmd.streaming.endpoint import is_live

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

# ------------------------------------------------------- when to stop trying
#
# This was the one supervised child in the codebase with no rule for giving up.
# `Supervisor.tick` calls start() on anything that is not running, every two
# seconds; a go2rtc that exits immediately - a half-copied binary, a config it
# will not parse, a port collision it loses - was therefore spawned every two
# seconds for months, and _reap wrote an ERROR on every one of those cycles.
#
# Thirty lines a minute into the console's 500-line ring empties the Logs tab of
# everything else in about seventeen minutes. That is not merely noisy: every
# other subsystem's careful reporting is written into that same buffer, and this
# erases it. The line explaining a mistyped camera password was lost exactly
# this way and it cost the owner hours.
#
# The same numbers as everything else here, deliberately: five starts inside two
# minutes, which is RESTART_LIMIT / RESTART_WINDOW_SECONDS in
# vmd\storage\recorder.py and SPAWN_LIMIT / FLAP_WINDOW in
# vmd\desktop\services.py. One rule with three copies of the constants is
# already the codebase's shape here; three different rules would be worse.
#
# Bounded by a window rather than latched, for the reason ffmpeg's is: giving up
# must never be permanent on a machine nobody visits. A binary that is replaced,
# a port that is freed, a config that is corrected - each comes back on its own
# two minutes later, with no one doing anything.
RESTART_WINDOW_SECONDS = 120.0
RESTART_LIMIT = 5

# How a line that keeps being written is kept without being repeated: said in
# full the first few times, then rarely and with the count on it. Dropping the
# repeats silently is the other way to lose the diagnosis - the operator reads
# three lines and believes it happened three times - so the number is what is
# said, and "this has now happened 60 times" is the diagnosis rather than sixty
# copies of one sentence.
SAID_IN_FULL = 3
SAID_EVERY = 20


class RepeatedLine:
    """One line's worth of memory: what was last said, and how often since.

    Owned by one thread. `_reap`'s lives on the service and is only touched from
    the thread that ticks the supervisor; the log pump's is created per launch
    and lives on that pump's own stack, so the two never share state and this
    adds no lock to a class that already has a deque crossing threads.
    """

    def __init__(self, in_full: int = SAID_IN_FULL, then_every: int = SAID_EVERY) -> None:
        self._text: str | None = None
        self._count = 0
        self._in_full = in_full
        self._then_every = max(then_every, 1)

    def seen(self, text: str) -> int | None:
        """How many times this text has now been seen in a row, or None to keep quiet."""
        if text != self._text:
            self._text = text
            self._count = 1
            return 1
        self._count += 1
        if self._count <= self._in_full or self._count % self._then_every == 0:
            return self._count
        return None

# ---------------------------------------------------------------- the claim
#
# Which process is serving the video.
#
# streaming.json carries the ports and no PID, and stop() only ever stopped a
# process object this service was holding - so a go2rtc adopted from an earlier
# console could not be stopped at all. A settings change left it running and
# started a SECOND go2rtc on a different port: a second connection across the
# radio link, which is the one cost this whole arrangement exists to avoid.
#
# Shaped after the recorder's claim in vmd\record_main.py, and read that file
# before changing this one. The difference is who writes it: the recorder writes
# its own, and go2rtc is somebody else's binary that cannot, so the console
# writes it on go2rtc's behalf and clears it when it stops one it started.
#
# The file holds a bare integer and nothing else. That is the recorder's rule
# and it is here for the recorder's reason: several readers parse a claim file
# as a whole number, and any of them failing to parse reads as "nothing is
# running", whose remedy is to start a second one. Everything that will not fit
# in an integer goes in a companion beside it, and a reader that does not know
# about the companion is left exactly as well off as it was.
PID_FILENAME = "go2rtc.pid"
IDENTITY_SUFFIX = ".json"

# How long a forced stop waits for an adopted server to disappear. Short,
# because taskkill /F has already returned by the time it is checked at all, and
# because it runs while the operator waits for a Save to finish.
ADOPTED_STOP_SECONDS = 2.0

# How often "is that adopted server still there?" is asked again. Asking costs a
# `tasklist`; the answer is read on a thread of its own, and this is the most it
# may be behind.
LIVENESS_SECONDS = 2.0

# What a live process must be running for the claim to be believed when the
# companion does not say. go2rtc is one binary with one name.
GO2RTC_IMAGES = ("go2rtc.exe", "go2rtc")

# How long one stream is given to prove it has a picture in it, and how the
# proof is made.
#
# DESCRIBE is the first thing VLC sends and the first thing that fails: the
# operator's `Failed to setup RTSP session` is what the pane makes of go2rtc
# answering `404 Not Found` to this exact request. It is also the request that
# makes go2rtc go to the camera - measured against the bundled 1.9.14, a
# DESCRIBE for a stream nothing had subscribed to produced `wrong user/pass` in
# the server's own log within a hundredth of a second - which is why it can tell
# "idle" from "broken" when `/api/streams` cannot. That list carries a producer
# with a URL in it and nothing else whether the producer has ever connected or
# not, so reading it for health would be the same mistake one layer down.
#
# Three seconds, and every stream is asked at once rather than in turn. This is
# paid at console start, in front of a window with no picture in it yet; a
# healthy loopback answers in about half a second, a refusal in a hundredth, and
# the bound is what a camera at the end of a >15 km radio link is given before
# the console stops waiting and starts a server of its own.
PROBE_TIMEOUT = 3.0
PROBE_AGENT = "vmd-console"

# How long a server that has just been spawned is given to bind its ports before
# the console stops waiting for it. Everything asked of it before that answers
# "the connection failed", which is a true sentence about the wrong thing.
LISTENING_SECONDS = 2.0

# How long anything is given to answer the API on the loopback. Two seconds is
# a long time for a local socket and is paid twice at most: once while the
# console decides whether the server from the last run can be adopted, and
# never on the heartbeat. A shorter wait on a laptop that is busy starting four
# ffmpegs would read a slow answer as no answer, and the answer to that is to
# stop a working streaming server and start another.
API_TIMEOUT = 2.0


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


def rtsp_describe(port: int, name: str, timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """Ask the streaming server on the loopback for one stream, as VLC would.

    True when the server answered with a description of a stream it is holding,
    which it can only do once it has the camera's tracks - so this is a picture
    proved rather than a name recognised. False, and what it said instead, for
    everything else: a refusal, a connection nothing accepted, an answer that
    never came.

    Only DESCRIBE, and no SETUP or PLAY after it. Reading an actual packet would
    be the fuller proof and it costs a keyframe interval per stream, which on
    this camera is seconds of blank window at every console start; DESCRIBE
    already forces the connection to the camera, which is the part that fails.
    That is the whole of the difference between this and `vmd/detect/runner.py`,
    which reads a frame because it is about to read every frame after it.

    127.0.0.1 always. This asks about a server on this machine and there is no
    argument for it to reach anywhere else.
    """
    request = (
        f"DESCRIBE rtsp://127.0.0.1:{int(port)}/{name} RTSP/1.0\r\n"
        f"CSeq: 1\r\nAccept: application/sdp\r\nUser-Agent: {PROBE_AGENT}\r\n\r\n"
    ).encode("utf-8")
    answer = b""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            while b"\r\n\r\n" not in answer and len(answer) < 8192:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                answer += chunk
    except (OSError, ValueError) as exc:
        return False, f"the connection failed: {exc}"
    first = answer.split(b"\r\n", 1)[0].decode("utf-8", "replace").strip()
    if not first:
        return False, "it accepted the connection and then said nothing"
    parts = first.split()
    if len(parts) >= 2 and parts[1] == "200":
        return True, first
    return False, first


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

    The video and nothing else. Nothing on this machine has ever listened to the
    camera's audio: the panes pass --no-audio to libVLC and the recorder passes
    -an to ffmpeg, because the camera sends pcm_mulaw, which is what MP4 could
    not carry and what stopped recording for a day. Asking for it here would
    pull a track across a radio link with five megabits on it so that both ends
    could throw it away.
    """
    url = with_credentials(stream.url, username, password)
    if getattr(stream, "reader", "auto") == "ffmpeg" and url.startswith(("rtsp://", "rtsps://")):
        return f"ffmpeg:{url}#video=copy"
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


def config_fingerprint(config: dict) -> str:
    """A stable summary of the streams a server was started with.

    This is what makes "that server is running settings that have been
    replaced" answerable at all. go2rtc reads its configuration once, when it
    starts, and this console rewrites go2rtc.json on every start - so the file
    beside the running process describes the console that is opening now, not
    the server that is already up. Nothing on the machine remembered what that
    server had actually been given, so a corrected password looked exactly like
    a correct one.

    Not asked of the server. `/api/config` looks like the answer and is not:
    measured against the bundled go2rtc 1.9.14, it re-reads the file from disk
    and reported a password corrected and a stream added seconds earlier, both
    of which the running process had never seen. It agrees with whatever this
    console last wrote, which is the definition of a check that proves nothing.

    Only `streams`, because only `streams` is about the camera: the two ports
    are chosen at launch from whatever is free and differ between two identical
    consoles. A digest rather than the text, because the text is a camera
    password and this file is not the one place that has to hold it.
    """
    streams = config.get("streams") if isinstance(config, dict) else None
    payload = json.dumps(streams or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def without_credentials(source: str) -> str:
    """The same source string with any username and password taken out.

    Used on both sides of every comparison with what the API reports, and that
    is the whole reason it exists: go2rtc 1.9.14 hands back the producer URL
    exactly as configured, password and all, but a version that redacted it
    would otherwise disagree with every correct configuration for ever - and
    what this console does with a server it disagrees with is stop it and start
    another. A check that cannot be wrong about the secret is worth more than
    one that notices a changed password, which is what the fingerprint above and
    the DESCRIBE probe are for.
    """
    prefix, _, rest = source.partition(":") if source.startswith("ffmpeg:") else ("", "", source)
    head = f"{prefix}:" if prefix else ""
    url, hash_sign, options = rest.partition("#")
    try:
        parsed = urlsplit(url)
        if "@" not in parsed.netloc:
            return source
        bare = parsed.netloc.split("@", 1)[1]
        url = urlunsplit((parsed.scheme, bare, parsed.path, parsed.query, parsed.fragment))
    except ValueError:  # not a URL at all - a file path, or something odd
        return source
    return head + url + hash_sign + options


def write_config(config: dict, path: Path) -> Path:
    """Write the config as JSON. go2rtc accepts JSON wherever it accepts YAML,
    and JSON removes a whole class of quoting bugs from RTSP URLs, which are
    full of colons, slashes, @ signs and passwords with punctuation in them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


@dataclass(frozen=True)
class StreamingClaim:
    """Enough about the claiming process to tell it from a recycled PID."""

    pid: int
    executable: str = ""
    api_port: int = 0
    rtsp_port: int = 0
    written_at: float = 0.0
    # What that process was actually given to serve; see `config_fingerprint`.
    # Empty whenever it is not known - a claim written by a console older than
    # this - which leaves the next console exactly as well off as it was.
    streams_fingerprint: str = ""

    def as_dict(self) -> dict:
        return {
            "pid": self.pid,
            "executable": self.executable,
            "api_port": self.api_port,
            "rtsp_port": self.rtsp_port,
            "written_at": self.written_at,
            "streams_fingerprint": self.streams_fingerprint,
        }


def identity_path(pid_path: str | Path) -> Path:
    return Path(str(pid_path) + IDENTITY_SUFFIX)


def read_claim(pid_path: str | Path) -> int | None:
    """The number in the claim file, or None if there is not one."""
    try:
        return int(Path(pid_path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def read_claim_details(pid_path: str | Path) -> StreamingClaim | None:
    """What the console that wrote the claim said about it, if anything.

    None whenever the companion is missing or unusable, which includes every
    claim written by a console older than this. Callers treat that as "less is
    known", never as "the claim is bad".
    """
    try:
        payload = json.loads(identity_path(pid_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return StreamingClaim(
            pid=int(payload["pid"]),
            executable=str(payload.get("executable") or ""),
            api_port=int(payload.get("api_port") or 0),
            rtsp_port=int(payload.get("rtsp_port") or 0),
            written_at=float(payload.get("written_at") or 0.0),
            streams_fingerprint=str(payload.get("streams_fingerprint") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def boot_time() -> float | None:
    """When this machine last started, in epoch seconds, or None if unknown.

    A claim written before the last boot names a PID that cannot still be its
    process, whatever is holding that number now - and an always-on laptop at
    the end of a radio link will certainly see a power cut. Spelled out here
    rather than imported from the recorder: this module has to be importable by
    a console on a laptop where the recorder's stack is not installed.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        return time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:  # noqa: BLE001 - an unknown boot time is not a failure
        return None


def process_image(pid: int) -> str | None:
    """The executable name of a live process, None if there is none, "" if the
    question could not be answered - which is not the same thing."""
    if os.name != "nt":  # pragma: no cover - not the deployment platform
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return "go2rtc"
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith('"'):
            return line.split('","')[0].strip('"')
    return None  # "INFO: No tasks are running which match the specified criteria."


def taskkill_tree(pid: int) -> bool:
    """End a process and everything under it. True if the request was accepted."""
    if os.name != "nt":  # pragma: no cover - not the deployment platform
        return False
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("taskkill could not be run for pid %s", pid, exc_info=True)
        return False
    if result.returncode != 0:
        logger.warning(
            "taskkill refused pid %s: %s", pid, (result.stderr or result.stdout).strip()
        )
        return False
    return True


@dataclass
class StreamingStatus:
    """What the console needs to know to show a picture, or to explain why not."""

    running: bool
    reason: str
    api_base: str
    streams: list[str]
    # It is not running AND nothing is going to start it until the flapping
    # quietens down. A default, so that nothing constructing one of these has to
    # know about it; `reason` says the same thing in words.
    held_back: bool = False


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
        pid_path: Path | None = None,
        image_of=None,
        kill_tree=None,
        booted=None,
        clock=None,
    ) -> None:
        self.settings = settings
        # Only the flapping window and the settle check read this. The two
        # bounded waits for an adopted process to die stay on the real clock:
        # they sleep, and a clock a caller has frozen would turn a two-second
        # wait into a loop that never ends.
        self._clock = clock or time.monotonic
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
        # Whether anything has seen the process now held actually running.
        self._seen_running = False
        # When each of those never-started launches happened; see `held_back`.
        self._stillbirths: list[float] = []
        self._said_held_back = False
        # The last immediate-death line and how often it has been written.
        self._said_about_dying = RepeatedLine()
        # Where this console writes down which process is serving the video.
        self.pid_path = Path(pid_path) if pid_path else self.config_path.parent / PID_FILENAME
        self._image_of = image_of or process_image
        self._kill_tree = kill_tree or taskkill_tree
        self._booted = booted or boot_time
        # A server this console did not start. `_adopted_pid` is None while the
        # process behind it could not be named, which is a state of its own:
        # nothing may start a second one, and nothing can stop this one either.
        self._adopted = False
        self._adopted_pid: int | None = None
        self._adopted_alive: BackgroundValue[bool] | None = None

    # ------------------------------------------------------------------ state

    @property
    def running(self) -> bool:
        """Whether video is being served, by us or by a server we adopted.

        An adopted server counts. Without that the supervisor - which starts
        anything that is not running, every two seconds - started a second
        go2rtc on top of the live one on the very first tick, which is a second
        connection to the camera across a link that barely carries one.

        Never waits for the operating system: asking about a PID means shelling
        out to `tasklist`, and this is read from the heartbeat.
        """
        if self._process is not None:
            alive = self._process.poll() is None
            if alive:
                # Somebody looked and it was there. That, and nothing else, is
                # what tells a server which ran and stopped from one that was
                # already gone the first time anyone asked - which is the
                # difference `held_back` is measured on. Shaped after
                # `SegmentRecorder._seen_running`, for the same reason.
                self._seen_running = True
            return alive
        if not self._adopted:
            return False
        watch = self._adopted_alive
        if watch is None:
            return True
        reading = watch.get()
        # An unanswerable question reads as "still there". Believing a live
        # server is gone starts a second one beside it, which is the collision
        # this whole claim exists to prevent.
        return True if reading.value is None else bool(reading.value)

    @property
    def held_back(self) -> bool:
        """Has go2rtc died on arrival so often, so recently, that starting it
        again is no longer worth doing? See RESTART_LIMIT.

        Only launches that were gone before anyone looked count. A server that
        streamed for an hour and then dropped is the ordinary life of this
        machine and must be restarted every time; one that is dead every time it
        is looked at has something wrong with it that another attempt will not
        fix, and every one of those attempts costs the operator's log.
        """
        cutoff = self._clock() - RESTART_WINDOW_SECONDS
        self._stillbirths = [at for at in self._stillbirths if at >= cutoff]
        return len(self._stillbirths) >= RESTART_LIMIT

    @property
    def adopted(self) -> bool:
        """Is what is serving video a server this console did not start?"""
        return self._adopted

    def claimed_pid(self) -> int | None:
        """The pid of a go2rtc that really is running, or None.

        "A process with that number exists" is a different claim from "go2rtc is
        running". The claim file survives `taskkill /F` and it survives a power
        cut, and Windows hands the same numbers out again - so a stale file plus
        an unrelated program wearing the recycled number reads as a healthy
        streaming server to anything that only asks whether the PID is alive.
        """
        pid = read_claim(self.pid_path)
        if pid is None or pid <= 0:
            return None
        details = read_claim_details(self.pid_path)
        machine_started = self._booted()
        if details and details.written_at and machine_started:
            if details.written_at < machine_started:
                logger.info(
                    "%s names pid %s but was written before this machine last "
                    "started, so it is left over from an earlier boot",
                    self.pid_path,
                    pid,
                )
                return None
        image = self._image_of(pid)
        if image is None:
            logger.info("%s names pid %s, which is not running", self.pid_path, pid)
            return None
        if image == "":
            # The process list could not be read. Nothing is proven either way,
            # and the safe reading is that go2rtc is up: one console not being
            # able to restart it costs the settings change, and starting a
            # second one costs the link.
            return pid
        expected = Path(details.executable).name if details and details.executable else ""
        if not expected and self.binary is not None:
            expected = Path(self.binary).name
        if expected:
            if image.lower() != expected.lower():
                logger.info(
                    "%s names pid %s, but that is %s and go2rtc is %s - the "
                    "number has been given to something else",
                    self.pid_path,
                    pid,
                    image,
                    expected,
                )
                return None
        elif image.lower() not in GO2RTC_IMAGES:
            logger.info(
                "%s names pid %s, which is %s and cannot be go2rtc",
                self.pid_path,
                pid,
                image,
            )
            return None
        return pid

    @property
    def stream_names(self) -> list[str]:
        return [s.name for s in self.settings.camera.streams if s.enabled and s.url]

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    def _last_words(self) -> str:
        """The last thing go2rtc said, or a stand-in. Never empty."""
        return next((line for line in reversed(self._recent) if line), "") or "nothing"

    def status(self) -> StreamingStatus:
        """Why there is no picture, in words, rather than a blank panel."""
        self._reap()
        held_back = self.held_back
        if self.running:
            reason = "streaming"
        elif self.binary is None:
            reason = "go2rtc is not installed - run install.bat"
        elif not self.stream_names:
            reason = "no stream addresses set - enter them in Settings"
        elif held_back:
            # Deliberately not "the streaming server stopped", which reads as
            # something being done about it. Nothing is being done about it
            # until the flapping quietens down, and saying so is the point.
            reason = (
                f"the streaming server is NOT running: it has exited immediately "
                f"{len(self._stillbirths)} times in the last "
                f"{RESTART_WINDOW_SECONDS / 60.0:.0f} minutes and is not being "
                f"started again until that has quietened down. It said: "
                f"{self._last_words()}"
            )
        else:
            last = next((line for line in reversed(self._recent) if line), "")
            code = self._exit_code
            reason = "the streaming server stopped"
            if code is not None:
                reason += f" (exit {code})"
            if last:
                reason += f": {last}"
        return StreamingStatus(
            self.running, reason, self.api_base, self.stream_names, held_back
        )

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
        now = self._clock()
        # Never seen alive, or gone before it could have served a frame. Asked
        # both ways because neither is enough on its own: the supervisor's tick
        # is two seconds and SETTLE_SECONDS is under one, so a launch whose
        # death is only noticed on the next tick would look like a server that
        # had run for a while - and it is the ones that were already gone the
        # first time anybody looked that must stop being started.
        never_started = not self._seen_running or (now - self._launched_at) < SETTLE_SECONDS
        if not never_started:
            logger.warning("go2rtc exited with %s; restarting", code)
            return
        self._stillbirths.append(now)
        # Said in full the first few times and then rarely, with the count, so
        # that a go2rtc dying identically every two seconds cannot empty the
        # Logs tab of everything that explains why. See RepeatedLine.
        said = f"go2rtc exited immediately ({code}): " + (
            " | ".join(self._recent) or "no output"
        )
        times = self._said_about_dying.seen(said)
        if times is None:
            return
        if times == 1:
            logger.error("%s", said)
        else:
            logger.error("%s - this has now happened %d times", said, times)

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
        if self.held_back:
            # Said once per spell rather than once per tick: this is reached
            # every two seconds for as long as it lasts, and a sentence repeated
            # every two seconds is the same fault as the flood it is describing.
            if not self._said_held_back:
                self._said_held_back = True
                stream_logger.error(
                    "go2rtc: the streaming server has exited immediately %d times "
                    "in the last %.0f minutes, so it is NOT running and is not "
                    "being started again until that has quietened down. There is "
                    "no live picture and no local copy of the streams for the "
                    "recorder to read. It said: %s",
                    len(self._stillbirths),
                    RESTART_WINDOW_SECONDS / 60.0,
                    self._last_words(),
                )
            return
        self._said_held_back = False

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

    def adopt(self, endpoint: dict) -> bool:
        """Take on the go2rtc that is already serving video, PID and all.

        Adoption is why this console does not start a second streaming server
        when one is already up. What it could not do until now is stop the one
        it adopted, because streaming.json carries ports and no PID - so a
        settings change left it running and started a second one on a different
        port, which is a second connection across the radio link.

        A server nobody can name is still adopted. Stopping it is impossible
        either way, and starting a second one on top of it is worse: the honest
        answer is to say so, keep serving its pictures, and tell the operator
        that a save could not reach it.
        """
        self.api_port = int(endpoint.get("api_port", self.api_port))
        self.rtsp_port = int(endpoint.get("rtsp_port", self.rtsp_port))
        self._adopted = True
        pid = self.claimed_pid()
        self._adopted_pid = pid
        self._forget_adopted_watch()
        if pid is None:
            logger.warning(
                "a streaming server is already running, but nothing on disk says "
                "which process it is, so this console cannot stop it: a settings "
                "change will leave the live picture on the settings it was started "
                "with. Close every console and start one again to clear this."
            )
            return True
        self._adopted_alive = BackgroundValue(
            read=lambda: self._image_of(pid) is not None,
            stale_after=LIVENESS_SECONDS,
            name="whether the adopted go2rtc is still running",
            seed=True,
        )
        return True

    def _forget_adopted_watch(self) -> None:
        watch, self._adopted_alive = self._adopted_alive, None
        if watch is not None:
            watch.close()

    def _stop_adopted(self) -> bool:
        """End a server this console did not start. True once it is gone."""
        pid = self._adopted_pid
        if pid is None:
            stream_logger.error(
                "go2rtc: the streaming server from an earlier run cannot be "
                "stopped, because nothing on disk says which process it is, so "
                "the settings you saved are NOT in effect for the live picture"
            )
            return False
        logger.warning(
            "stopping the adopted go2rtc (pid %s), because the settings it was "
            "started with have been replaced",
            pid,
        )
        self._kill_tree(pid)
        # Bounded, and short. taskkill /F has already returned by the time this
        # runs; the loop is here because this decides whether a second go2rtc
        # may be started on the same camera, and that must never be guessed.
        deadline = time.monotonic() + ADOPTED_STOP_SECONDS
        while self._image_of(pid) is not None and time.monotonic() < deadline:
            time.sleep(0.1)
        if self._image_of(pid) is not None:
            # Deliberately stays adopted, so `running` stays True and nothing
            # starts a second one on top of it. Two go2rtcs on one camera is
            # worse than a setting that did not apply.
            stream_logger.error(
                "go2rtc: the streaming server from an earlier run (pid %s) would "
                "not stop, so nothing was started in its place and the settings "
                "you saved are NOT in effect for the live picture",
                pid,
            )
            return False
        self._forget_adopted_watch()
        self._adopted = False
        self._adopted_pid = None
        self._clear_claim(pid)
        self._clear_endpoint()
        return True

    def stop(self, force: bool = False) -> None:
        """Stop the streaming server this console started.

        An adopted one is left alone unless `force` is given, exactly as an
        adopted recorder is. It belongs to a console that is gone, and it is
        feeding the recorder as well as this window - stopping it here would
        stop the picture and the footage because somebody closed a second
        window. `force` is what a settings change uses, and the distinction is
        the point: a Save is an explicit instruction to change how the system
        runs, and a server on settings the operator has just replaced is not
        worth protecting.
        """
        if self._process is None and self._adopted:
            if not force:
                return
            self._stop_adopted()
            return

        process = self._process
        if process is None:
            self._clear_endpoint()
            return
        pid = getattr(process, "pid", None)
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
        self._clear_endpoint()
        if pid is not None:
            self._clear_claim(pid)

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
        config = build_config(self.settings, self.api_port, self.rtsp_port)
        write_config(config, self.config_path)
        try:
            process = self._spawn([str(self.binary), "-c", str(self.config_path)])
        except OSError:
            logger.exception("could not start go2rtc")
            self._process = None
            return False

        self._process = process
        self._launched_at = self._clock()
        self._seen_running = False
        self._adopted = False
        self._adopted_pid = None
        self._forget_adopted_watch()
        self._pump_output(process)
        self._write_endpoint()
        # The config it was handed, not the one on disk: this file is rewritten
        # by the next console before it asks any of these questions.
        self._write_claim(getattr(process, "pid", None), config_fingerprint(config))
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

    def _write_claim(self, pid: int | None, fingerprint: str = "") -> None:
        """Say which process is serving the video, so the next console can stop it.

        The bare number first and on its own, because that is what every reader
        of a claim file needs; the companion is only what makes the check
        sharper, and a companion that could not be written leaves the next
        console exactly as well off as it was before this existed.
        """
        if pid is None:
            return
        claim = StreamingClaim(
            pid=int(pid),
            executable=str(self.binary or ""),
            api_port=self.api_port,
            rtsp_port=self.rtsp_port,
            written_at=time.time(),
            streams_fingerprint=fingerprint,
        )
        try:
            self.pid_path.parent.mkdir(parents=True, exist_ok=True)
            self.pid_path.write_text(str(claim.pid), encoding="utf-8")
        except OSError:
            logger.warning("could not write %s", self.pid_path, exc_info=True)
            return
        try:
            identity_path(self.pid_path).write_text(
                json.dumps(claim.as_dict(), indent=2), encoding="utf-8"
            )
        except OSError:
            logger.warning(
                "could not write %s", identity_path(self.pid_path), exc_info=True
            )

    def _clear_claim(self, pid: int) -> None:
        """Drop the claim, but only while it still names that process.

        Something else may have taken it over in the meantime - another console
        starting its own go2rtc - and deleting that claim would let the next one
        start a second server beside a live one.
        """
        if read_claim(self.pid_path) != pid:
            return
        for path in (identity_path(self.pid_path), self.pid_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:  # noqa: PERF203 - shutdown must always complete
                logger.warning("could not remove %s", path, exc_info=True)

    def api_streams(self, api_port: int | None = None, timeout: float = API_TIMEOUT) -> dict | None:
        """What the server on that port says it is serving, or None if it did not say.

        None and {} are different answers and the caller has to be able to tell
        them apart: {} is a go2rtc serving nothing, None is nothing that answers
        at all. Adoption turns on exactly that difference.

        No `running` guard, deliberately. This is asked of a server this console
        has not adopted yet and may never adopt, which is the whole point: the
        question is what is on that port, not what this object believes.
        """
        port = self.api_port if api_port is None else api_port
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{int(port)}/api/streams", timeout=timeout
            ) as response:
                raw = json.loads(response.read().decode("utf-8", "replace"))
        except (OSError, ValueError, TypeError):
            return None
        return raw if isinstance(raw, dict) else None

    def wait_until_listening(self, seconds: float = LISTENING_SECONDS) -> bool:
        """Wait, briefly, for the RTSP port to start accepting connections.

        A go2rtc that was spawned a moment ago has not bound its ports yet, and
        asking it for a picture in that moment answers "the connection failed"
        for every stream - which reads as a camera that will not answer and is
        nothing of the kind. Short, because a refused connection on the loopback
        comes back instantly and this is only covering the gap between spawning
        a process and that process listening.
        """
        deadline = time.monotonic() + max(seconds, 0.0)
        while True:
            if is_live({"rtsp_port": self.rtsp_port}, timeout=0.5):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    def api_log(self, api_port: int | None = None, timeout: float = API_TIMEOUT) -> list[dict]:
        """What the server has lately said about itself, in its own words.

        This is the only way to read an adopted server's output at all. It was
        started by a console that has closed, its pipe went with that console,
        and `_pump_output` has nothing to pump - so "401 Unauthorized", the one
        line that says why there is no picture, is inside a process nobody can
        hear. `/api/log` is that same output, kept by the server, and it is how
        the sentence in the Logs tab can be go2rtc's rather than ours.

        The body is one JSON object per line - go2rtc answers it as
        `application/jsonlines` - and a line that will not parse is skipped
        rather than losing the rest.
        """
        port = self.api_port if api_port is None else api_port
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{int(port)}/api/log", timeout=timeout
            ) as response:
                body = response.read().decode("utf-8", "replace")
        except (OSError, ValueError, TypeError):
            return []
        entries: list[dict] = []
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries

    def said_about(self, name: str, api_port: int | None = None) -> str:
        """The last thing the server itself said about that stream, or "".

        The newest matching line wins: go2rtc repeats itself for as long as the
        camera keeps refusing, and the most recent attempt is the one the
        operator is being told about.
        """
        for entry in reversed(self.api_log(api_port)):
            if str(entry.get("stream") or "") != name:
                continue
            said = str(entry.get("error") or entry.get("message") or "").strip()
            if said:
                return said[:MAX_LINE_CHARS]
        return ""

    def without_a_picture(
        self,
        names: list[str] | None = None,
        rtsp_port: int | None = None,
        api_port: int | None = None,
        timeout: float = PROBE_TIMEOUT,
    ) -> dict[str, str]:
        """Which of these streams the server cannot actually hand over, and why.

        Empty is the healthy answer. Every stream is asked at once, because they
        fail together - a camera that has refused one connection refuses both -
        and asking in turn would multiply the wait by the number of views.

        The reason is go2rtc's own where go2rtc gives one, and the refusal it
        sent otherwise. Both beat a blank pane.
        """
        wanted = list(self.stream_names if names is None else names)
        if not wanted:
            return {}
        port = self.rtsp_port if rtsp_port is None else rtsp_port
        answers: dict[str, tuple[bool, str]] = {}
        threads = []
        for name in wanted:
            def ask(name: str = name) -> None:
                answers[name] = rtsp_describe(port, name, timeout)

            thread = threading.Thread(target=ask, name=f"go2rtc-probe-{name}", daemon=True)
            thread.start()
            threads.append(thread)
        for thread in threads:
            # Bounded twice over: the socket has the same timeout, and this
            # cannot outlast it by more than the moment it takes to return.
            thread.join(timeout + 1.0)
        broken: dict[str, str] = {}
        for name in wanted:
            served, said = answers.get(name, (False, "it was not asked in time"))
            if served:
                continue
            in_its_own_words = self.said_about(name, api_port)
            broken[name] = in_its_own_words or said
        return broken

    def unadoptable(self, endpoint: dict, probe_timeout: float = PROBE_TIMEOUT) -> str:
        """Why the server that file names cannot be taken on, or "" if it can.

        A port in a file is a claim, not an answer. The console adopted on the
        strength of one and pointed every pane at rtsp://127.0.0.1:8554/thermal,
        which answered "Failed to connect" - because the server the file named
        had been gone since the last power cut, or because it was still running
        the streams it was started with a fortnight ago and had never heard of
        this one. Both leave the operator with no picture and no sentence
        saying why, which is the worst state this console can be in.

        Asking whether it has heard of the names was not enough, and the third
        morning of this proved it: the server was listening, it did list both
        names, and every pane got `Failed to setup RTSP session` for as long as
        the console stayed open, because that server could not get the picture
        from the camera at all. A name is not a picture.

        So it is asked four times, cheapest first: is anything listening where
        the recorder is being told to look; does the thing that answers know the
        streams this settings file asks for; was it started on the settings that
        are on disk now; and - the only one of the four that is evidence rather
        than agreement - can it actually hand each of those streams over. The
        last is a DESCRIBE per stream, all at once, bounded by `probe_timeout`.
        """
        if not is_live(endpoint):
            return (
                f"nothing is listening on port {endpoint.get('rtsp_port')}, where "
                "it says the video is"
            )
        try:
            api_port = int(endpoint.get("api_port") or 0)
        except (TypeError, ValueError):
            api_port = 0
        if api_port <= 0:
            return "it does not say which port to ask what it is serving"
        served = self.api_streams(api_port)
        if served is None:
            return f"nothing answered the streaming server's API on port {api_port}"
        missing = [name for name in self.stream_names if name not in served]
        if missing:
            serving = ", ".join(sorted(served)) or "nothing"
            return (
                f"it is serving {serving}, not {', '.join(missing)} - it was "
                "started before these streams were set up"
            )
        try:
            rtsp_port = int(endpoint.get("rtsp_port") or 0)
        except (TypeError, ValueError):
            rtsp_port = 0
        elsewhere = self._configured_differently(served, api_port, rtsp_port)
        if elsewhere:
            return elsewhere
        broken = self.without_a_picture(
            rtsp_port=rtsp_port or None, api_port=api_port, timeout=probe_timeout
        )
        if broken:
            return "; ".join(
                f"it is listening, and it knows the name {name}, but it has no "
                f"picture to give for it: {said}"
                for name, said in sorted(broken.items())
            )
        return ""

    def _configured_differently(
        self, served: dict, api_port: int, rtsp_port: int
    ) -> str:
        """Why that server cannot have read the settings on disk now, or "".

        Two ways of asking, and the first is the strong one. When this console
        started that server it wrote down a digest of the streams it handed it;
        a digest that no longer matches means the settings behind the picture
        have been replaced - a corrected password, a corrected address, a
        renamed stream, a changed reader, any of them - and no amount of probing
        that server could ever discover it, because it is serving perfectly well
        from settings nobody wants any more.

        The claim has to be about the server being asked about. Two consoles
        with two go2rtcs would otherwise have this refuse a healthy server on
        the strength of a claim describing a different one.

        Failing that - a server started by a console older than this, which
        wrote no digest - what the API says its producers are pointed at, with
        the credentials taken out of both sides. That still catches a renamed
        stream and a changed address, and it can never be wrong about a
        password, which is the failure mode that matters here: refusing a
        healthy server means stopping it, and stopping it costs the picture.
        """
        wanted = {
            stream.name: source_for(
                stream, self.settings.camera.username, self.settings.camera.password
            )
            for stream in self.settings.camera.streams
            if stream.enabled and stream.url
        }
        claim = read_claim_details(self.pid_path)
        about_this_server = bool(
            claim
            and (not claim.api_port or claim.api_port == api_port)
            and (not claim.rtsp_port or not rtsp_port or claim.rtsp_port == rtsp_port)
        )
        if claim and about_this_server and claim.streams_fingerprint:
            if claim.streams_fingerprint != config_fingerprint({"streams": wanted}):
                return (
                    "it was started with different settings from the ones saved "
                    "now - the camera password, an address, a stream name or a "
                    "reader has changed since, and go2rtc reads its settings once, "
                    "when it starts"
                )
            return ""
        for name, source in sorted(wanted.items()):
            entry = served.get(name)
            producers = (entry or {}).get("producers") or []
            reported = next(
                (str(p.get("url")) for p in producers if isinstance(p, dict) and p.get("url")),
                "",
            )
            if not reported:
                continue  # it did not say; nothing is proven either way
            if without_credentials(reported) != without_credentials(source):
                return (
                    f"it is pointed at {without_credentials(reported)} for {name}, "
                    f"and the settings saved now say {without_credentials(source)}"
                )
        return ""

    def replace(self, why: str) -> None:
        """Stop the streaming server left over from an earlier run, and start ours.

        Only reached when that server has been shown not to be serving what this
        console needs, and the ordinary rule - never start a second go2rtc,
        because a second connection across the radio link is the one cost this
        arrangement exists to avoid - is not the rule here: what is on the port
        is not serving the camera to anybody, so replacing it costs nothing and
        keeping it costs the picture and the recording both.

        The claim is what makes this possible at all: it names the process, so
        the ghost is stopped rather than left holding the port. One that cannot
        be named is left alone and a fresh server is started beside it, on
        whatever port is free - no video at all is worse.
        """
        stream_logger.warning(
            "go2rtc: the streaming server left running from an earlier run is "
            "being replaced, because %s. There is no picture until the new one "
            "is up, which takes a moment",
            why,
        )
        pid = self.claimed_pid()
        if pid is not None:
            self._kill_tree(pid)
            deadline = time.monotonic() + ADOPTED_STOP_SECONDS
            while self._image_of(pid) is not None and time.monotonic() < deadline:
                time.sleep(0.1)
            if self._image_of(pid) is None:
                self._clear_claim(pid)
            else:
                stream_logger.warning(
                    "go2rtc: the one from the earlier run (pid %s) would not "
                    "stop, so the new one is starting beside it on another port",
                    pid,
                )
        else:
            stream_logger.warning(
                "go2rtc: nothing on disk says which process the earlier one is, "
                "so it cannot be stopped; the new one starts on another port"
            )
        self._forget_adopted_watch()
        self._adopted = False
        self._adopted_pid = None
        self._clear_endpoint()
        self.start()

    def sources(self) -> dict:
        """What go2rtc says about each stream: is the camera side connected?

        This separates two failures that look identical in the browser - the
        camera having dropped us, and the browser having stalled on a stream
        that is still arriving. Best effort: the console works without it.
        """
        if not self.running:
            return {}
        raw = self.api_streams()
        if raw is None:
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

        A line it writes over and over - a camera refusing the login, which it
        retries for ever - is counted rather than repeated. Two hundred copies
        of one sentence in a five-hundred-line ring is the same loss as the
        flood of restarts, and the sentence being repeated is usually the one
        worth keeping. The memory of what was last said belongs to this pump and
        no other thread touches it.
        """
        stream = getattr(process, "stdout", None)
        if stream is None:
            return
        repeats = RepeatedLine()

        def pump() -> None:
            try:
                for line in stream:
                    text = line.rstrip()[:MAX_LINE_CHARS]
                    if not text:
                        continue
                    self._recent.append(text)
                    times = repeats.seen(text)
                    if times is None:
                        continue
                    if times > 1:
                        text = f"{text} - this has now happened {times} times"
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
        means restarting it - which is why this is one call and not two.

        A forced stop, because this is a Save. An adopted server is protected
        from a window closing and not from its operator: one running the
        settings that have just been replaced is exactly the server a Save
        exists to replace. If it will not stop, nothing is started in its
        place - two go2rtcs on one camera is worse than a setting that did not
        apply - and `adopted` stays true so the console can say so.
        """
        self.settings = settings
        was_running = self.running
        self.stop(force=True)
        if self.running:
            return
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
