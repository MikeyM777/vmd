"""How far behind the camera each way of showing it is, side by side, on this machine.

Run it on the console machine, with VMD already open so the streaming server is
up, and give it the name of one of the camera's views:

    uv run python spike/probe_delay.py thermal

Four pictures of the same camera appear in one window, each fetched a different
way. Wave a hand in front of the camera, or watch a vehicle cross the scene, and
the one that shows it last is the one carrying the delay. That is the whole
method, and it is the only honest one available here: there is no clock in the
stream, nothing timestamps a frame on its way through, and every figure libVLC
reports about its own buffer is a figure about its intention rather than about
what is on the screen.

    1. the console          how VMD shows it now
    2. the old settings     what it did before the delay was looked at
    3. straight from camera  the same options, without the streaming server
    4. no hardware decode   the console's settings with the GPU left out

Why each one is there:

  1 against 2 says what changing the libVLC options bought. `--clock-jitter`
    defaulted to five seconds of tolerance for a source whose clock wanders, and
    bought that tolerance with buffer; `--live-caching` was never set at all, so
    the one caching figure that was set was being applied to a path that does
    not read it. Both are in `vmd/desktop/video.py:vlc_options` now.

  1 against 3 says what go2rtc costs. It exists because the camera hands out
    very few connections and the recorder needs one - so this is not an offer to
    remove it, it is the measurement that says whether removing it would be
    worth arguing about. Expect it to be small; if it is not, that is a finding.

  1 against 4 says what hardware decoding costs. It buffers a frame or two,
    which is real, and the console keeps it because two 4K pictures on a machine
    that also records and detects is what it is for. If 4 is visibly ahead of 1
    on this machine, that trade is worth reopening.

Nothing here writes anything, changes any setting, or touches the camera. It
opens four read-only players and closes them when the window closes.

If it will not start, the reason is almost always that VMD is not open: the
streaming server's port is written to streaming.json beside settings.json when
the console starts, and this reads it rather than guessing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.style import PALETTE, SIZE_BAND, SIZE_SMALL, stylesheet  # noqa: E402
from vmd.desktop.video import VlcVideoPane, vlc_options  # noqa: E402
from vmd.settings import load_settings  # noqa: E402
from vmd.streaming.endpoint import read_endpoint  # noqa: E402
from vmd.streaming.go2rtc import with_credentials  # noqa: E402

# What the console was doing before the delay was looked into: a caching figure
# chosen for a network stream and no opinion at all about the clock. Written out
# rather than imported, because the point of this pane is to be the old
# behaviour for ever, even after the current options change again.
OLD_OPTIONS = [
    "--network-caching=300",
    "--rtsp-tcp",
    "--no-audio",
    "--no-video-title-show",
    "--avcodec-hw=any",
]


def find_settings() -> Path:
    here = Path(__file__).resolve().parent.parent
    for candidate in (Path("settings.json"), here / "settings.json"):
        if candidate.exists():
            return candidate
    # The two-camera layout. Either is as good as the other for this: the
    # question is about the machine, not about which camera is on it.
    for candidate in sorted((here / "cameras").glob("*/settings.json")):
        return candidate
    raise SystemExit(
        "Could not find settings.json. Run this from the folder VMD is installed in."
    )


class Fixed(QWidget):
    """One pane, with the words for what it is and nothing else on it."""

    def __init__(self, title: str, note: str, options: list[str], url: str) -> None:
        super().__init__()
        column = QVBoxLayout(self)
        column.setContentsMargins(6, 6, 6, 6)
        column.setSpacing(4)

        heading = QLabel(title)
        heading.setStyleSheet(
            f"color: {PALETTE['ink']}; font-size: {SIZE_BAND}px; font-weight: 600;"
        )
        column.addWidget(heading)

        caption = QLabel(note)
        caption.setWordWrap(True)
        caption.setStyleSheet(f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;")
        column.addWidget(caption)

        # Built the way the console builds one, then handed different options.
        # `VlcVideoPane` takes a delay rather than a whole option list, so the
        # two panes that need something else than a delay reach past it - which
        # is a spike's privilege and is why this lives in spike/.
        self.pane = VlcVideoPane()
        self.pane._instance.release()
        import vlc

        self.pane._instance = vlc.Instance(options)
        self.pane._player = self.pane._instance.media_player_new()
        column.addWidget(self.pane, 1)
        self._url = url

    def start(self) -> None:
        self.pane.show_widget()
        self.pane.show(self._url)

    def stop(self) -> None:
        self.pane.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("view", help="the name of one of the camera's views")
    args = parser.parse_args()

    path = find_settings()
    settings = load_settings(path)
    wanted = [s for s in settings.camera.streams if s.name == args.view]
    if not wanted:
        names = ", ".join(s.name for s in settings.camera.streams) or "none at all"
        raise SystemExit(f"There is no view called {args.view!r}. This camera has: {names}")
    stream = wanted[0]

    endpoint = read_endpoint(path.parent / "streaming.json")
    if not endpoint:
        raise SystemExit(
            "The streaming server is not running, so there is nothing local to "
            "compare against. Open VMD first, leave it open, and run this again."
        )
    local = f"rtsp://127.0.0.1:{int(endpoint['rtsp_port'])}/{stream.name}"
    direct = with_credentials(stream.url, settings.camera.username, settings.camera.password)

    print(f"settings : {path}")
    print(f"view     : {stream.name}")
    print(f"local    : {local}")
    print("direct   : the camera's own address, with the credentials from settings")
    print()
    print("Wave a hand in front of the camera. The picture that shows it last is")
    print("the one carrying the delay. Close the window to stop.")
    print()

    app = QApplication(sys.argv)
    app.setStyleSheet(stylesheet())

    window = QWidget()
    window.setWindowTitle(f"VMD - where the delay is - {stream.name}")
    window.resize(1600, 950)
    grid = QGridLayout(window)
    grid.setSpacing(8)

    panes = [
        Fixed(
            "1. the console",
            "What VMD shows now: the delay from Settings, no clock allowance, "
            "both caching figures set.",
            vlc_options(settings.live_delay_ms),
            local,
        ),
        Fixed(
            "2. the old settings",
            "What it did before: 300 ms of network caching, libVLC's five-second "
            "clock allowance, and no live-caching at all.",
            OLD_OPTIONS,
            local,
        ),
        Fixed(
            "3. straight from the camera",
            "The console's options without the streaming server in the way. The "
            "difference from 1 is what go2rtc costs.",
            vlc_options(settings.live_delay_ms),
            direct,
        ),
        Fixed(
            "4. no hardware decode",
            "The console's options with the GPU left out. If this is ahead of 1, "
            "hardware decoding is buffering.",
            [o for o in vlc_options(settings.live_delay_ms) if not o.startswith("--avcodec-hw")]
            + ["--avcodec-hw=none"],
            local,
        ),
    ]
    for index, pane in enumerate(panes):
        grid.addWidget(pane, index // 2, index % 2)

    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    window.show()
    for pane in panes:
        pane.start()

    code = app.exec()
    for pane in panes:
        pane.stop()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
