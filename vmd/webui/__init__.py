"""Local console: a small web server and the page it serves."""

from vmd.webui.server import DEFAULT_HOST, DEFAULT_PORT, ConsoleServer, make_server

__all__ = ["ConsoleServer", "DEFAULT_HOST", "DEFAULT_PORT", "make_server"]
