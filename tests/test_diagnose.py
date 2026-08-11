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


def test_a_password_typed_into_the_address_itself_is_redacted(no_network) -> None:
    """The form the camera's own instructions come in, and the one that leaked.

    Every camera manual on earth gives the address as
    `rtsp://admin:hunter2@192.168.1.251/ch0`, and an operator who pastes that in
    has no reason to fill the password field as well - the address already
    carries the login, which is a case this file explicitly handles two hundred
    lines further down. But redaction worked off the two password FIELDS, so a
    password that was never in either of them was printed verbatim on the
    `typed :` line of a report whose whole purpose is being handed to somebody
    else.

    That is the fifth encoding of the same bug in this project, which is the
    reason the rule changed: the secret is found in the output rather than
    listed at every place it might appear.
    """
    settings = settings_with(password="")
    settings.camera.streams = [
        StreamSettings(name="thermal", url="rtsp://admin:hunter2@10.0.0.5:554/ch1")
    ]
    text = "\n".join(diagnose(settings))
    assert "hunter2" not in text, text
    # And what is left is still the sentence he came for: the username and the
    # address are what he checks, and only the secret goes.
    assert "admin" in text and "10.0.0.5" in text


def test_a_password_in_an_address_survives_neither_the_typed_nor_the_encoded_form(
    no_network,
) -> None:
    """The same address with a password that has to be escaped to travel."""
    settings = settings_with(password="")
    settings.camera.streams = [
        StreamSettings(name="thermal", url=f"rtsp://admin:{ENCODED}@10.0.0.5:554/ch1")
    ]
    lines = diagnose(settings) + [f"ffprobe: could not open rtsp://admin:{ENCODED}@10.0.0.5/ch1"]
    text = "\n".join(redact(lines, settings))
    assert ENCODED not in text, text
    assert PASSWORD not in text, text


def test_a_login_in_a_line_is_masked_even_when_settings_has_never_heard_of_it() -> None:
    """The half of the rule that stopped listing places and looks for the shape.

    ffprobe, ffmpeg and go2rtc all echo the address they were given, and the
    address they were given is not always one this settings file can name - a
    go2rtc source, a URL built by something else, a line copied from a log. The
    only thing every one of them has in common is `scheme://user:secret@`, and
    that is now what is looked for.
    """
    text = "\n".join(
        redact(["ffprobe: rtsp://admin:s3cret@10.0.0.5/ch1 refused"], settings_with(password=""))
    )
    assert "s3cret" not in text, text
    assert "rtsp://admin:****@10.0.0.5/ch1" in text, text


def test_a_password_from_an_address_is_masked_where_it_is_echoed_on_its_own() -> None:
    """And the half that still earns its place: a secret with no shape to find.

    A camera that puts the login in its refusal - or any tool that prints the
    password without the URL around it - leaves nothing for the pattern above to
    match, which is why the list of known secrets is still applied afterwards.
    """
    settings = settings_with(password="")
    settings.camera.streams = [
        StreamSettings(name="thermal", url="rtsp://admin:hunter2@10.0.0.5:554/ch1")
    ]
    text = "\n".join(redact(["the camera answered: bad login for admin/hunter2"], settings))
    assert "hunter2" not in text, text


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
