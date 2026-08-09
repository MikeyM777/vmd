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

from vmd.webui.server import DEFAULT_HOST, DEFAULT_PORT, make_server

logger = logging.getLogger("vmd.webui")


def default_settings_path() -> Path:
    """Beside the executable, so settings do not follow whatever folder it was
    launched from. Double-clicking VMD.exe from the desktop and from its own
    folder must reach the same camera address."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "settings.json"
    return Path("settings.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vmd-console", description="VMD console")
    parser.add_argument("--host", default=DEFAULT_HOST, help="interface to bind (default loopback)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on")
    parser.add_argument(
        "--settings", default=str(default_settings_path()), help="where settings are stored"
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    try:
        server = make_server(args.host, args.port, args.settings)
    except OSError as exc:
        # Nearly always "port already in use", which means the console is
        # probably already running. Say that rather than printing a traceback.
        print(f"\n  Cannot start on {args.host}:{args.port} - {exc}")
        print("  If the console is already open in a browser tab, that is why.")
        print(f"  To use a different port:  python -m vmd.webui --port {args.port + 1}\n")
        return 1

    url = f"http://{args.host}:{args.port}/"
    print()
    print("  VMD console")
    print(f"  {url}")
    print(f"  settings: {args.settings}")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
