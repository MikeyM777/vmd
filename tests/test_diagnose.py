"""The two tools the operator has when there is no picture, and what they leak.

Nothing here touches a network or spawns a process: `_reachable` and
`subprocess.run` are both replaced, so every test is bounded by its own
assertions rather than by somebody's timeout.
"""

from __future__ import annotations

import subprocess

import pytest

from vmd.settings import CameraSettings, RadioSettings, Settings, StreamSettings
from vmd.streaming import diagnose as diagnose_module
from vmd.streaming.diagnose import diagnose, find_paths, redact

PASSWORD = "p@ss:w/rd"
ENCODED = "p%40ss%3Aw%2Frd"


def settings_with(reader: str = "auto", password: str = PASSWORD) -> Settings:
    return Settings(
        camera=CameraSettings(
            host="10.0.0.5",
            username="admin",
            password=password,
            streams=[
                StreamSettings(name="thermal", url="rtsp://10.0.0.5:554/ch1", reader=reader)
            ],
        )
    )


@pytest.fixture
def no_network(monkeypatch):
    """Nothing answers, and nothing is spawned. Both tools then run instantly."""
    monkeypatch.setattr(diagnose_module, "_reachable", lambda host, port, timeout=3.0: False)

    def refuse(*args, **kwargs):  # pragma: no cover - reaching this is the failure
        raise AssertionError("a diagnostic spawned a process it should not have")

    monkeypatch.setattr(diagnose_module.subprocess, "run", refuse)


def test_a_stream_read_by_ffmpeg_is_still_diagnosed(no_network) -> None:
    """The ffmpeg reader is picked for the stream that is already misbehaving.

    Its source is stored as "ffmpeg:rtsp://...#video=copy", which is a go2rtc
    instruction rather than a URL - so the tool found no host in it and reported
    that instead of testing the camera, on exactly the stream someone had come
    here about.
    """
    lines = diagnose(settings_with(reader="ffmpeg"))
    assert not any("no host in it" in line for line in lines), "\n".join(lines)
    assert any("10.0.0.5:554" in line for line in lines), "\n".join(lines)


def test_find_paths_probes_the_address_inside_an_ffmpeg_source(monkeypatch) -> None:
    tried: list[str] = []

    def fake_try_path(base_url: str, path: str):
        tried.append(base_url)
        return False, ""

    monkeypatch.setattr(diagnose_module, "try_path", fake_try_path)

    lines = find_paths(settings_with(reader="ffmpeg"))
    assert lines[0].startswith("Trying"), lines
    assert "10.0.0.5" in lines[0]
    assert tried, "no path was tried at all"
    assert all(base.startswith("rtsp://") for base in tried), tried


def test_the_camera_password_never_survives_in_either_form(no_network) -> None:
    """The typed form and the percent-encoded form are different strings.

    `with_credentials` percent-encodes the password into the RTSP URL, so a
    redaction that knew only what the operator typed matched nothing - and these
    lines are what "Save a report" writes into a file meant to be sent on.
    """
    text = "\n".join(diagnose(settings_with()))
    assert PASSWORD not in text
    assert ENCODED not in text
    assert "****" in text


def test_the_radio_password_is_redacted_too(no_network) -> None:
    settings = settings_with(password="")
    settings.radio = RadioSettings(
        host="10.0.0.9", username="ubnt", password="radi0&pass", enabled=True
    )
    lines = diagnose(settings) + ["the radio said radi0&pass"]
    assert "radi0&pass" not in "\n".join(redact(lines, settings))


def test_ffprobe_echoing_the_url_back_does_not_leak_the_password(monkeypatch) -> None:
    """ffprobe puts the input URL in its own error text, and this prints it."""
    monkeypatch.setattr(diagnose_module, "_reachable", lambda host, port, timeout=3.0: True)

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode=1,
            stdout="",
            stderr=f"rtsp://admin:{ENCODED}@10.0.0.5:554/ch1: 401 Unauthorized",
        )

    monkeypatch.setattr(diagnose_module.subprocess, "run", fake_run)

    text = "\n".join(diagnose(settings_with()))
    assert ENCODED not in text
    assert PASSWORD not in text
    assert "401 Unauthorized" in text, "the camera's own words must still get through"
