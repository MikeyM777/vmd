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
import threading
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pydantic import ValidationError

from vmd.settings import Settings, SettingsError, detect_free_bytes, load_settings, save_settings
from vmd.streaming.go2rtc import Go2rtcService
from vmd.ptz.service import PtzService
from vmd.webui.updater import Updater

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# A settings document is a few kilobytes. Anything approaching this is either a
# mistake or an attempt to exhaust memory, and reading it in costs that memory.
MAX_BODY_BYTES = 1_000_000
# Enough history for an operator to see what happened while they were away, and
# small enough that it can never be the thing that fills memory.
LOG_LINES = 500
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8723


class LogBuffer(logging.Handler):
    """The last few hundred log lines, kept in memory for the Logs tab.

    A file on disk would be the obvious answer, but the operator cannot open a
    file - they have a browser and a black window. Whatever the system says
    about itself has to be reachable from the console.
    """

    def __init__(self, capacity: int = LOG_LINES) -> None:
        super().__init__()
        self.records: deque[dict] = deque(maxlen=capacity)
        self._lock_ = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.exc_info:
                text += "\n" + logging.Formatter().formatException(record.exc_info)
        except Exception:  # noqa: BLE001 - logging must never raise into the caller
            text = "<unformattable log record>"
        with self._lock_:
            self.records.append(
                {
                    "time": record.created,
                    "level": record.levelname,
                    "source": record.name,
                    "text": text,
                }
            )

    def snapshot(self) -> list[dict]:
        with self._lock_:
            return list(self.records)


LOG_BUFFER = LogBuffer()


def capture_logs() -> LogBuffer:
    """Attach the buffer to the root logger. Idempotent."""
    root = logging.getLogger()
    if LOG_BUFFER not in root.handlers:
        LOG_BUFFER.setLevel(logging.INFO)
        root.addHandler(LOG_BUFFER)
    return LOG_BUFFER


