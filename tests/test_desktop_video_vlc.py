"""The real pane, against a real stream. Marked integration: it needs libVLC,
ffmpeg, go2rtc and a few seconds."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from vmd.streaming.go2rtc import find_binary


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def synthetic_stream(tmp_path: Path):
    """A go2rtc serving a generated test pattern over RTSP."""
    binary = find_binary()
    if binary is None or shutil.which("ffmpeg") is None:
        pytest.skip("needs go2rtc and ffmpeg")

    api, rtsp = free_port(), free_port()
    config = tmp_path / "cam.json"
    config.write_text(
        json.dumps(
            {
                "api": {"listen": f"127.0.0.1:{api}"},
                "rtsp": {"listen": f"127.0.0.1:{rtsp}"},
                "webrtc": {"listen": ""},
                "log": {"level": "warn"},
                "streams": {
                    "test": (
                        "exec:ffmpeg -hide_banner -re -f lavfi "
                        "-i testsrc=size=640x512:rate=15 -c:v libx264 "
                        "-preset ultrafast -tune zerolatency -g 15 -f rtsp {output}"
                    )
                },
            }
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [str(binary), "-c", str(config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    try:
        yield f"rtsp://127.0.0.1:{rtsp}/test"
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.integration
def test_the_real_pane_plays_a_real_stream(qtbot, synthetic_stream: str) -> None:
    from vmd.desktop.video import VlcVideoPane

    pane = VlcVideoPane()
    qtbot.addWidget(pane)
    pane.resize(320, 240)
    pane.show_widget()

    pane.show(synthetic_stream)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and pane.state != "playing":
        qtbot.wait(200)

    assert pane.state == "playing", "the pane never reported frames"
    frames = pane.frames_seen
    qtbot.wait(1500)
    assert pane.frames_seen > frames, "frames stopped advancing"

    pane.stop()
    assert pane.state == "stopped"


def test_the_pane_is_told_where_vlc_is_before_it_imports_it() -> None:
    """The search has to happen first or it has not happened at all: python-vlc
    goes looking at import, once, and keeps whatever it decided."""
    from vmd.desktop.video import load_vlc

    order: list[str] = []

    def prepare():
        order.append("looked")
        return None

    def imports():
        order.append("imported")
        return "the vlc module"

    assert load_vlc(prepare=prepare, import_vlc=imports) == "the vlc module"
    assert order == ["looked", "imported"]


def test_python_vlc_calling_it_a_day_does_not_take_the_console_with_it() -> None:
    """python-vlc answers a library it cannot load with `sys.exit(1)`, at import,
    from inside the constructor of a widget. That is not an exception - it walks
    straight past the guard that keeps the console open when there is no video,
    and the window never appears at all."""
    from vmd.desktop.libvlc import VlcUnavailable
    from vmd.desktop.video import load_vlc

    def gives_up():
        raise SystemExit(1)

    with pytest.raises(VlcUnavailable) as raised:
        load_vlc(prepare=lambda: None, import_vlc=gives_up)

    said = str(raised.value)
    assert "VLC" in said
    assert "console again" in said
    # And what it says is still a sentence, not what the library said on its
    # way out - which is the wording this whole change exists to stop showing.
    assert "libvlc.dll" not in said
    assert said.endswith(".")


def test_a_pane_that_cannot_find_vlc_says_so_instead_of_naming_a_file(qtbot) -> None:
    """What the operator reads when the search comes up empty - the sentence the
    field report was missing."""
    from vmd.desktop.app import pane_factory
    from vmd.desktop.libvlc import VlcUnavailable

    def no_vlc():
        raise VlcUnavailable("VLC is not installed on this machine. Do this instead.")

    pane = pane_factory(build=no_vlc)("thermal")
    qtbot.addWidget(pane)

    assert "VLC is not installed on this machine" in pane.text()
    assert "libvlc.dll" not in pane.text()
    assert pane.state == "stopped"


class _StubPlayer:
    """Stands in for the libVLC player so the reporting can be driven by hand."""

    def __init__(self, vlc_state, pictures: int) -> None:
        self.vlc_state = vlc_state
        self.pictures = pictures

    def get_state(self):
        return self.vlc_state

    def set_media(self, media) -> None:
        pass

    def set_hwnd(self, handle) -> None:
        pass

    def play(self) -> None:
        pass

    def get_media(self):
        return self

    def get_stats(self, stats) -> bool:
        stats.displayed_pictures = self.pictures
        return True

    def stop(self) -> None:
        pass


class _Releasable:
    """Counts what a released libVLC object was asked to do."""

    def __init__(self) -> None:
        self.releases = 0
        self.stops = 0

    def release(self) -> None:
        self.releases += 1

    def stop(self) -> None:
        self.stops += 1


def test_releasing_a_pane_hands_back_the_player_and_the_instance(qtbot) -> None:
    """Nothing in python-vlc frees anything when the object is collected, and
    the panes are rebuilt whenever the streams change. A pane that was only
    stopped keeps its decoder threads and its instance for the life of the
    process."""
    from vmd.desktop.video import VlcVideoPane

    pane = VlcVideoPane()
    qtbot.addWidget(pane)
    player, instance = _Releasable(), _Releasable()
    pane._player, pane._instance = player, instance

    pane.release()

    assert player.stops == 1
    assert player.releases == 1
    assert instance.releases == 1
    assert pane.state == "stopped"
    assert pane._poll.isActive() is False

    # And it is over. A second release is a double free inside a C library, and
    # showing again would hand a URL to a player that no longer exists.
    pane.release()
    pane.show("rtsp://127.0.0.1:1/nowhere")
    pane.stop()
    assert player.releases == 1
    assert instance.releases == 1
    assert player.stops == 1
    assert pane.state == "stopped"


def test_a_stream_vlc_has_given_up_on_is_reported_failed(qtbot) -> None:
    """VLC ends a dead RTSP session ~10 s after the source disappears. Ended is
    not Error, but it is just as final: no picture will ever arrive again."""
    import vlc

    from vmd.desktop.video import VlcVideoPane

    pane = VlcVideoPane()
    qtbot.addWidget(pane)
    pane._player = _StubPlayer(vlc.State.Playing, 10)
    pane.show("rtsp://127.0.0.1:1/nowhere")

    pane._sample()
    assert pane.state == "playing"

    pane._player.vlc_state = vlc.State.Ended
    assert pane.state == "failed"


def test_the_drain_at_teardown_is_not_mistaken_for_frames(qtbot) -> None:
    """Tearing the input down flushes the decoder: displayed_pictures leaps by
    a hundred-odd in one sample. Those pictures are not a live stream."""
    import vlc

    from vmd.desktop.video import VlcVideoPane

    pane = VlcVideoPane()
    qtbot.addWidget(pane)
    pane._player = _StubPlayer(vlc.State.Playing, 58)
    pane.show("rtsp://127.0.0.1:1/nowhere")
    pane._sample()
    assert pane.state == "playing"

    # The source dies. VLC keeps saying Playing, then gives up and drains.
    pane._player.vlc_state = vlc.State.Ended
    pane._player.pictures = 202
    pane._sample()

    assert pane.frames_seen == 58, "the drain was counted as frames arriving"
    assert pane.state == "failed"


@pytest.fixture
def a_recorded_minute(tmp_path: Path) -> Path:
    """A real file with a clock burned into it, sixty seconds long.

    Generated rather than recorded off a camera, because the assertion is about
    libVLC's position in a file and nothing about how the file was made. Sixty
    seconds is long enough that starting thirty in cannot be confused with
    starting at nought by any amount of buffering, and short enough to write in
    a couple of seconds.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    target = tmp_path / "minute.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=60",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", "15",
            "-pix_fmt", "yuv420p", "-y", str(target),
        ],
        check=True,
        timeout=120,
    )
    return target


