"""Start the console: `python -m vmd.webui`, or double-click VMD.bat.

Opens the browser itself, because the point of this entry point is that someone
double-clicks one thing and the console appears.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path

from vmd.settings import SettingsError, load_settings
from vmd.streaming.go2rtc import Go2rtcService, find_binary
from vmd.webui.server import DEFAULT_HOST, DEFAULT_PORT, capture_logs, make_server

logger = logging.getLogger("vmd.webui")


def default_settings_path() -> Path:
    """Beside the executable, so settings do not follow whatever folder it was
    launched from. Double-clicking VMD.exe from the desktop and from its own
    folder must reach the same camera address."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "settings.json"
    return Path("settings.json")


def hold_window_open() -> None:
    """Keep the window up long enough to read the failure.

    A PyInstaller console exe started from Explorer owns its window, and that
    window closes the instant the process exits. Printing why we could not start
    is worthless if the operator cannot read it - and VMD.exe is the thing they
    are told to double-click every day.
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        input("\n  Press Enter to close this window. ")
    except (EOFError, KeyboardInterrupt):
        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vmd-console", description="VMD console")
    parser.add_argument("--host", default=DEFAULT_HOST, help="interface to bind (default loopback)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on")
    parser.add_argument(
        "--settings", default=str(default_settings_path()), help="where settings are stored"
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--no-live", action="store_true", help="do not start the streaming server")
    parser.add_argument("--stream-port", type=int, default=1984, help="go2rtc API port")
    parser.add_argument("--rtsp-port", type=int, default=8554, help="go2rtc internal RTSP port")
    return parser.parse_args(argv)


def start_streaming(args: argparse.Namespace) -> Go2rtcService | None:
    """Bring up live video, or return None having said why in the log.

    Nothing here is allowed to stop the console from starting. A console with no
    picture is a bad day; a console that will not open is a dead system.
    """
    if args.no_live:
        return None
    try:
        settings = load_settings(args.settings)
    except SettingsError:
        # The console itself reports the broken file; live video simply waits.
        return None
    service = Go2rtcService(
        settings,
        config_path=Path(args.settings).parent / "go2rtc.json",
        binary=find_binary(),
        api_port=args.stream_port,
        rtsp_port=args.rtsp_port,
    )
    try:
        service.start()
    except Exception:  # noqa: BLE001 - live video must never block the console
        logger.exception("could not start live video")
    return service


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Before anything else that might log: the Logs tab should show the startup
    # of the very session the operator is looking at.
    capture_logs()
    args = parse_args(argv)

    streaming = start_streaming(args)
    try:
        server = make_server(args.host, args.port, args.settings, streaming)
    except (OSError, OverflowError, ValueError) as exc:
        # Nearly always "port already in use", which means the console is
        # probably already running. Say that rather than printing a traceback.
        print(f"\n  Cannot start on {args.host}:{args.port} - {exc}")
        print("  If the console is already open in a browser tab, that is why.")
        print(f"  To use a different port:  python -m vmd.webui --port {args.port + 1}\n")
        # Streaming started before the console did. Leaving it behind would hold
        # the camera connection and its ports with nothing on screen to show it.
        if streaming is not None:
            streaming.stop()
        hold_window_open()
        return 1

    # The bound port, not the requested one: --port 0 asks the OS to choose,
    # and printing the 0 back would advertise an address nothing answers on.
    port = server.server_address[1]
    url = f"http://{args.host}:{port}/"
    print()
    print("  VMD console")
    print(f"  {url}")
    print(f"  settings: {args.settings}")
    if streaming is not None:
        status = streaming.status()
        print(f"  live video: {status.reason}" + (f" ({', '.join(status.streams)})" if status.streams else ""))
    print()
    print("  Leave this window open. Closing it stops the console.")
    print("  Press Ctrl+C to stop.")
    print()

    if not args.no_browser:
        # A beat late, so the browser asks for a page the server is ready to serve.
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        server.shutdown()
        server.server_close()
        if streaming is not None:
            streaming.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