class ConsoleServer(ThreadingHTTPServer):
    """Holds the settings path and the streaming server so handlers can reach
    them without a global."""

    daemon_threads = True
    # Deliberately off. On Windows SO_REUSEADDR lets a second console bind a
    # port that is already listening: both processes "start", one silently
    # serves nothing, and if the first dies the second takes over with a
    # different settings file. Failing to bind is the honest outcome.
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        settings_path: Path,
        streaming: Go2rtcService | None = None,
        updater: Updater | None = None,
        ptz: PtzService | None = None,
    ) -> None:
        super().__init__(address, ConsoleHandler)
        self.settings_path = settings_path
        self.streaming = streaming
        self.updater = updater
        self.ptz = ptz


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

    def handle_one_request(self) -> None:
        """Nothing a request can contain may kill the console.

        The handlers below catch what they can name. This is the backstop for
        what they cannot: a bad Content-Length, undecodable bytes, JSON nested
        deep enough to exhaust the stack. Without it those escape into
        socketserver, the client gets zero bytes and a traceback lands in the
        operator's window - the opposite of degrading visibly.
        """
        try:
            super().handle_one_request()
        except (ConnectionError, TimeoutError):
            pass  # the browser went away mid-response; nothing to report
        except Exception:  # noqa: BLE001 - the console must survive any request
            logger.exception("unhandled error serving %s", getattr(self, "path", "?"))
            try:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "the console could not handle that request")
            except Exception:  # noqa: BLE001 - the socket is probably gone too
                pass
            self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._serve_static("console.html")
        elif path == "/api/settings":
            self._get_settings()
        elif path == "/api/status":
            self._get_status()
        elif path == "/api/streams":
            self._get_streams()
        elif path == "/api/logs":
            self._get_logs()
        elif path == "/api/update":
            self._get_update()
        elif path == "/api/ptz":
            self._get_ptz()
        elif path.startswith("/static/"):
            self._serve_static(path[len("/static/") :])
        else:
            self._error(HTTPStatus.NOT_FOUND, f"no such path: {path}")

    do_HEAD = do_GET  # noqa: N815

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/settings":
            self._put_settings()
        elif path == "/api/update":
            self._post_update()
        elif path == "/api/ptz":
            self._post_ptz()
        else:
            self._error(HTTPStatus.NOT_FOUND, f"no such path: {path}")

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
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, f"Content-Length is not a number: {raw_length!r}")
            return
        if length < 0:
            # A negative length would make rfile.read(-1) block until the client
            # gives up, and the request would then be saved anyway.
            self._error(HTTPStatus.BAD_REQUEST, "a settings save needs a body and a Content-Length")
            return
        if length == 0:
            # An empty body used to parse as {} and overwrite every setting with
            # its default - camera address and password wiped, reported as success.
            self._error(HTTPStatus.BAD_REQUEST, "empty request body: nothing to save")
            return
        if length > MAX_BODY_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "settings payload is implausibly large")
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            self._error(HTTPStatus.BAD_REQUEST, "body is not valid UTF-8")
            return
        except RecursionError:
            self._error(HTTPStatus.BAD_REQUEST, "JSON is nested too deeply")
            return
        except json.JSONDecodeError as exc:
            self._error(HTTPStatus.BAD_REQUEST, f"not valid JSON: {exc}")
            return
        if not isinstance(payload, dict):
            self._error(HTTPStatus.BAD_REQUEST, "settings must be a JSON object")
            return
        try:
            settings = Settings.model_validate(payload)
        except ValidationError as exc:
            self._error(HTTPStatus.BAD_REQUEST, _first_problem(exc))
            return
        try:
            save_settings(settings, self.server.settings_path)
        except (OSError, ValueError) as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"could not write settings: {exc}")
            return

        # New camera address or stream paths mean the streaming server is now
        # pointed at the wrong thing. Restarting it here is what makes Save the
        # only action an operator has to take to get a picture.
        streaming = self.server.streaming
        if streaming is not None:
            try:
                streaming.apply(settings)
            except Exception:  # noqa: BLE001 - saving succeeded; streaming is secondary
                logger.exception("could not restart streaming after a settings change")

        if self.server.ptz is not None:
            try:
                self.server.ptz.apply(settings)
            except Exception:  # noqa: BLE001 - same: the save itself succeeded
                logger.exception("could not re-point PTZ after a settings change")

        self._send_json(HTTPStatus.OK, json.loads(settings.model_dump_json()))

    def _get_status(self) -> None:
        try:
            settings = load_settings(self.server.settings_path)
        except SettingsError:
            settings = Settings()
        root = settings.storage.root
        streams = [s.name for s in settings.camera.streams if s.enabled]
        # No root.exists() probe here: on a UNC path to a machine that is not
        # answering, that call blocks for the better part of a minute, and this
        # endpoint is polled. detect_free_bytes already returns None on failure.
        free_bytes = detect_free_bytes(root)
        self._send_json(
            HTTPStatus.OK,
            {
                "configured": bool(settings.camera.host and streams),
                "streams": streams,
                "storage_root": str(root),
                "free_bytes": free_bytes,
                "settings_path": str(self.server.settings_path),
                "recording": False,  # the recorder is a separate process; not wired yet
            },
        )


    def _get_streams(self) -> None:
        """Where the live video is, or why there is none. The page polls this."""
        streaming = self.server.streaming
        if streaming is None:
            self._send_json(
                HTTPStatus.OK,
                {
                    "running": False,
                    "reason": "live video is not enabled in this session",
                    "api_base": "",
                    "streams": [],
                },
            )
            return
        # Restart it here rather than in a separate loop: this endpoint is
        # already polled every few seconds by the page that needs the video.
        try:
            streaming.ensure_running()
        except Exception:  # noqa: BLE001 - reporting must not depend on restarting
            logger.exception("could not restart the streaming server")
        status = streaming.status()
        self._send_json(
            HTTPStatus.OK,
            {
                "sources": streaming.sources(),
                "running": status.running,
                "reason": status.reason,
                "api_base": status.api_base,
                "streams": status.streams,
            },
        )


    def _get_ptz(self) -> None:
        ptz = self.server.ptz
        if ptz is None:
            self._send_json(HTTPStatus.OK, {"available": False, "reason": "PTZ is not enabled"})
            return
        self._send_json(HTTPStatus.OK, ptz.status())

    def _post_ptz(self) -> None:
        """One endpoint for the whole head: move, stop, home.

        Deliberately not three. The browser sends these as keys go down and up,
        and a single shape keeps the ordering obvious on both sides.
        """
        ptz = self.server.ptz
        if ptz is None:
            self._error(HTTPStatus.CONFLICT, "PTZ is not enabled")
            return
        payload = self._read_json()
        if payload is None:
            return
        action = str(payload.get("action", "move"))
        if action == "stop":
            result = ptz.stop()
        elif action == "home":
            result = ptz.home()
        elif action == "move":
            try:
                pan = float(payload.get("pan", 0))
                tilt = float(payload.get("tilt", 0))
                zoom = float(payload.get("zoom", 0))
            except (TypeError, ValueError):
                self._error(HTTPStatus.BAD_REQUEST, "pan, tilt and zoom must be numbers")
                return
            result = ptz.move(pan, tilt, zoom)
        else:
            self._error(HTTPStatus.BAD_REQUEST, f"unknown action: {action}")
            return
        self._send_json(HTTPStatus.OK, result)

    def _read_json(self) -> dict | None:
        """A small JSON body, or None having already answered with the reason."""
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else 0
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "Content-Length is not a number")
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            self._error(HTTPStatus.BAD_REQUEST, "unusable Content-Length")
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, f"not valid JSON: {exc}")
            return None
        if not isinstance(payload, dict):
            self._error(HTTPStatus.BAD_REQUEST, "expected a JSON object")
            return None
        return payload

    def _get_update(self) -> None:
        updater = self.server.updater
        if updater is None:
            self._send_json(
                HTTPStatus.OK,
                {"running": False, "ok": None, "message": "", "output": [],
                 "current": {"known": False, "reason": "updating is not available in this session"}},
            )
            return
        self._send_json(HTTPStatus.OK, updater.snapshot())

    def _post_update(self) -> None:
        updater = self.server.updater
        if updater is None:
            self._error(HTTPStatus.CONFLICT, "updating is not available in this session")
            return
        started, why_not = updater.start()
        if not started:
            self._error(HTTPStatus.CONFLICT, why_not)
            return
        self._send_json(HTTPStatus.ACCEPTED, updater.snapshot())

    def _get_logs(self) -> None:
        """Everything the console has said about itself, newest last."""
        self._send_json(HTTPStatus.OK, {"lines": LOG_BUFFER.snapshot()})


def _first_problem(exc: ValidationError) -> str:
    """One readable sentence out of a pydantic error, for a form to display."""
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first["loc"]) or "settings"
    return f"{where}: {first['msg']}"


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    settings_path: str | Path = "settings.json",
    streaming: Go2rtcService | None = None,
    updater: Updater | None = None,
    ptz: PtzService | None = None,
) -> ConsoleServer:
    return ConsoleServer((host, port), Path(settings_path), streaming, updater, ptz)