@pytest.mark.integration
def test_the_real_pane_opens_a_file_where_it_was_asked_to(
    qtbot, a_recorded_minute: Path
) -> None:
    """The part a unit test cannot settle.

    `set_time` after `play()` is silently dropped on media that has not opened
    yet, so a pane that looked correct in every widget test would have played
    every segment from its first frame - which is what Playback did, and is why
    clicking 14:32 on the timeline could show you 14:27. What is asserted here
    is libVLC's own idea of where it is, not what the console asked for.
    """
    from vmd.desktop.video import VlcVideoPane

    pane = VlcVideoPane()
    qtbot.addWidget(pane)
    pane.resize(320, 240)
    pane.show_widget()

    pane.show(a_recorded_minute.as_uri(), at_seconds=30.0)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and pane.state != "playing":
        qtbot.wait(200)
    assert pane.state == "playing", "the pane never reported frames"

    where = pane._player.get_time() / 1000.0
    pane.stop()
    # Generous either way: a keyframe every second means the demuxer lands on
    # the one at or before 30 s, and the file has been playing for a moment by
    # the time this is read. What is being ruled out is nought.
    assert 27.0 <= where <= 40.0, f"asked for 30 s in and libVLC is at {where:.1f} s"


@pytest.mark.integration
def test_the_real_pane_still_starts_a_file_at_the_beginning_by_default(
    qtbot, a_recorded_minute: Path
) -> None:
    """The other half: nothing seeks unless it was asked to."""
    from vmd.desktop.video import VlcVideoPane

    pane = VlcVideoPane()
    qtbot.addWidget(pane)
    pane.resize(320, 240)
    pane.show_widget()

    pane.show(a_recorded_minute.as_uri())
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and pane.state != "playing":
        qtbot.wait(200)
    assert pane.state == "playing", "the pane never reported frames"

    where = pane._player.get_time() / 1000.0
    pane.stop()
    assert where < 10.0, f"nothing asked for a seek and libVLC is at {where:.1f} s"


