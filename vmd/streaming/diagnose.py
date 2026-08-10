"""Answering "why is there no picture" from inside the console.

The same checks the command-line tool runs, returned as lines the Settings tab
prints. An operator with a browser and no terminal must be able to get the
camera's own answer, because that answer is the whole diagnosis: an address that
nothing answers on, a login the camera rejects, and a path it does not have all
look identical from a panel that says "connecting".
"""

from __future__ import annotations

import re
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


def _with_path(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/") + path


def _reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def measure_bitrate(url: str, seconds: int = 4) -> float | None:
    """How many megabits a second this stream really costs, measured.

    Resolution alone does not answer the question that matters here - whether a
    stream fits the radio link - and cameras rarely tell the truth about their
    configured bitrate. Pulling it for a few seconds does.
    """
    try:
        run = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-rtsp_transport", "tcp",
                "-i", url, "-t", str(seconds), "-c", "copy", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=seconds + 20, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    # ffmpeg's summary line. Newer builds write KiB/MiB, older ones kB/MB, and
    # matching only one of those silently returns "unmeasured" forever.
    match = re.search(r"video:\s*(\d+(?:\.\d+)?)\s*([kKmMgG])i?B", run.stderr or "")
    if not match:
        return None
    unit = match.group(2).lower()
    scale = {"k": 1024, "m": 1024**2, "g": 1024**3}[unit]
    size = float(match.group(1)) * scale
    return round(size * 8 / seconds / 1_000_000, 2)


def link_verdict(mbps: float | None, ceiling_mbps: float) -> str:
    if mbps is None:
        return ""
    if mbps > ceiling_mbps:
        return f"  -> {mbps} Mb/s does NOT fit a {ceiling_mbps:g} Mb/s link. Use a smaller stream."
    if mbps > ceiling_mbps * 0.6:
        return f"  -> {mbps} Mb/s leaves little room on a {ceiling_mbps:g} Mb/s link."
    return f"  -> {mbps} Mb/s fits a {ceiling_mbps:g} Mb/s link."


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

    ceiling = settings.bitrate.ceiling_kbps / 1000
    lines = [f"Trying {len(COMMON_PATHS)} common paths on {parsed.hostname}:{parsed.port or 554}", ""]
    working: list[tuple[str, float | None]] = []
    for index, path in enumerate(COMMON_PATHS, 1):
        if on_progress:
            on_progress(f"trying {path} ({index}/{len(COMMON_PATHS)})")
        worked, detail = try_path(base, path)
        if worked:
            if on_progress:
                on_progress(f"measuring {path}")
            mbps = measure_bitrate(_with_path(base, path))
            working.append((path, mbps))
            lines.append(f"  [ok] {path}   {detail}")
            verdict = link_verdict(mbps, ceiling)
            if verdict:
                lines.append(f"      {verdict.strip()}")
    lines.append("")
    if working:
        host = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
        # Cheapest first: on a link this narrow the smallest stream that shows
        # what is happening is the right one, not the best-looking one.
        for path, mbps in sorted(working, key=lambda item: (item[1] is None, item[1] or 0)):
            cost = f"{mbps} Mb/s" if mbps is not None else "unmeasured"
            lines.append(f"  {parsed.scheme}://{host}{path}   ({cost})")
        lines.insert(len(lines) - len(working), "Use one of these as the stream address:")
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
            mbps = measure_bitrate(url)
            verdict = link_verdict(mbps, settings.bitrate.ceiling_kbps / 1000)
            if verdict:
                lines.append(f"     {verdict.strip()}")
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
