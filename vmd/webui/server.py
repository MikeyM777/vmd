"""The local web server: serves the console and the settings it reads and writes.

Bound to 127.0.0.1 and nothing else. The machine this runs on has no network of
its own, and the console is for the person sitting at it.

There is no framework here on purpose. The standard library serves three static
files and two JSON endpoints perfectly well, and one fewer dependency is one
fewer thing to install on a laptop that cannot reach the internet.
"""

from __future__ import annotations

import json
import logging
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pydantic import ValidationError

from vmd.settings import Settings, SettingsError, detect_free_bytes, load_settings, save_settings

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8723


class ConsoleServer(ThreadingHTTPServer):
    """Holds the settings path so handlers can reach it without a global."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], settings_path: Path) -> None:
        super().__init__(address, ConsoleHandler)
        self.settings_path = settings_path


class ConsoleHandler(BaseHTTPRequestHandler):
    server: ConsoleServer  # type: ignore[assignment]
    server_version = "vmd"
    sys_version = ""

    # ---------------------------------------------------------------- helpers

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The page is served fresh every time. A stale console showing last
        # week's settings is worse than a few kilobytes of traffic on loopback.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, payload: object) -> None:
        self._send(status, json.dumps(payload, default=str).encode("utf-8"), "application/json")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": message})

    def log_message(self, fmt: str, *args: object) -> None:
        logger.debug("%s %s", self.address_string(), fmt % args)

    # ----------------------------------------------------------------- routes

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._serve_static("console.html")
        elif path == "/api/settings":
            self._get_settings()
        elif path == "/api/status":
            self._get_status()
        elif path.startswith("/static/"):
            self._serve_static(path[len("/static/") :])
        else:
            self._error(HTTPStatus.NOT_FOUND, f"no such path: {path}")

    do_HEAD = do_GET  # noqa: N815

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/settings":
            self._error(HTTPStatus.NOT_FOUND, f"no such path: {self.path}")
            return
        self._put_settings()

    # --------------------------------------------------------------- handlers

    def _serve_static(self, name: str) -> None:
        # Resolve and confine: a request for ../../settings.json must not escape
        # the static directory, even on a server bound to loopback.
        target = (STATIC_DIR / name).resolve()
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            self._error(HTTPStatus.NOT_FOUND, f"no such file: {name}")
            return
        ctype, _ = mimetypes.guess_type(target.name)
        self._send(HTTPStatus.OK, target.read_bytes(), ctype or "application/octet-stream")

    def _get_settings(self) -> None:
        try:
            settings = load_settings(self.server.settings_path)
        except SettingsError as exc:
            # A corrupt file must not leave the operator with a blank form and no
            # explanation; the console shows this message and keeps working.
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self._send_json(HTTPStatus.OK, json.loads(settings.model_dump_json()))

    def _put_settings(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            self._error(HTTPStatus.BAD_REQUEST, f"not valid JSON: {exc}")
            return
        try:
            settings = Settings.model_validate(payload)
        except ValidationError as exc:
            self._error(HTTPStatus.BAD_REQUEST, _first_problem(exc))
            return
        try:
            save_settings(settings, self.server.settings_path)
        except OSError as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"could not write settings: {exc}")
            return
        self._send_json(HTTPStatus.OK, json.loads(settings.model_dump_json()))

    def _get_status(self) -> None:
        try:
            settings = load_settings(self.server.settings_path)
        except SettingsError:
            settings = Settings()
        root = settings.storage.root
        streams = [s.name for s in settings.camera.streams if s.enabled]
        self._send_json(
            HTTPStatus.OK,
            {
                "configured": bool(settings.camera.host and streams),
                "streams": streams,
                "storage_root": str(root),
                "free_bytes": detect_free_bytes(root if root.exists() else Path.cwd()),
                "settings_path": str(self.server.settings_path),
                "recording": False,  # the recorder is a separate process; not wired yet
            },
        )


def _first_problem(exc: ValidationError) -> str:
    """One readable sentence out of a pydantic error, for a form to display."""
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first["loc"]) or "settings"
    return f"{where}: {first['msg']}"


def make_server(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, settings_path: str | Path = "settings.json"
) -> ConsoleServer:
    return ConsoleServer((host, port), Path(settings_path))