@pytest.fixture
def a_stream_that_starts_black(tmp_path: Path):
    """A synthetic RTSP source whose first second fades up out of black.

    This is the fault as the field saw it: a stream that is perfectly healthy
    and whose FIRST decoded frame is black. `fade=t=in:st=0:d=1` makes frame one
    pure black and everything past a second the full test pattern, which is
    exactly the window `-frames:v 1` fell into and `-frames:v 20` steps over.
    """
    binary = find_binary()
    if binary is None or shutil.which("ffmpeg") is None:
        pytest.skip("needs go2rtc and ffmpeg")

    api, rtsp = free_port(), free_port()
    config = tmp_path / "black-first.json"
    config.write_text(
        json.dumps(
            {
                "api": {"listen": f"127.0.0.1:{api}"},
                "rtsp": {"listen": f"127.0.0.1:{rtsp}"},
                "webrtc": {"listen": ""},
                "log": {"level": "warn"},
                "streams": {
                    "thermal": (
                        "exec:ffmpeg -hide_banner -re -f lavfi "
                        "-i testsrc=size=640x512:rate=15 "
                        "-vf fade=t=in:st=0:d=1 -c:v libx264 "
                        "-preset ultrafast -tune zerolatency -g 15 -f rtsp {output}"
                    )
                },
            }
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [str(binary), "-c", str(config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    try:
        yield f"rtsp://127.0.0.1:{rtsp}/thermal"
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.integration
def test_the_grab_gets_a_picture_and_not_the_black_first_frame(
    a_stream_that_starts_black: str,
) -> None:
    """The operator's own report: thermal playing in the Live tab, PTZ working,
    and the picker a black rectangle. Against `-frames:v 1` this fails, which is
    the point of testing it against a real stream rather than a mock."""
    from PySide6.QtGui import QImage

    from vmd.desktop.picker import blankness, grab_frame, is_blank
    from vmd.settings import CameraSettings, Settings, StreamSettings

    settings = Settings(
        camera=CameraSettings(
            host="127.0.0.1",
            streams=[
                StreamSettings(name="thermal", url=a_stream_that_starts_black)
            ],
        )
    )
    data = grab_frame(settings, "thermal")
    image = QImage()
    assert image.loadFromData(data), "what came back was not a picture at all"
    assert not is_blank(image), (
        f"the grab kept a blank frame; it measures {blankness(image):.2f}"
    )
