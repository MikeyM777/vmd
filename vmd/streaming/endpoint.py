"""Where the streaming server is, for the processes that are not it.

The console runs go2rtc; the recording service runs on its own. Without this the
recorder opens its own RTSP connection to the camera, and every stream crosses
the radio link twice - once for the screen and once for the disk. On a link with
five megabits that is the difference between working and not.
"""

from __future__ import annotations

import json
import logging
import socket
from pathlib import Path

logger = logging.getLogger(__name__)


def read_endpoint(path: str | Path) -> dict | None:
    """What the console wrote about the running streaming server, if anything."""
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "rtsp_port" not in payload:
        return None
    return payload


def is_live(endpoint: dict, timeout: float = 1.5) -> bool:
    """A stale file is worse than none: it would point the recorder at nothing."""
    try:
        with socket.create_connection(("127.0.0.1", int(endpoint["rtsp_port"])), timeout=timeout):
            return True
    except (OSError, KeyError, TypeError, ValueError):
        return False


def local_source(endpoint: dict | None, name: str) -> str | None:
    """The local address for one stream, or None to use the camera directly."""
    if not endpoint:
        return None
    streams = endpoint.get("streams") or {}
    url = streams.get(name)
    return url if isinstance(url, str) and url else None
