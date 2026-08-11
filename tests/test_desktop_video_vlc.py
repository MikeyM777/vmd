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
