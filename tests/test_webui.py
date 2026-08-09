"""The console server: serving the page, and reading and writing settings."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from vmd.settings import load_settings
from vmd.webui.server import make_server


@pytest.fixture
def console(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """A running server on a port the OS picks, and the settings file it uses."""
    settings_path = tmp_path / "settings.json"
    server = make_server("127.0.0.1", 0, settings_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}", settings_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def post(url: str, payload: object) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_serves_the_console_page(console: tuple[str, Path]) -> None:
    base, _ = console
    status, body = get(f"{base}/")
    assert status == 200
    assert "VMD" in body


def test_settings_work_before_any_file_exists(console: tuple[str, Path]) -> None:
    """First run must not require the operator to create anything by hand."""
    base, settings_path = console
    assert not settings_path.exists()
    status, body = get(f"{base}/api/settings")
    assert status == 200
    assert json.loads(body)["camera"]["host"] == ""


def test_saving_settings_writes_them_and_they_come_back(console: tuple[str, Path]) -> None:
    base, settings_path = console
    status, saved = post(
        f"{base}/api/settings",
        {
            "camera": {
                "host": "192.168.1.64",
                "username": "admin",
                "password": "secret",
                "streams": [{"name": "thermal", "url": "rtsp://192.168.1.64/thermal"}],
            }
        },
    )
    assert status == 200
    assert saved["camera"]["host"] == "192.168.1.64"

    assert settings_path.exists()
    assert load_settings(settings_path).camera.streams[0].url == "rtsp://192.168.1.64/thermal"

    status, body = get(f"{base}/api/settings")
    assert json.loads(body)["camera"]["password"] == "secret"


def test_invalid_settings_are_refused_with_a_readable_reason(console: tuple[str, Path]) -> None:
    base, settings_path = console
    status, body = post(f"{base}/api/settings", {"storage": {"budget_gb": -5}})
    assert status == 400
    assert "budget_gb" in body["error"]
    assert not settings_path.exists(), "a rejected save must not have written anything"


def test_a_corrupt_settings_file_reports_itself(console: tuple[str, Path]) -> None:
    base, settings_path = console
    settings_path.write_text("{not json", encoding="utf-8")
    status, body = get(f"{base}/api/settings")
    assert status == 500
    assert "could not be read" in json.loads(body)["error"]


def test_status_says_whether_anything_is_configured(console: tuple[str, Path]) -> None:
    base, _ = console
    status, body = get(f"{base}/api/status")
    assert status == 200
    assert json.loads(body)["configured"] is False

    post(
        f"{base}/api/settings",
        {
            "camera": {
                "host": "10.0.0.2",
                "streams": [{"name": "visible", "url": "rtsp://10.0.0.2/v", "enabled": True}],
            }
        },
    )
    _, body = get(f"{base}/api/status")
    assert json.loads(body)["configured"] is True
    assert json.loads(body)["streams"] == ["visible"]


def test_static_paths_cannot_escape_the_static_directory(console: tuple[str, Path]) -> None:
    base, _ = console
    status, _ = get(f"{base}/static/../../settings.json")
    assert status == 404


def test_unknown_paths_are_not_found(console: tuple[str, Path]) -> None:
    base, _ = console
    assert get(f"{base}/nope")[0] == 404
    assert post(f"{base}/api/nope", {})[0] == 404
