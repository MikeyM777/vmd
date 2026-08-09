"""`uv run python -m vmd.streaming.check` - what is actually being sent to the camera.

When the picture will not come up, the question is always the same: what exactly
did we ask for? This prints the stored settings, the exact URL handed to go2rtc,
and then tries the camera itself so the answer comes from the camera rather than
from a guess.

Passwords are printed in full, deliberately. The machine is offline and the
usual cause of this failure is a typo that masking hides.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from vmd.settings import SettingsError, load_settings
from vmd.streaming.go2rtc import build_config, find_binary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vmd-check", description="Show and test what the console sends to the camera"
    )
    parser.add_argument("--settings", default="settings.json", help="which settings file to read")
    return parser.parse_args(argv)


def reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path(args.settings)

    print()
    print(f"  settings file : {path.resolve()}")
    if not path.exists():
        print("  This file does not exist, so nothing has been saved yet.")
        print("  Open the console, fill in Settings, and press Save.")
        return 1

    try:
        settings = load_settings(path)
    except SettingsError as exc:
        print(f"  The settings file cannot be read: {exc}")
        return 1

    camera = settings.camera
    print(f"  camera address: {camera.host or '(empty)'}")
    print(f"  username      : {camera.username or '(empty)'}")
    print(f"  password      : {camera.password or '(empty)'}")
    print()

    if not camera.streams:
        print("  No streams are saved. Add an RTSP address in Settings and press Save.")
        return 1

    config = build_config(settings, 1984, 8554)
    print("  What the streaming server is given:")
    for stream in camera.streams:
        mark = "on " if stream.enabled else "off"
        effective = config["streams"].get(stream.name, "(not enabled)")
        print(f"   [{mark}] {stream.name}")
        print(f"       typed    : {stream.url or '(empty)'}")
        print(f"       sent     : {effective}")
        if stream.enabled and stream.url and effective == stream.url and camera.password:
            print("       note     : credentials were NOT added - this URL already has an @,")
            print("                  or its scheme is not rtsp. Check the address.")
    print()

    binary = find_binary()
    print(f"  go2rtc        : {binary or 'NOT FOUND - run install.bat'}")

    for stream in camera.streams:
        if not (stream.enabled and stream.url):
            continue
        parsed = urlsplit(config["streams"][stream.name])
        host, port = parsed.hostname, parsed.port or 554
        if not host:
            continue
        print()
        print(f"  Testing {stream.name} -> {host}:{port}")
        if not reachable(host, port):
            print(f"   [x] Nothing is answering on {host}:{port}.")
            print("       The address is wrong, the camera is off, or the link is down.")
            print(f"       Try:  ping {host}")
            continue
        print(f"   [ok] {host}:{port} answers.")
        if binary is None:
            continue
        # Ask ffprobe, because its failure message is the camera's own words.
        try:
            probe = subprocess.run(
                [
                    "ffprobe", "-hide_banner", "-loglevel", "error",
                    "-rtsp_transport", "tcp", "-timeout", "5000000",
                    "-show_entries", "stream=codec_name,width,height",
                    "-of", "default=noprint_wrappers=1",
                    config["streams"][stream.name],
                ],
                capture_output=True, text=True, timeout=25, check=False,
            )
        except FileNotFoundError:
            print("   ffprobe is not installed, so the stream itself was not tested.")
            continue
        except subprocess.TimeoutExpired:
            print("   [x] The camera accepted the connection but sent no video within 25 s.")
            continue

        if probe.returncode == 0 and probe.stdout.strip():
            print("   [ok] The camera is sending video:")
            for line in probe.stdout.strip().splitlines():
                print(f"        {line}")
        else:
            message = (probe.stderr or "").strip() or "no reason given"
            print("   [x] The camera refused or sent nothing. It said:")
            for line in message.splitlines()[:6]:
                print(f"        {line}")
            lowered = message.lower()
            if "401" in lowered or "unauthorized" in lowered:
                print("        -> wrong username or password for this camera.")
            elif "404" in lowered or "not found" in lowered:
                print("        -> the address is right but the path is wrong.")
                print("           Run: uv run python spike/probe_camera.py <ip> --user U --password P")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
