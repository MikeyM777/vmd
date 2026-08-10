"""Answering "why is there no picture" from inside the console.

The same checks the command-line tool runs, returned as lines the Settings tab
prints. An operator with a browser and no terminal must be able to get the
camera's own answer, because that answer is the whole diagnosis: an address that
nothing answers on, a login the camera rejects, and a path it does not have all
look identical from a panel that says "connecting".
"""

from __future__ import annotations

import socket
import subprocess
from urllib.parse import urlsplit, urlunsplit

from vmd.settings import Settings
from vmd.streaming.go2rtc import build_config, find_binary

PROBE_TIMEOUT = 15
PATH_TIMEOUT = 6

# The paths cameras actually use, in rough order of how often. Probing beats
# reading a manual nobody has: the camera answers this question definitively in
# under a minute, and a wrong path is indistinguishable from every other failure
# from the console's point of view.
COMMON_PATHS = [
    "/ch1", "/ch2", "/ch0", "/stream1", "/stream2", "/live", "/live1", "/live2",
    "/h264", "/video1", "/video2", "/1", "/2", "/profile1", "/profile2",
    "/Streaming/Channels/101", "/Streaming/Channels/201",
    "/cam/realmonitor?channel=1&subtype=0", "/cam/realmonitor?channel=2&subtype=0",
    "/axis-media/media.amp", "/media/video1", "/videoMain", "/onvif1", "/onvif2",
]


def _reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def try_path(base_url: str, path: str) -> tuple[bool, str]:
    """Ask one path for video. Returns (worked, what it said)."""
    parsed = urlsplit(base_url)
    candidate = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    candidate = candidate.rstrip("/") + path
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-hide_banner", "-loglevel", "error",
                "-rtsp_transport", "tcp", "-timeout", "4000000",
                "-show_entries", "stream=codec_name,width,height",
                "-of", "default=noprint_wrappers=1",
                candidate,
            ],
            capture_output=True, text=True, timeout=PATH_TIMEOUT, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, ""
    if probe.returncode == 0 and probe.stdout.strip():
        return True, " ".join(probe.stdout.split())
    return False, (probe.stderr or "").strip().splitlines()[:1][0] if probe.stderr.strip() else ""


def find_paths(settings: Settings, on_progress=None) -> list[str]:
    """Try the common RTSP paths and report which ones give video."""
    camera = settings.camera
    enabled = [s for s in camera.streams if s.enabled and s.url]
    if not enabled:
        return ["No stream is configured, so there is no address to work from."]

    config = build_config(settings, 1984, 8554)
    base = config["streams"].get(enabled[0].name, enabled[0].url)
    parsed = urlsplit(base)
    if not parsed.hostname:
        return ["The configured address has no host in it."]

    lines = [f"Trying {len(COMMON_PATHS)} common paths on {parsed.hostname}:{parsed.port or 554}", ""]
    working: list[str] = []
    for index, path in enumerate(COMMON_PATHS, 1):
        if on_progress:
            on_progress(f"trying {path} ({index}/{len(COMMON_PATHS)})")
        worked, detail = try_path(base, path)
        if worked:
            working.append(path)
            lines.append(f"  [ok] {path}   {detail}")
    lines.append("")
    if working:
        lines.append("Use one of these as the stream address:")
        for path in working:
            scheme, netloc = parsed.scheme, parsed.netloc
            # Shown without the login: the console adds it from the Camera fields.
            host = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
            lines.append(f"  {scheme}://{host}{path}")
    else:
        lines.append("None of the common paths returned video.")
        lines.append("Check the username and password first - a camera that refuses the")
        lines.append("login refuses every path the same way.")
    return lines


def diagnose(settings: Settings) -> list[str]:
    """Plain lines, in the order someone would work through them."""
    lines: list[str] = []
    camera = settings.camera

    lines.append(f"camera address : {camera.host or '(empty)'}")
    lines.append(f"username       : {camera.username or '(empty)'}")
    lines.append(f"password       : {'set' if camera.password else '(empty)'}")

    enabled = [s for s in camera.streams if s.enabled and s.url]
    if not enabled:
        lines.append("")
        lines.append("No stream is enabled. Add an address under Streams and tick record.")
        return lines

    config = build_config(settings, 1984, 8554)
    binary = find_binary()
    lines.append(f"go2rtc         : {binary or 'NOT INSTALLED - run install.bat'}")

    for stream in enabled:
        url = config["streams"].get(stream.name, stream.url)
        parsed = urlsplit(url)
        lines.append("")
        lines.append(f"[{stream.name}]")
        lines.append(f"  typed : {stream.url}")
        if url != stream.url:
            shown = url.replace(camera.password, "****") if camera.password else url
            lines.append(f"  sent  : {shown}")
        elif camera.password:
            lines.append("  sent  : unchanged - this address already carries a login,")
            lines.append("          or it is not an rtsp:// address")

        host, port = parsed.hostname, parsed.port or 554
        if not host:
            lines.append("  [x] This address has no host in it.")
            continue

        if not _reachable(host, port):
            lines.append(f"  [x] Nothing answers on {host}:{port}.")
            lines.append("      Wrong address, camera off, or the radio link is down.")
            continue
        lines.append(f"  [ok] {host}:{port} answers.")

        try:
            probe = subprocess.run(
                [
                    "ffprobe", "-hide_banner", "-loglevel", "error",
                    "-rtsp_transport", "tcp", "-timeout", "5000000",
                    "-show_entries", "stream=codec_name,width,height,avg_frame_rate",
                    "-of", "default=noprint_wrappers=1",
                    url,
                ],
                capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=False,
            )
        except FileNotFoundError:
            lines.append("  ffprobe is not installed, so the stream itself was not tested.")
            continue
        except subprocess.TimeoutExpired:
            lines.append(f"  [x] Connected, but sent no video within {PROBE_TIMEOUT} s.")
            lines.append("      The path is usually wrong when this happens.")
            continue

        if probe.returncode == 0 and probe.stdout.strip():
            lines.append("  [ok] The camera is sending video:")
            for line in probe.stdout.strip().splitlines():
                lines.append(f"       {line}")
            continue

        message = (probe.stderr or "").strip() or "no reason given"
        lines.append("  [x] The camera refused or sent nothing. It said:")
        for line in message.splitlines()[:6]:
            lines.append(f"       {line}")
        lowered = message.lower()
        if "401" in lowered or "unauthorized" in lowered:
            lines.append("      -> the username or password is wrong for this camera.")
        elif "404" in lowered or "not found" in lowered:
            lines.append("      -> the address is right but the path is wrong.")
            lines.append("         Try /ch1, /ch2, /stream1, /stream2, /h264, /live.")
        elif "timed out" in lowered or "timeout" in lowered:
            lines.append("      -> it accepted the connection then went quiet, which")
            lines.append("         usually means the path does not exist on this camera.")
    return lines
