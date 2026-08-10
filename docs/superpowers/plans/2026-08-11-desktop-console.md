# Desktop Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the browser console with a PySide6 desktop application that renders live video with libVLC, supervises go2rtc and the recorder as child processes, and carries Live, Playback, Settings and Logs.

**Architecture:** One window, two child processes. libVLC renders into Qt widgets through `set_hwnd`. go2rtc keeps a single connection to the camera and re-serves it on `127.0.0.1`; both the video panes and the recorder read from there. Everything below the interface layer — settings, storage, PTZ, radio, streaming, supervisor — is existing, tested code that never knew a browser existed and is reused unchanged.

**Tech Stack:** Python 3.11+, PySide6 6.11, python-vlc (libVLC), pytest + pytest-qt, existing `vmd.*` packages.

---

## Read this before starting

**The spec:** `docs/superpowers/specs/2026-08-11-desktop-console-design.md`.

**The rule that shapes the video code:** the pane *watches*; it does not intervene.
No stall timers, no reconnect on drift, no live-edge chasing. VLC handles its own
recovery. A stream is restarted only when VLC reports `Error`, or when the
operator changes it. Every disconnection reported from the field traced back to
recovery code firing too early. If you find yourself adding a timer that tears
down a stream, stop: that is the bug this rewrite exists to remove.

**Existing interfaces you will call.** Do not modify these; they have tests.

```python
# vmd/settings.py
load_settings(path) -> Settings          # missing file -> defaults
save_settings(settings, path) -> None    # atomic
Settings.camera.streams -> list[StreamSettings]   # .name .url .enabled .reader
Settings.storage.root, .budget_gb, .retention_days, .segment_seconds
Settings.bitrate.ceiling_kbps
Settings.radio.host, .username, .password, .enabled

# vmd/streaming/go2rtc.py
Go2rtcService(settings, config_path, binary, api_port=1984, rtsp_port=8554,
              webrtc_port=8555)          # .start() .stop() .running .status()
                                         # .ensure_running() .sources()
                                         # .local_rtsp_url(name) -> str
find_binary() -> Path | None

# vmd/ptz/service.py
PtzService(settings)                     # .status() .move(pan,tilt,zoom) .stop()
                                         # .home() .encoders() .set_encoder(...)
                                         # .fit_encoders_to_link(kbps) .apply(settings)

# vmd/radio/service.py
RadioService(settings)                   # .status() -> dict  .apply(settings)

# vmd/storage/index.py
SegmentIndex(db_path)                    # .all(stream=None) -> list[Segment]
                                         # .gaps(stream, start, end) -> [(s,e)]
Segment: .id .stream .path .start .end .size_bytes .duration

# vmd/supervisor.py
Supervisor([Managed(name, service)])     # .tick() -> list[str]  .stop_all()
Service protocol: .running (property), .start(), .stop()

# vmd/streaming/diagnose.py
diagnose(settings) -> list[str]
find_paths(settings, on_progress=None) -> list[str]

# vmd/webui/updater.py   (moves in Task 13, unchanged)
Updater(root)                            # .version() .start() .snapshot()
```

---

## File structure

| File | Responsibility |
|---|---|
| `vmd/desktop/style.py` | Palette constants and the Qt stylesheet |
| `vmd/desktop/video.py` | `VideoPane` protocol, `VlcVideoPane`, `FakeVideoPane` |
| `vmd/desktop/services.py` | Builds and supervises go2rtc + recorder; exposes state |
| `vmd/desktop/steering.py` | Pointer-to-velocity maths, no Qt |
| `vmd/desktop/live.py` | Live tab: video wall, steering overlay, side column |
| `vmd/desktop/timeline.py` | Timeline geometry and coverage maths, no Qt |
| `vmd/desktop/playback.py` | Playback tab |
| `vmd/desktop/settings_tab.py` | Settings tab |
| `vmd/desktop/logs.py` | Logs tab and the log buffer |
| `vmd/desktop/window.py` | Main window, tab assembly, status bar |
| `vmd/desktop/app.py` | Entry point: `python -m vmd.desktop` |

Pure logic (`steering.py`, `timeline.py`) is deliberately separate from widgets so
it is tested without a display.

---

## Task 1: Prove the overlay before building on it

The spec names overlay flicker over VLC's native surface as the one risk that
would change the design. Settle it first, in a throwaway script, before any
structure depends on it.

**Files:**
- Create: `spike/overlay_probe.py`

- [ ] **Step 1: Add python-vlc to the project**

```bash
cd C:\dev\VMD
uv add python-vlc
```

Expected: `pyproject.toml` gains `python-vlc`, and `uv.lock` updates.

- [ ] **Step 2: Write the probe**

```python
"""Does a transparent Qt widget survive on top of libVLC's video surface?

The Live tab wants the pointer over the picture for steering, which means a
widget above VLC's own output. On Windows that is not guaranteed to composite
cleanly. Ten minutes here decides whether the steering overlay is possible or
whether steering moves to a side strip.

Run:  uv run python spike/overlay_probe.py rtsp://127.0.0.1:8554/thermal
"""

from __future__ import annotations

import sys

import vlc
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


class Overlay(QWidget):
    """A transparent widget that draws a moving box and some text."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.offset = 0
        timer = QTimer(self)
        timer.timeout.connect(self._advance)
        timer.start(33)

    def _advance(self) -> None:
        self.offset = (self.offset + 4) % max(1, self.width())
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#EEBB58"), 2))
        painter.drawRect(self.offset, 40, 160, 120)
        painter.drawText(20, 24, "overlay: if this flickers, say so")


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    if not url:
        print("give an rtsp:// or file url as the argument")
        return 1

    app = QApplication(sys.argv)
    window = QWidget()
    window.resize(960, 600)
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)

    surface = QWidget()
    surface.setStyleSheet("background: #050607;")
    layout.addWidget(surface, 1)
    layout.addWidget(QLabel("move the mouse over the picture; watch for tearing"))

    window.show()

    instance = vlc.Instance(["--no-audio", "--rtsp-tcp", "--network-caching=300"])
    player = instance.media_player_new()
    player.set_media(instance.media_new(url))
    player.set_hwnd(int(surface.winId()))
    player.play()

    overlay = Overlay(surface)
    overlay.setGeometry(surface.rect())
    overlay.raise_()
    overlay.show()

    def keep_covering() -> None:
        overlay.setGeometry(surface.rect())
        overlay.raise_()

    resize_timer = QTimer()
    resize_timer.timeout.connect(keep_covering)
    resize_timer.start(500)

    code = app.exec()
    player.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Run it against a real stream**

Start a synthetic camera and the console's streaming server, or point it at the
real one. With the field camera available:

Run: `uv run python spike/overlay_probe.py rtsp://127.0.0.1:8554/thermal`

Expected: video plays, an amber box slides across it, and the box does not
flicker, tear, or disappear behind the video when the window is moved or resized.

- [ ] **Step 4: Record the verdict in the spec**

Append to `docs/superpowers/specs/2026-08-11-desktop-console-design.md` under
Risks, replacing the overlay row's Response text with what actually happened —
either `Verified on <date>: overlay composites cleanly over libVLC on this
machine.` or `Verified on <date>: overlay flickers; steering moves to a side
strip (Task 7 alternative).`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock spike/overlay_probe.py docs/superpowers/specs/2026-08-11-desktop-console-design.md
git commit -m "Prove the video overlay before building the Live tab on it"
```

---

## Task 2: The palette and stylesheet

Qt cannot parse OKLCH, so the DESIGN.md tokens are converted once, here, and
never guessed again.

**Files:**
- Create: `vmd/desktop/__init__.py`, `vmd/desktop/style.py`
- Test: `tests/test_desktop_style.py`

- [ ] **Step 1: Write the failing test**

```python
"""The palette: converted once from DESIGN.md, and never guessed at again."""

from __future__ import annotations

import re

from vmd.desktop.style import PALETTE, stylesheet


def test_every_token_from_the_design_system_is_present() -> None:
    expected = {
        "bg", "surface", "raised", "well", "line", "line_strong",
        "ink", "muted", "ok", "warn", "alarm", "accent",
    }
    assert set(PALETTE) == expected


def test_every_colour_is_a_hex_value_qt_can_parse() -> None:
    for name, value in PALETTE.items():
        assert re.fullmatch(r"#[0-9A-F]{6}", value), f"{name} is not a Qt hex colour"


def test_the_video_well_is_the_darkest_surface() -> None:
    """DESIGN.md: nothing except video is this dark."""
    def brightness(hex_colour: str) -> int:
        return sum(int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))

    assert brightness(PALETTE["well"]) < brightness(PALETTE["bg"])


def test_the_stylesheet_uses_the_palette_and_square_corners() -> None:
    sheet = stylesheet()
    assert PALETTE["bg"] in sheet
    assert PALETTE["accent"] in sheet
    assert "border-radius: 0" in sheet
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_style.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.desktop'`

- [ ] **Step 3: Write the implementation**

Create `vmd/desktop/__init__.py`:

```python
"""The desktop console."""
```

Create `vmd/desktop/style.py`:

```python
"""The visual system from DESIGN.md, in the form Qt understands.

DESIGN.md is written in OKLCH because that is how the colours were chosen. Qt
stylesheets cannot parse OKLCH, so they are converted once here. The conversions
are exact; if a colour changes in DESIGN.md it is converted again rather than
adjusted by eye.
"""

from __future__ import annotations

# oklch(L C H) -> sRGB, converted from the table in DESIGN.md.
PALETTE: dict[str, str] = {
    "bg": "#1B1D20",           # oklch(0.23 0.006 265)
    "surface": "#27292C",      # oklch(0.28 0.007 265)
    "raised": "#33353A",       # oklch(0.33 0.008 265)
    "well": "#050607",         # oklch(0.12 0.005 265) - video only
    "line": "#45484D",         # oklch(0.40 0.010 265)
    "line_strong": "#656970",  # oklch(0.52 0.012 265)
    "ink": "#F4F5F7",          # oklch(0.97 0.003 265)
    "muted": "#B4B7BE",        # oklch(0.78 0.010 265)
    "ok": "#6ED889",           # oklch(0.80 0.15 150)
    "warn": "#FFBC56",         # oklch(0.84 0.14 75)
    "alarm": "#FF534B",        # oklch(0.68 0.21 27)
    "accent": "#EEBB58",       # oklch(0.82 0.13 82) - interactive emphasis only
}

MONO = '"Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace'


def stylesheet() -> str:
    """The whole application's appearance.

    Radius 0 throughout: square corners are the strongest single signal
    separating equipment from web application, and they cost nothing.
    """
    p = PALETTE
    return f"""
QWidget {{
    background: {p["bg"]};
    color: {p["ink"]};
    font-size: 12pt;
}}
QTabWidget::pane {{ border: 1px solid {p["line"]}; }}
QTabBar::tab {{
    background: {p["surface"]};
    color: {p["muted"]};
    padding: 7px 16px;
    border: 1px solid transparent;
    border-radius: 0;
}}
QTabBar::tab:selected {{
    background: {p["raised"]};
    color: {p["ink"]};
    border-color: {p["line"]};
}}
QGroupBox {{
    border: 1px solid {p["line"]};
    border-radius: 0;
    margin-top: 14px;
    padding-top: 8px;
}}
QGroupBox::title {{
    color: {p["muted"]};
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}}
QPushButton {{
    background: {p["surface"]};
    color: {p["ink"]};
    border: 1px solid {p["line"]};
    border-radius: 0;
    padding: 6px 13px;
}}
QPushButton:hover {{ background: {p["raised"]}; }}
QPushButton:focus {{ border-color: {p["accent"]}; }}
QPushButton:disabled {{ color: {p["muted"]}; }}
QLineEdit, QComboBox, QSpinBox {{
    background: {p["raised"]};
    color: {p["ink"]};
    border: 1px solid {p["line"]};
    border-radius: 0;
    padding: 5px 7px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {p["accent"]}; }}
QTableWidget, QTableView, QPlainTextEdit {{
    background: {p["well"]};
    border: 1px solid {p["line"]};
    border-radius: 0;
    font-family: {MONO};
}}
QHeaderView::section {{
    background: {p["surface"]};
    color: {p["muted"]};
    border: 0;
    border-bottom: 1px solid {p["line"]};
    padding: 4px 6px;
}}
QSplitter::handle {{ background: {p["line_strong"]}; }}
QStatusBar {{ background: {p["surface"]}; color: {p["muted"]}; }}
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_desktop_style.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add vmd/desktop/__init__.py vmd/desktop/style.py tests/test_desktop_style.py
git commit -m "Convert the design system into a Qt stylesheet"
```

---

## Task 3: The video pane and its fake

**Files:**
- Create: `vmd/desktop/video.py`
- Test: `tests/test_desktop_video.py`

- [ ] **Step 1: Write the failing test**

```python
"""The video pane contract, exercised through the fake.

The real pane needs a display and a stream; everything that consumes video is
tested against the fake instead, which is why the fake is production code rather
than a test fixture.
"""

from __future__ import annotations

import pytest

from vmd.desktop.video import FakeVideoPane


def test_a_new_pane_is_stopped() -> None:
    assert FakeVideoPane().state == "stopped"


def test_showing_a_url_connects_then_plays() -> None:
    pane = FakeVideoPane()
    pane.show("rtsp://127.0.0.1:8554/thermal")
    assert pane.state == "connecting"
    assert pane.url == "rtsp://127.0.0.1:8554/thermal"

    pane.pretend_playing()
    assert pane.state == "playing"


def test_stopping_forgets_the_stream() -> None:
    pane = FakeVideoPane()
    pane.show("rtsp://x/y")
    pane.pretend_playing()
    pane.stop()
    assert pane.state == "stopped"
    assert pane.url is None


def test_a_pane_that_goes_quiet_is_late_and_nothing_else_happens() -> None:
    """The rule of this rewrite: the pane reports; it does not intervene."""
    pane = FakeVideoPane()
    pane.show("rtsp://x/y")
    pane.pretend_playing()
    pane.pretend_late()
    assert pane.state == "late"
    assert pane.url == "rtsp://x/y", "a late stream must not be torn down"
    assert pane.restarts == 0


def test_only_a_failure_counts_as_a_failure() -> None:
    pane = FakeVideoPane()
    pane.show("rtsp://x/y")
    pane.pretend_failed()
    assert pane.state == "failed"


def test_showing_a_new_url_replaces_the_old_one() -> None:
    pane = FakeVideoPane()
    pane.show("rtsp://x/one")
    pane.pretend_playing()
    pane.show("rtsp://x/two")
    assert pane.url == "rtsp://x/two"
    assert pane.state == "connecting"
    assert pane.restarts == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_video.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.desktop.video'`

- [ ] **Step 3: Write the implementation**

Create `vmd/desktop/video.py`:

```python
"""Showing a stream, and saying what it is doing - nothing more.

The pane watches; it does not intervene. VLC recovers from its own trouble far
better than the timers that used to sit here, and every disconnection reported
from the field traced back to one of those timers firing early. A stream is
restarted when VLC reports an error, or when the operator changes it. Never
because a frame was late.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

PaneState = Literal["stopped", "connecting", "playing", "late", "failed"]

# A stream that has produced nothing for this long is reported as late. It is
# not touched: this number exists to put a word on the screen, not to trigger
# anything.
LATE_AFTER_SECONDS = 8.0


@runtime_checkable
class VideoPane(Protocol):
    """Anything that can show one stream."""

    def show(self, url: str) -> None: ...

    def stop(self) -> None: ...

    @property
    def state(self) -> PaneState: ...


class FakeVideoPane:
    """A pane with no video in it, for testing everything that uses one."""

    def __init__(self) -> None:
        self.url: str | None = None
        self.restarts = 0
        self._state: PaneState = "stopped"

    @property
    def state(self) -> PaneState:
        return self._state

    def show(self, url: str) -> None:
        if self.url is not None:
            self.restarts += 1
        self.url = url
        self._state = "connecting"

    def stop(self) -> None:
        self.url = None
        self._state = "stopped"

    # -- test control -----------------------------------------------------
    def pretend_playing(self) -> None:
        self._state = "playing"

    def pretend_late(self) -> None:
        self._state = "late"

    def pretend_failed(self) -> None:
        self._state = "failed"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_desktop_video.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add vmd/desktop/video.py tests/test_desktop_video.py
git commit -m "Add the video pane contract and the fake that stands in for it"
```

---

## Task 4: The libVLC pane

**Files:**
- Modify: `vmd/desktop/video.py`
- Test: `tests/test_desktop_video_vlc.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_video_vlc.py -v`
Expected: FAIL with `ImportError: cannot import name 'VlcVideoPane'`

- [ ] **Step 3: Write the implementation**

Append to `vmd/desktop/video.py`:

```python
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

VLC_OPTIONS = [
    # The source is on this machine, so this absorbs the laptop and nothing
    # else; the link's jitter was already absorbed by go2rtc.
    "--network-caching=300",
    "--rtsp-tcp",       # what both VLC and go2rtc negotiate anyway
    "--no-audio",       # never listened to: one less decode, one less failure
    "--no-video-title-show",
    "--avcodec-hw=any",  # hardware decode where the laptop offers it
]


class VlcVideoPane(QWidget):
    """A widget libVLC draws into."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        import vlc  # imported here so the module imports without libVLC present

        self.setStyleSheet("background: #050607;")
        self._vlc = vlc
        self._instance = vlc.Instance(VLC_OPTIONS)
        self._player = self._instance.media_player_new()
        self._url: str | None = None
        self._started_at = 0.0
        self._last_frame_at = 0.0
        self.frames_seen = 0
        self._last_count = -1

        # Polled rather than driven by VLC events: libVLC delivers events on its
        # own threads, and touching Qt from those is how a UI toolkit crashes.
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._sample)
        self._poll.start(250)

    def show_widget(self) -> None:
        """Realise the widget so it has a window handle for VLC to draw into."""
        super().show()

    def show(self, url: str) -> None:  # noqa: A003 - the protocol's name
        self._url = url
        self._started_at = time.monotonic()
        self._last_frame_at = 0.0
        self._last_count = -1
        media = self._instance.media_new(url)
        self._player.set_media(media)
        self._attach_surface()
        self._player.play()
        logger.info("showing %s", url)

    def stop(self) -> None:
        self._url = None
        self._player.stop()

    @property
    def state(self) -> PaneState:
        if self._url is None:
            return "stopped"
        if self._player.get_state() == self._vlc.State.Error:
            return "failed"
        if self._last_frame_at == 0.0:
            return "connecting"
        if time.monotonic() - self._last_frame_at > LATE_AFTER_SECONDS:
            return "late"
        return "playing"

    def _attach_surface(self) -> None:
        handle = int(self.winId())
        if hasattr(self._player, "set_hwnd"):
            self._player.set_hwnd(handle)
        else:  # pragma: no cover - not the deployment platform
            self._player.set_xwindow(handle)

    def _sample(self) -> None:
        """Count decoded frames. This is the only truth about whether a picture
        is arriving: VLC's state says Playing long after the pictures stop."""
        if self._url is None:
            return
        stats = self._vlc.MediaStats()
        media = self._player.get_media()
        if media is None or not media.get_stats(stats):
            return
        count = stats.displayed_pictures
        if count != self._last_count:
            self._last_count = count
            self.frames_seen = count
            self._last_frame_at = time.monotonic()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_desktop_video_vlc.py -v`
Expected: PASS, 1 test, taking roughly 10 seconds

- [ ] **Step 5: Commit**

```bash
git add vmd/desktop/video.py tests/test_desktop_video_vlc.py
git commit -m "Render live video with libVLC, and let VLC do its own recovery"
```

---

## Task 5: Supervising the child processes

**Files:**
- Create: `vmd/desktop/services.py`
- Test: `tests/test_desktop_services.py`

- [ ] **Step 1: Write the failing test**

```python
"""go2rtc and the recorder as children of the window - and outliving it."""

from __future__ import annotations

from pathlib import Path

from vmd.desktop.services import ConsoleServices, RecorderProcess
from vmd.settings import CameraSettings, Settings, StorageSettings, StreamSettings


class FakeProcess:
    def __init__(self) -> None:
        self.alive = True
        self.terminated = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def kill(self) -> None:
        self.alive = False

    def wait(self, timeout=None):
        return 0


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name="thermal", url="rtsp://10.0.0.2/t", enabled=True)],
        ),
        storage=StorageSettings(root=tmp_path / "rec"),
    )


def test_the_recorder_is_started_as_its_own_process(tmp_path: Path) -> None:
    spawned: list[list[str]] = []
    recorder = RecorderProcess(
        settings_path=tmp_path / "settings.json",
        spawn=lambda command: (spawned.append(command), FakeProcess())[1],
    )
    recorder.start()
    assert recorder.running is True
    assert any("vmd.record_main" in part for part in spawned[0])
    assert any(str(tmp_path / "settings.json") in part for part in spawned[0])


def test_a_dead_recorder_is_restarted_by_a_tick(tmp_path: Path) -> None:
    processes: list[FakeProcess] = []

    def spawn(command):
        process = FakeProcess()
        processes.append(process)
        return process

    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=tmp_path / "settings.json",
        streaming=None,
        recorder=RecorderProcess(tmp_path / "settings.json", spawn=spawn),
    )
    services.start()
    assert services.recorder.running is True

    processes[0].alive = False          # it died on its own
    assert services.recorder.running is False
    services.tick()
    assert services.recorder.running is True
    assert len(processes) == 2


def test_stopping_the_console_stops_its_children(tmp_path: Path) -> None:
    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=tmp_path / "settings.json",
        streaming=None,
        recorder=RecorderProcess(tmp_path / "settings.json", spawn=lambda c: FakeProcess()),
    )
    services.start()
    services.stop()
    assert services.recorder.running is False


def test_a_recorder_left_running_is_adopted_not_duplicated(tmp_path: Path) -> None:
    """Children outlive the window on purpose. Opening the window again must not
    start a second recorder on the same directory - two of them would fight over
    the same files and the same index."""
    import os

    settings_path = tmp_path / "settings.json"
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")  # a PID that is alive

    spawned: list = []
    recorder = RecorderProcess(
        settings_path,
        pid_path=pid_file,
        spawn=lambda command: (spawned.append(command), FakeProcess())[1],
    )
    recorder.start()
    assert recorder.running is True, "the live process should be adopted"
    assert spawned == [], "nothing new should have been started"


def test_a_stale_pid_file_does_not_stop_a_start(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text("999999", encoding="utf-8")  # nothing is running there

    spawned: list = []
    recorder = RecorderProcess(
        settings_path,
        pid_path=pid_file,
        spawn=lambda command: (spawned.append(command), FakeProcess())[1],
    )
    recorder.start()
    assert recorder.running is True
    assert len(spawned) == 1, "a dead PID must not block recording forever"


def test_state_reports_what_the_operator_needs_to_know(tmp_path: Path) -> None:
    services = ConsoleServices(
        settings=settings_for(tmp_path),
        settings_path=tmp_path / "settings.json",
        streaming=None,
        recorder=RecorderProcess(tmp_path / "settings.json", spawn=lambda c: FakeProcess()),
    )
    services.start()
    state = services.state()
    assert state["recording"] is True
    assert "streaming" in state
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_services.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.desktop.services'`

- [ ] **Step 3: Write the implementation**

Create `vmd/desktop/services.py`:

```python
"""The processes the window looks after, and the state it reports about them.

Recording does not belong to the window. It is a separate process so that a
crash in the video pane, or the operator closing the window, cannot stop the
disk filling - which was the first requirement this system was given.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from vmd.settings import Settings
from vmd.streaming.endpoint import is_live, read_endpoint
from vmd.streaming.go2rtc import Go2rtcService
from vmd.supervisor import Managed, Supervisor

logger = logging.getLogger(__name__)


class RecorderProcess:
    """`python -m vmd.record_main`, shaped to fit the supervisor's protocol.

    A PID file makes the process findable across window lifetimes. Recording is
    meant to outlive the window, which means the next window must be able to
    tell "already recording" from "not recording" - otherwise it starts a second
    recorder on the same directory, and two of them fight over the same files
    and the same index.
    """

    def __init__(self, settings_path: str | Path, pid_path: str | Path | None = None, spawn=None) -> None:
        self.settings_path = Path(settings_path)
        self.pid_path = Path(pid_path) if pid_path else self.settings_path.parent / "recorder.pid"
        self._spawn = spawn or _default_spawn
        self._process: subprocess.Popen | None = None
        self._adopted_pid: int | None = None

    @property
    def running(self) -> bool:
        if self._process is not None:
            return self._process.poll() is None
        if self._adopted_pid is not None:
            return _pid_alive(self._adopted_pid)
        return False

    def start(self) -> None:
        if self.running:
            return

        adopted = self._read_pid()
        if adopted is not None and _pid_alive(adopted):
            logger.info("a recorder is already running (pid %s); adopting it", adopted)
            self._adopted_pid = adopted
            return
        self._adopted_pid = None

        command = [
            sys.executable,
            "-m",
            "vmd.record_main",
            "--settings",
            str(self.settings_path),
        ]
        try:
            self._process = self._spawn(command)
        except OSError:
            logger.exception("could not start the recorder")
            self._process = None
            return
        self._write_pid()
        logger.info("recorder started")

    def _read_pid(self) -> int | None:
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _write_pid(self) -> None:
        pid = getattr(self._process, "pid", None)
        if pid is None:
            return
        try:
            self.pid_path.parent.mkdir(parents=True, exist_ok=True)
            self.pid_path.write_text(str(pid), encoding="utf-8")
        except OSError:
            logger.warning("could not write %s", self.pid_path, exc_info=True)

    def stop(self) -> None:
        """Stop a recorder this object started.

        An adopted one is left alone: it belongs to a window that is gone, and
        killing it here would stop recording because someone closed a second
        window.
        """
        if self._process is None and self._adopted_pid is not None:
            self._adopted_pid = None
            return
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Never forget a process that may still be writing: a second
                    # recorder on the same directory would fight the first.
                    logger.error("the recorder did not stop; leaving it tracked")
                    return
        self._process = None


def _pid_alive(pid: int) -> bool:
    """Is that process still there? Cheap, and does not require ownership."""
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    try:  # pragma: no cover - not the deployment platform
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _default_spawn(command: list[str]) -> subprocess.Popen:
    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


class ConsoleServices:
    """Everything the window starts and watches."""

    def __init__(
        self,
        settings: Settings,
        settings_path: str | Path,
        streaming: Go2rtcService | None,
        recorder: RecorderProcess,
    ) -> None:
        self.settings = settings
        self.settings_path = Path(settings_path)
        self.streaming = streaming
        self.recorder = recorder
        self.adopted_streaming = False

        managed = [Managed(name="recorder", service=recorder)]
        if streaming is not None:
            managed.insert(0, Managed(name="streaming", service=streaming))
        self.supervisor = Supervisor(managed)

    def start(self) -> None:
        """Bring the children up, adopting any that are already running.

        go2rtc writes where it is listening; if that server is still answering
        it is used as it stands. Starting a second one would open a second
        connection to the camera, which is the cost this whole arrangement
        exists to avoid.
        """
        if self.streaming is not None:
            endpoint = read_endpoint(self.settings_path.parent / "streaming.json")
            if endpoint and is_live(endpoint):
                logger.info("a streaming server is already running; adopting it")
                self.streaming.api_port = int(endpoint.get("api_port", self.streaming.api_port))
                self.streaming.rtsp_port = int(endpoint.get("rtsp_port", self.streaming.rtsp_port))
                self.adopted_streaming = True
            else:
                self.adopted_streaming = False
                self.streaming.start()
        self.recorder.start()

    def tick(self) -> list[str]:
        """Restart whatever has died. Called on a timer by the window."""
        return self.supervisor.tick()

    def stop(self) -> None:
        self.supervisor.stop_all()

    def local_url(self, stream_name: str) -> str | None:
        if self.streaming is None:
            return None
        return self.streaming.local_rtsp_url(stream_name)

    def state(self) -> dict:
        streaming_state = "not enabled"
        if self.streaming is not None:
            streaming_state = self.streaming.status().reason
        return {
            "recording": self.recorder.running,
            "streaming": streaming_state,
            "restarts": dict(self.supervisor.restarts),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_desktop_services.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add vmd/desktop/services.py tests/test_desktop_services.py
git commit -m "Run go2rtc and the recorder as supervised children of the console"
```

---

## Task 6: Steering maths, without Qt

**Files:**
- Create: `vmd/desktop/steering.py`
- Test: `tests/test_desktop_steering.py`

- [ ] **Step 1: Write the failing test**

```python
"""Turning keys and pointer positions into camera speeds."""

from __future__ import annotations

import pytest

from vmd.desktop.steering import EDGE_FRACTION, edge_velocity, key_velocity


def test_no_keys_is_no_movement() -> None:
    assert key_velocity(set(), fine=False) == (0.0, 0.0)


def test_one_key_moves_one_axis() -> None:
    assert key_velocity({"right"}, fine=False) == (0.5, 0.0)
    assert key_velocity({"up"}, fine=False) == (0.0, 0.5)


def test_two_keys_move_both_axes_at_once() -> None:
    """Holding up and right must go diagonally, not pick a winner."""
    assert key_velocity({"up", "right"}, fine=False) == (0.5, 0.5)


def test_opposing_keys_cancel() -> None:
    assert key_velocity({"up", "down"}, fine=False) == (0.0, 0.0)
    assert key_velocity({"left", "right"}, fine=False) == (0.0, 0.0)


def test_fine_movement_is_slower_on_every_axis() -> None:
    fast = key_velocity({"up", "right"}, fine=False)
    slow = key_velocity({"up", "right"}, fine=True)
    assert 0 < slow[0] < fast[0]
    assert 0 < slow[1] < fast[1]


def test_the_middle_of_the_picture_does_not_steer() -> None:
    assert edge_velocity(0.5, 0.5) == (0.0, 0.0)


def test_the_edge_steers_and_the_speed_grows_with_depth() -> None:
    shallow = edge_velocity(1.0 - EDGE_FRACTION + 0.001, 0.5)
    deep = edge_velocity(0.999, 0.5)
    assert shallow[0] > 0 and deep[0] > shallow[0]
    assert deep[0] <= 1.0


def test_up_is_positive_tilt_wherever_it_comes_from() -> None:
    """Screen coordinates grow downwards; the camera does not."""
    pan, tilt = edge_velocity(0.5, 0.001)
    assert tilt > 0
    pan, tilt = edge_velocity(0.5, 0.999)
    assert tilt < 0


def test_a_corner_steers_both_axes() -> None:
    pan, tilt = edge_velocity(0.999, 0.001)
    assert pan > 0 and tilt > 0


@pytest.mark.parametrize("x,y", [(-0.5, 0.5), (1.5, 0.5), (0.5, -2.0), (0.5, 9.9)])
def test_a_pointer_outside_the_picture_is_clamped_not_amplified(x: float, y: float) -> None:
    pan, tilt = edge_velocity(x, y)
    assert -1.0 <= pan <= 1.0
    assert -1.0 <= tilt <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_steering.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.desktop.steering'`

- [ ] **Step 3: Write the implementation**

Create `vmd/desktop/steering.py`:

```python
"""Keys and pointer positions to camera speeds. No Qt, no camera - just maths.

Speeds are -1..1 because that is what ONVIF takes. Pan and tilt are computed on
separate axes so that holding two keys goes diagonally instead of the last key
winning, and so that opposing keys cancel the way the head physically would.
"""

from __future__ import annotations

# The outer band of the picture that steers. Inside it, speed grows with depth,
# so a nudge and a fast slew are the same gesture rather than two modes.
EDGE_FRACTION = 0.14

NORMAL_SPEED = 0.5
FINE_SPEED = 0.08


def key_velocity(held: set[str], fine: bool) -> tuple[float, float]:
    """(pan, tilt) for the arrow keys currently held.

    `held` contains any of "left", "right", "up", "down".
    """
    speed = FINE_SPEED if fine else NORMAL_SPEED
    pan = (-1 if "left" in held else 0) + (1 if "right" in held else 0)
    tilt = (-1 if "down" in held else 0) + (1 if "up" in held else 0)
    return (pan * speed, tilt * speed)


def edge_velocity(x: float, y: float) -> tuple[float, float]:
    """(pan, tilt) for a pointer at fractional position (x, y) in the picture.

    (0, 0) is the top-left corner. Tilt is inverted because screen coordinates
    grow downwards and cameras do not.
    """
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)

    def component(position: float) -> float:
        if position < EDGE_FRACTION:
            return -(EDGE_FRACTION - position) / EDGE_FRACTION
        if position > 1.0 - EDGE_FRACTION:
            return (position - (1.0 - EDGE_FRACTION)) / EDGE_FRACTION
        return 0.0

    pan = component(x)
    tilt = -component(y)
    return (round(pan, 3), round(tilt, 3))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_desktop_steering.py -v`
Expected: PASS, 12 tests

- [ ] **Step 5: Commit**

```bash
git add vmd/desktop/steering.py tests/test_desktop_steering.py
git commit -m "Add steering maths as plain functions, tested without a display"
```

---

## Task 7: The Live tab

**Files:**
- Create: `vmd/desktop/live.py`
- Test: `tests/test_desktop_live.py`

If Task 1 found that the overlay flickers, build the steering controls as a
column to the right of the video instead of an overlay: keep every signal
connection in this task identical and only change where the widget is added.

- [ ] **Step 1: Write the failing test**

```python
"""The Live tab, driven against fake panes and a fake PTZ."""

from __future__ import annotations

from vmd.desktop.live import LiveTab
from vmd.desktop.video import FakeVideoPane
from vmd.settings import CameraSettings, Settings, StreamSettings


class FakePtz:
    def __init__(self) -> None:
        self.commands: list[tuple] = []

    def status(self) -> dict:
        return {"available": True, "reason": "ready"}

    def move(self, pan: float, tilt: float, zoom: float) -> dict:
        self.commands.append(("move", pan, tilt, zoom))
        return {"ok": True}

    def stop(self) -> dict:
        self.commands.append(("stop",))
        return {"ok": True}

    def home(self) -> dict:
        self.commands.append(("home",))
        return {"ok": True}


def settings_with(*names: str) -> Settings:
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(name=name, url=f"rtsp://10.0.0.2/{name}", enabled=True)
                for name in names
            ],
        )
    )


def build(qtbot, *names: str):
    ptz = FakePtz()
    panes: dict[str, FakeVideoPane] = {}

    def make_pane(name: str) -> FakeVideoPane:
        panes[name] = FakeVideoPane()
        return panes[name]

    tab = LiveTab(ptz=ptz, make_pane=make_pane, local_url=lambda name: f"rtsp://127.0.0.1:8554/{name}")
    qtbot.addWidget(tab)
    tab.apply(settings_with(*names))
    return tab, ptz, panes


def test_a_pane_appears_for_every_enabled_stream(qtbot) -> None:
    tab, _, panes = build(qtbot, "thermal", "visible")
    assert set(panes) == {"thermal", "visible"}


def test_panes_are_pointed_at_the_local_server_not_the_camera(qtbot) -> None:
    """One connection crosses the radio link, and it is not this one."""
    tab, _, panes = build(qtbot, "thermal")
    assert panes["thermal"].url == "rtsp://127.0.0.1:8554/thermal"


def test_arrow_keys_move_the_camera_and_release_stops_it(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    tab.key_down("right", fine=False)
    assert ptz.commands[-1] == ("move", 0.5, 0.0, 0.0)
    tab.key_down("up", fine=False)
    assert ptz.commands[-1] == ("move", 0.5, 0.5, 0.0)
    tab.key_up("up")
    assert ptz.commands[-1] == ("move", 0.5, 0.0, 0.0)
    tab.key_up("right")
    assert ptz.commands[-1] == ("stop",)


def test_home_is_sent_once(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    tab.go_home()
    assert ptz.commands == [("home",)]


def test_the_same_velocity_is_not_sent_twice(qtbot) -> None:
    """Repeat key events must not become a command storm on the link."""
    tab, ptz, _ = build(qtbot, "thermal")
    tab.key_down("right", fine=False)
    tab.key_down("right", fine=False)
    assert len(ptz.commands) == 1


def test_a_late_stream_is_reported_and_left_alone(qtbot) -> None:
    tab, _, panes = build(qtbot, "thermal")
    panes["thermal"].pretend_playing()
    panes["thermal"].pretend_late()
    tab.refresh()
    assert "late" in tab.stream_status_text("thermal").lower()
    assert panes["thermal"].restarts == 0


def test_a_failed_stream_is_restarted(qtbot) -> None:
    tab, _, panes = build(qtbot, "thermal")
    panes["thermal"].pretend_failed()
    tab.refresh()
    assert panes["thermal"].restarts == 1


def test_changing_the_streams_replaces_the_panes(qtbot) -> None:
    tab, _, panes = build(qtbot, "thermal")
    tab.apply(settings_with("thermal", "visible"))
    assert set(panes) == {"thermal", "visible"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_live.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.desktop.live'`

- [ ] **Step 3: Write the implementation**

Create `vmd/desktop/live.py`:

```python
"""The Live tab: the pictures, and the controls that move the camera."""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.steering import edge_velocity, key_velocity
from vmd.desktop.video import VideoPane
from vmd.settings import Settings

logger = logging.getLogger(__name__)


class LiveTab(QWidget):
    """Video wall plus steering.

    `make_pane` and `local_url` are injected so the whole tab can be tested with
    fakes: one needs a display and a stream, the other needs a running server.
    """

    def __init__(
        self,
        ptz,
        make_pane: Callable[[str], VideoPane],
        local_url: Callable[[str], str | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ptz = ptz
        self._make_pane = make_pane
        self._local_url = local_url
        self._panes: dict[str, VideoPane] = {}
        self._status: dict[str, str] = {}
        self._held: set[str] = set()
        self._last_velocity: tuple[float, float, float] | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._wall = QSplitter(Qt.Horizontal)
        layout.addWidget(self._wall, 1)

        side = QWidget()
        side.setFixedWidth(292)
        self._side_layout = QVBoxLayout(side)
        self._moving = QLabel("idle")
        self._ptz_note = QLabel("")
        self._ptz_note.setWordWrap(True)
        steering_box = QGroupBox("Steering")
        steering_layout = QVBoxLayout(steering_box)
        steering_layout.addWidget(QLabel("Arrow keys pan and tilt. Shift for fine."))
        steering_layout.addWidget(self._moving)
        steering_layout.addWidget(self._ptz_note)
        self._side_layout.addWidget(steering_box)
        self._side_layout.addStretch(1)
        layout.addWidget(side)

    # ---------------------------------------------------------------- streams

    def apply(self, settings: Settings) -> None:
        """Build a pane for every enabled stream, replacing whatever was there."""
        for pane in self._panes.values():
            pane.stop()
            if isinstance(pane, QWidget):
                pane.setParent(None)
        self._panes.clear()
        self._status.clear()

        for stream in settings.camera.streams:
            if not (stream.enabled and stream.url):
                continue
            pane = self._make_pane(stream.name)
            self._panes[stream.name] = pane
            if isinstance(pane, QWidget):
                self._wall.addWidget(pane)
            url = self._local_url(stream.name)
            if url:
                pane.show(url)

    def refresh(self) -> None:
        """Read every pane's state. Restart only what has actually failed."""
        for name, pane in self._panes.items():
            state = pane.state
            self._status[name] = state
            if state == "failed":
                url = self._local_url(name)
                if url:
                    logger.warning("%s failed; restarting it", name)
                    pane.show(url)

    def stream_status_text(self, name: str) -> str:
        return self._status.get(name, "stopped")

    # --------------------------------------------------------------- steering

    def key_down(self, key: str, fine: bool) -> None:
        self._held.add(key)
        self._drive(*key_velocity(self._held, fine), 0.0)

    def key_up(self, key: str) -> None:
        self._held.discard(key)
        self._drive(*key_velocity(self._held, False), 0.0)

    def pointer_at(self, x: float, y: float, pressed: bool) -> None:
        if not pressed:
            return
        pan, tilt = edge_velocity(x, y)
        self._drive(pan, tilt, 0.0)

    def zoom(self, direction: int) -> None:
        self._drive(0.0, 0.0, 0.5 * direction)

    def go_home(self) -> None:
        self._last_velocity = None
        self._ptz.home()

    def _drive(self, pan: float, tilt: float, zoom: float) -> None:
        """Send a velocity, or a stop. Repeats are dropped: a held key produces
        a stream of identical events and every one of them would otherwise be a
        request across the link."""
        velocity = (pan, tilt, zoom)
        if velocity == self._last_velocity:
            return
        self._last_velocity = velocity

        if pan == 0.0 and tilt == 0.0 and zoom == 0.0:
            result = self._ptz.stop()
            self._moving.setText("idle")
        else:
            result = self._ptz.move(pan, tilt, zoom)
            self._moving.setText(f"pan {pan:+.2f}  tilt {tilt:+.2f}  zoom {zoom:+.2f}")

        if isinstance(result, dict) and result.get("ok") is False:
            self._ptz_note.setText(result.get("error", "the camera refused the command"))
        else:
            self._ptz_note.setText("")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_desktop_live.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add vmd/desktop/live.py tests/test_desktop_live.py
git commit -m "Add the Live tab: panes from the local server, steering to the camera"
```

---

## Task 8: Timeline maths, without Qt

**Files:**
- Create: `vmd/desktop/timeline.py`
- Test: `tests/test_desktop_timeline.py`

- [ ] **Step 1: Write the failing test**

```python
"""Turning indexed segments into a drawable day, and clicks back into times."""

from __future__ import annotations

from vmd.desktop.timeline import DAY_SECONDS, coverage_bars, day_bounds, seek_target, time_at
from vmd.storage.index import Segment


def segment(start: float, end: float, path: str = "a.mp4") -> Segment:
    return Segment(id=1, stream="thermal", path=path, start=start, end=end, size_bytes=10)


def test_a_day_is_midnight_to_midnight_local() -> None:
    start, end = day_bounds(2026, 8, 11)
    assert end - start == DAY_SECONDS


def test_coverage_is_a_fraction_of_the_day() -> None:
    start, _ = day_bounds(2026, 8, 11)
    bars = coverage_bars([segment(start + 3600, start + 7200)], start)
    assert len(bars) == 1
    left, width = bars[0]
    assert abs(left - 1 / 24) < 1e-6
    assert abs(width - 1 / 24) < 1e-6


def test_segments_outside_the_day_are_not_drawn() -> None:
    start, _ = day_bounds(2026, 8, 11)
    bars = coverage_bars([segment(start - 10000, start - 9000)], start)
    assert bars == []


def test_a_segment_crossing_midnight_is_clipped_to_the_day() -> None:
    start, _ = day_bounds(2026, 8, 11)
    bars = coverage_bars([segment(start + DAY_SECONDS - 60, start + DAY_SECONDS + 600)], start)
    left, width = bars[0]
    assert left + width <= 1.0 + 1e-9


def test_a_click_maps_to_a_time_in_that_day() -> None:
    start, _ = day_bounds(2026, 8, 11)
    assert time_at(0.0, start) == start
    assert time_at(0.5, start) == start + DAY_SECONDS / 2
    assert time_at(1.0, start) == start + DAY_SECONDS


def test_a_click_inside_a_segment_seeks_into_that_file() -> None:
    start, _ = day_bounds(2026, 8, 11)
    one = segment(start + 100, start + 400, "one.mp4")
    target = seek_target([one], start + 250)
    assert target is not None
    assert target.path == "one.mp4"
    assert abs(target.offset_seconds - 150) < 1e-6


def test_a_click_in_a_gap_finds_nothing() -> None:
    start, _ = day_bounds(2026, 8, 11)
    one = segment(start + 100, start + 200, "one.mp4")
    assert seek_target([one], start + 5000) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_timeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.desktop.timeline'`

- [ ] **Step 3: Write the implementation**

Create `vmd/desktop/timeline.py`:

```python
"""What the playback timeline draws, and what a click on it means.

Coverage comes from the segment index, which knows the exact start and end of
every file on disk. The bar therefore shows what was actually recorded,
including the gaps - a timeline that draws an unbroken day it cannot prove is
worse than no timeline.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from vmd.storage.index import Segment

DAY_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class SeekTarget:
    """A file and how far into it to start."""

    path: str
    offset_seconds: float


def day_bounds(year: int, month: int, day: int) -> tuple[float, float]:
    """Midnight to midnight, in local time, as epoch seconds.

    Local rather than UTC because the operator picks a date from a calendar and
    means their own day. Segment filenames are UTC, which is a different problem
    already solved in storage.
    """
    start = datetime.datetime(year, month, day).timestamp()
    return (start, start + DAY_SECONDS)


def coverage_bars(segments: list[Segment], day_start: float) -> list[tuple[float, float]]:
    """(left, width) as fractions of the day, for every segment that touches it."""
    day_end = day_start + DAY_SECONDS
    bars: list[tuple[float, float]] = []
    for segment in segments:
        start = max(segment.start, day_start)
        end = min(segment.end, day_end)
        if end <= start:
            continue
        bars.append(((start - day_start) / DAY_SECONDS, (end - start) / DAY_SECONDS))
    return bars


def time_at(fraction: float, day_start: float) -> float:
    """The epoch time a click at this fraction of the width means."""
    fraction = min(max(fraction, 0.0), 1.0)
    return day_start + fraction * DAY_SECONDS


def seek_target(segments: list[Segment], when: float) -> SeekTarget | None:
    """The file covering this moment, and how far into it, or None for a gap."""
    for segment in segments:
        if segment.start <= when < segment.end:
            return SeekTarget(path=segment.path, offset_seconds=when - segment.start)
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_desktop_timeline.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add vmd/desktop/timeline.py tests/test_desktop_timeline.py
git commit -m "Add timeline maths: real coverage, real gaps, clicks to seek points"
```

---

## Task 9: The Playback tab

**Files:**
- Create: `vmd/desktop/playback.py`
- Test: `tests/test_desktop_playback.py`

- [ ] **Step 1: Write the failing test**

```python
"""Playback, against a fake pane and a real index."""

from __future__ import annotations

from pathlib import Path

from vmd.desktop.playback import PlaybackTab
from vmd.desktop.timeline import day_bounds
from vmd.desktop.video import FakeVideoPane
from vmd.storage.index import SegmentIndex


def build(qtbot, tmp_path: Path):
    index = SegmentIndex(tmp_path / "segments.db")
    pane = FakeVideoPane()
    tab = PlaybackTab(index=index, pane=pane)
    qtbot.addWidget(tab)
    return tab, pane, index


def test_a_day_with_nothing_recorded_says_so(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        tab.show_day(2026, 8, 11, stream="thermal")
        assert tab.coverage == []
        assert "nothing" in tab.status_text.lower()
    finally:
        index.close()


def test_recorded_segments_become_coverage(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _ = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "a.mp4"), start + 3600, start + 5400, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        assert len(tab.coverage) == 1
    finally:
        index.close()


def test_clicking_inside_coverage_opens_that_file_at_that_offset(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _ = day_bounds(2026, 8, 11)
        path = tmp_path / "a.mp4"
        index.add("thermal", str(path), start + 3600, start + 5400, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        # 3600 s into a 86400 s day, plus 30 s
        tab.click_at((3600 + 30) / 86400)
        assert pane.url is not None
        assert path.name in pane.url
        assert tab.seek_offset == 30
    finally:
        index.close()


def test_clicking_a_gap_explains_rather_than_playing_something_else(qtbot, tmp_path: Path) -> None:
    tab, pane, index = build(qtbot, tmp_path)
    try:
        start, _ = day_bounds(2026, 8, 11)
        index.add("thermal", str(tmp_path / "a.mp4"), start + 3600, start + 5400, 1000)
        tab.show_day(2026, 8, 11, stream="thermal")
        tab.click_at(0.9)
        assert pane.url is None
        assert "no recording" in tab.status_text.lower()
    finally:
        index.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_playback.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.desktop.playback'`

- [ ] **Step 3: Write the implementation**

Create `vmd/desktop/playback.py`:

```python
"""Looking back through what was recorded."""

from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from vmd.desktop.timeline import coverage_bars, day_bounds, seek_target, time_at
from vmd.desktop.video import VideoPane
from vmd.storage.index import SegmentIndex


class PlaybackTab(QWidget):
    """A day of recordings, and a player pointed into it."""

    def __init__(
        self,
        index: SegmentIndex,
        pane: VideoPane,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._pane = pane
        self._day_start = 0.0
        self._segments: list = []
        self.coverage: list[tuple[float, float]] = []
        self.status_text = ""
        self.seek_offset = 0.0

        layout = QVBoxLayout(self)
        if isinstance(pane, QWidget):
            layout.addWidget(pane, 1)
        self._status = QLabel("")
        layout.addWidget(self._status)

    def show_day(self, year: int, month: int, day: int, stream: str) -> None:
        self._day_start, day_end = day_bounds(year, month, day)
        self._segments = [
            s
            for s in self._index.all(stream)
            if s.end > self._day_start and s.start < day_end
        ]
        self.coverage = coverage_bars(self._segments, self._day_start)
        if not self.coverage:
            self._set_status("Nothing was recorded on this day.")
        else:
            self._set_status(f"{len(self.coverage)} segments recorded.")

    def click_at(self, fraction: float) -> None:
        when = time_at(fraction, self._day_start)
        target = seek_target(self._segments, when)
        if target is None:
            self._set_status(
                "No recording at " + datetime.datetime.fromtimestamp(when).strftime("%H:%M:%S")
            )
            return
        self.seek_offset = target.offset_seconds
        self._pane.show(Path(target.path).as_uri())
        self._set_status(
            "Playing "
            + datetime.datetime.fromtimestamp(when).strftime("%H:%M:%S")
            + f" — {Path(target.path).name}"
        )

    def _set_status(self, text: str) -> None:
        self.status_text = text
        self._status.setText(text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_desktop_playback.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add vmd/desktop/playback.py tests/test_desktop_playback.py
git commit -m "Add the Playback tab, drawing coverage the index can prove"
```

---

## Task 10: The Logs tab

**Files:**
- Create: `vmd/desktop/logs.py`
- Test: `tests/test_desktop_logs.py`

- [ ] **Step 1: Write the failing test**

```python
"""Whatever the system says about itself, where the operator can read it."""

from __future__ import annotations

import logging

from vmd.desktop.logs import LogBuffer, LogsTab


def test_records_are_kept_in_order() -> None:
    buffer = LogBuffer(capacity=10)
    logger = logging.getLogger("vmd.test.order")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("first")
        logger.warning("second")
    finally:
        logger.removeHandler(buffer)

    lines = buffer.snapshot()
    assert [line["text"] for line in lines] == ["first", "second"]
    assert lines[1]["level"] == "WARNING"


def test_the_oldest_lines_fall_off_rather_than_growing_forever() -> None:
    buffer = LogBuffer(capacity=3)
    logger = logging.getLogger("vmd.test.capacity")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        for i in range(10):
            logger.info("line %d", i)
    finally:
        logger.removeHandler(buffer)

    lines = buffer.snapshot()
    assert len(lines) == 3
    assert lines[-1]["text"] == "line 9"


def test_a_traceback_is_kept_with_its_message() -> None:
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.exception")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("it failed")
    finally:
        logger.removeHandler(buffer)

    assert "boom" in buffer.snapshot()[0]["text"]


def test_logging_never_raises_into_the_caller() -> None:
    """A broken log call must not take the console with it."""
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.bad")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("%d", "not a number")   # deliberately wrong
    finally:
        logger.removeHandler(buffer)

    assert len(buffer.snapshot()) == 1


def test_the_tab_shows_what_the_buffer_holds(qtbot) -> None:
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.tab")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("visible line")
    finally:
        logger.removeHandler(buffer)

    tab = LogsTab(buffer)
    qtbot.addWidget(tab)
    tab.refresh()
    assert tab.row_count == 1
    assert "visible line" in tab.text_at(0)


def test_the_tab_can_show_only_what_went_wrong(qtbot) -> None:
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.filter")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("ordinary")
        logger.error("bad")
    finally:
        logger.removeHandler(buffer)

    tab = LogsTab(buffer)
    qtbot.addWidget(tab)
    tab.set_level_filter("WARNING")
    tab.refresh()
    assert tab.row_count == 1
    assert "bad" in tab.text_at(0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_logs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.desktop.logs'`

- [ ] **Step 3: Write the implementation**

Create `vmd/desktop/logs.py`:

```python
"""The last few hundred things the system said, where they can be read.

The operator has this window and nothing else. Whatever the console, the
streaming server and the recorder report has to be reachable here, because
asking someone to open a log file on a machine bolted to a desk is not a plan.
"""

from __future__ import annotations

import logging
import threading
from collections import deque

from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

LOG_LINES = 500
SEVERE = {"WARNING", "ERROR", "CRITICAL"}


class LogBuffer(logging.Handler):
    """A ring of recent log records, safe to read from the UI thread."""

    def __init__(self, capacity: int = LOG_LINES) -> None:
        super().__init__()
        self.records: deque[dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.exc_info:
                text += "\n" + logging.Formatter().formatException(record.exc_info)
        except Exception:  # noqa: BLE001 - logging must never raise into the caller
            text = "<unformattable log record>"
        with self._lock:
            self.records.append(
                {
                    "time": record.created,
                    "level": record.levelname,
                    "source": record.name,
                    "text": text,
                }
            )

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self.records)


def attach(buffer: LogBuffer) -> LogBuffer:
    """Put the buffer on the root logger. Idempotent."""
    root = logging.getLogger()
    if buffer not in root.handlers:
        buffer.setLevel(logging.INFO)
        root.addHandler(buffer)
    return buffer


class LogsTab(QWidget):
    """A table of the buffer, newest last."""

    def __init__(self, buffer: LogBuffer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buffer = buffer
        self._filter = "ALL"

        layout = QVBoxLayout(self)
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["time", "level", "message"])
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

    def set_level_filter(self, level: str) -> None:
        self._filter = level

    @property
    def row_count(self) -> int:
        return self._table.rowCount()

    def text_at(self, row: int) -> str:
        item = self._table.item(row, 2)
        return item.text() if item else ""

    def refresh(self) -> None:
        import datetime

        lines = [
            line
            for line in self._buffer.snapshot()
            if self._filter == "ALL" or line["level"] in SEVERE
        ]
        self._table.setRowCount(len(lines))
        for row, line in enumerate(lines):
            stamp = datetime.datetime.fromtimestamp(line["time"]).strftime("%H:%M:%S")
            self._table.setItem(row, 0, QTableWidgetItem(stamp))
            self._table.setItem(row, 1, QTableWidgetItem(line["level"]))
            self._table.setItem(row, 2, QTableWidgetItem(line["text"]))
        self._table.scrollToBottom()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_desktop_logs.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add vmd/desktop/logs.py tests/test_desktop_logs.py
git commit -m "Add the Logs tab, reachable from the only window the operator has"
```

---

## Task 11: The Settings tab

**Files:**
- Create: `vmd/desktop/settings_tab.py`
- Test: `tests/test_desktop_settings_tab.py`

- [ ] **Step 1: Write the failing test**

```python
"""The settings form: what it loads, what it saves, and what it refuses."""

from __future__ import annotations

from pathlib import Path

from vmd.desktop.settings_tab import SettingsTab
from vmd.settings import CameraSettings, Settings, StreamSettings, load_settings, save_settings


def build(qtbot, tmp_path: Path, settings: Settings | None = None):
    path = tmp_path / "settings.json"
    if settings is not None:
        save_settings(settings, path)
    tab = SettingsTab(settings_path=path)
    qtbot.addWidget(tab)
    tab.load()
    return tab, path


def test_a_first_run_loads_defaults_without_a_file(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    assert not path.exists()
    assert tab.camera_host == ""


def test_what_was_typed_is_what_is_saved(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.camera_host = "192.168.1.250"
    tab.camera_username = "admin"
    tab.camera_password = "p@ss/word"
    tab.set_streams([("thermal", "rtsp://192.168.1.250:554/ch2", True, "auto")])
    assert tab.save() is True

    stored = load_settings(path)
    assert stored.camera.host == "192.168.1.250"
    assert stored.camera.password == "p@ss/word"
    assert stored.camera.streams[0].name == "thermal"


def test_existing_streams_survive_a_load_and_save(qtbot, tmp_path: Path) -> None:
    """The browser form once deleted any stream it did not have a row for."""
    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(name="IR-ch2", url="rtsp://10.0.0.2/ch2", enabled=True),
                StreamSettings(name="day", url="rtsp://10.0.0.2/ch0", enabled=False),
            ],
        )
    )
    tab, path = build(qtbot, tmp_path, settings)
    assert tab.save() is True
    assert [s.name for s in load_settings(path).camera.streams] == ["IR-ch2", "day"]


def test_a_stream_ticked_to_record_with_no_address_is_refused(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.set_streams([("thermal", "", True, "auto")])
    assert tab.save() is False
    assert "address" in tab.message.lower()
    assert not path.exists(), "a refused save must not write anything"


def test_two_streams_with_one_name_are_refused(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.set_streams(
        [("thermal", "rtsp://a/1", True, "auto"), ("thermal", "rtsp://a/2", True, "auto")]
    )
    assert tab.save() is False
    assert "thermal" in tab.message


def test_a_budget_the_model_rejects_is_reported_not_swallowed(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.budget_gb = "-5"
    assert tab.save() is False
    assert "budget" in tab.message.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_settings_tab.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.desktop.settings_tab'`

- [ ] **Step 3: Write the implementation**

Create `vmd/desktop/settings_tab.py`:

```python
"""Everything the operator configures, and nothing that is not configuration.

A save either writes exactly what is on screen or writes nothing and says why.
Silently dropping a field the operator just typed - which the browser form did
with stream names - is worse than refusing.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vmd.settings import (
    CameraSettings,
    RadioSettings,
    Settings,
    StorageSettings,
    StreamSettings,
    load_settings,
    save_settings,
)

logger = logging.getLogger(__name__)

# name, url, enabled, reader
StreamRow = tuple[str, str, bool, str]


class SettingsTab(QWidget):
    def __init__(self, settings_path: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings_path = Path(settings_path)
        self.message = ""
        self._streams: list[StreamRow] = []

        layout = QVBoxLayout(self)

        camera_box = QGroupBox("Camera")
        camera_form = QFormLayout(camera_box)
        self._host = QLineEdit()
        self._username = QLineEdit()
        # Shown, never masked: this machine is offline and physically controlled,
        # and the failure this form actually suffers is a typo nobody can see.
        self._password = QLineEdit()
        camera_form.addRow("Address", self._host)
        camera_form.addRow("Username", self._username)
        camera_form.addRow("Password", self._password)
        layout.addWidget(camera_box)

        storage_box = QGroupBox("Storage")
        storage_form = QFormLayout(storage_box)
        self._root = QLineEdit()
        self._budget = QLineEdit()
        self._days = QLineEdit()
        storage_form.addRow("Folder", self._root)
        storage_form.addRow("Budget (GB)", self._budget)
        storage_form.addRow("Delete older than (days)", self._days)
        layout.addWidget(storage_box)

        radio_box = QGroupBox("Radio")
        radio_form = QFormLayout(radio_box)
        self._radio_host = QLineEdit()
        self._radio_user = QLineEdit()
        self._radio_password = QLineEdit()
        radio_form.addRow("Address", self._radio_host)
        radio_form.addRow("Username", self._radio_user)
        radio_form.addRow("Password", self._radio_password)
        layout.addWidget(radio_box)

        self._message = QLabel("")
        self._message.setWordWrap(True)
        layout.addWidget(self._message)

        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save)
        layout.addWidget(save_button)
        layout.addStretch(1)

    # ------------------------------------------------------------- properties

    @property
    def camera_host(self) -> str:
        return self._host.text()

    @camera_host.setter
    def camera_host(self, value: str) -> None:
        self._host.setText(value)

    @property
    def camera_username(self) -> str:
        return self._username.text()

    @camera_username.setter
    def camera_username(self, value: str) -> None:
        self._username.setText(value)

    @property
    def camera_password(self) -> str:
        return self._password.text()

    @camera_password.setter
    def camera_password(self, value: str) -> None:
        self._password.setText(value)

    @property
    def budget_gb(self) -> str:
        return self._budget.text()

    @budget_gb.setter
    def budget_gb(self, value: str) -> None:
        self._budget.setText(str(value))

    def set_streams(self, rows: list[StreamRow]) -> None:
        self._streams = list(rows)

    def streams(self) -> list[StreamRow]:
        return list(self._streams)

    # ------------------------------------------------------------------ load

    def load(self) -> None:
        settings = load_settings(self.settings_path)
        self.camera_host = settings.camera.host
        self.camera_username = settings.camera.username
        self.camera_password = settings.camera.password
        self._root.setText(str(settings.storage.root))
        self._budget.setText(str(settings.storage.budget_gb))
        self._days.setText("" if settings.storage.retention_days is None else str(settings.storage.retention_days))
        self._radio_host.setText(settings.radio.host)
        self._radio_user.setText(settings.radio.username)
        self._radio_password.setText(settings.radio.password)
        self._streams = [
            (s.name, s.url, s.enabled, getattr(s, "reader", "auto"))
            for s in settings.camera.streams
        ]
        self._set_message("")

    # ------------------------------------------------------------------ save

    def save(self) -> bool:
        problem = self._problem()
        if problem:
            self._set_message(problem)
            return False

        try:
            settings = Settings(
                camera=CameraSettings(
                    host=self.camera_host.strip(),
                    username=self.camera_username.strip(),
                    password=self.camera_password,
                    streams=[
                        StreamSettings(name=name, url=url, enabled=enabled, reader=reader)
                        for name, url, enabled, reader in self._streams
                        if name and url
                    ],
                ),
                radio=RadioSettings(
                    host=self._radio_host.text().strip(),
                    username=self._radio_user.text().strip(),
                    password=self._radio_password.text(),
                    enabled=bool(self._radio_host.text().strip()),
                ),
                storage=StorageSettings(
                    root=Path(self._root.text().strip() or "recordings"),
                    budget_gb=float(self._budget.text() or 100),
                    retention_days=int(self._days.text()) if self._days.text().strip() else None,
                ),
            )
        except (ValidationError, ValueError) as exc:
            self._set_message(_first_problem(exc))
            return False

        try:
            save_settings(settings, self.settings_path)
        except OSError as exc:
            self._set_message(f"Could not write the settings file: {exc}")
            return False

        self._set_message("Saved.")
        return True

    def _problem(self) -> str:
        seen: set[str] = set()
        for name, url, enabled, _reader in self._streams:
            if enabled and not url.strip():
                return f'"{name or "a stream"}" is ticked to record but has no address.'
            if url.strip() and not name.strip():
                return "A stream has an address but no name."
            if name in seen:
                return f'Two streams are both called "{name}".'
            if name:
                seen.add(name)
        return ""

    def _set_message(self, text: str) -> None:
        self.message = text
        self._message.setText(text)


def _first_problem(exc: Exception) -> str:
    """One readable sentence out of a validation error."""
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "settings"
        return f"{where}: {first['msg']}"
    return str(exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_desktop_settings_tab.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add vmd/desktop/settings_tab.py tests/test_desktop_settings_tab.py
git commit -m "Add the Settings tab, which saves what was typed or says why not"
```

---

## Task 12: The camera tools in the Settings tab

**Files:**
- Modify: `vmd/desktop/settings_tab.py`
- Test: `tests/test_desktop_camera_tools.py`

- [ ] **Step 1: Write the failing test**

```python
"""Find the right path, and fit the camera to the link, without a browser."""

from __future__ import annotations

from pathlib import Path

from vmd.desktop.settings_tab import CameraTools
from vmd.settings import CameraSettings, Settings, StreamSettings


class FakePtz:
    def __init__(self) -> None:
        self.fitted_to: int | None = None

    def fit_encoders_to_link(self, ceiling_kbps: int) -> dict:
        self.fitted_to = ceiling_kbps
        return {"ok": True, "changed": ["visible: 16000 -> 2800 kb/s"]}


def settings_with_camera() -> Settings:
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name="thermal", url="rtsp://10.0.0.2/ch2", enabled=True)],
        )
    )


def test_finding_paths_reports_progress_and_results(qtbot) -> None:
    progress: list[str] = []
    tools = CameraTools(
        ptz=FakePtz(),
        find_paths=lambda settings, on_progress: (
            on_progress("trying /ch1 (1/24)"),
            ["  [ok] /ch1   codec_name=h264"],
        )[1],
        diagnose=lambda settings: ["nothing to say"],
    )
    tools.on_progress = progress.append
    lines = tools.find_paths(settings_with_camera())
    assert progress == ["trying /ch1 (1/24)"]
    assert any("/ch1" in line for line in lines)


def test_fitting_to_the_link_uses_the_configured_ceiling(qtbot) -> None:
    ptz = FakePtz()
    tools = CameraTools(ptz=ptz, find_paths=lambda s, on_progress: [], diagnose=lambda s: [])
    settings = settings_with_camera()
    settings.bitrate.ceiling_kbps = 4200
    lines = tools.fit_to_link(settings)
    assert ptz.fitted_to == 4200
    assert any("2800" in line for line in lines)


def test_a_report_can_be_written_to_a_file_to_send_on(qtbot, tmp_path) -> None:
    """The spec's replacement for the browser's Copy a report: this window is
    the only thing on the machine, so the report has to become a file."""
    tools = CameraTools(
        ptz=FakePtz(),
        find_paths=lambda s, on_progress: [],
        diagnose=lambda s: ["camera address : 10.0.0.2", "  [ok] answers"],
    )
    target = tmp_path / "vmd-report.txt"
    written = tools.write_report(settings_with_camera(), target, extra=["recording: yes"])
    assert written == target
    text = target.read_text(encoding="utf-8")
    assert "10.0.0.2" in text
    assert "recording: yes" in text


def test_a_report_never_contains_the_password(qtbot, tmp_path) -> None:
    settings = settings_with_camera()
    settings.camera.password = "s3cret-in-the-field"
    tools = CameraTools(
        ptz=FakePtz(),
        find_paths=lambda s, on_progress: [],
        diagnose=lambda s: ["password       : set"],
    )
    target = tmp_path / "vmd-report.txt"
    tools.write_report(settings, target, extra=[])
    assert "s3cret-in-the-field" not in target.read_text(encoding="utf-8")


def test_a_camera_that_refuses_is_reported_in_its_own_words(qtbot) -> None:
    class Refusing:
        def fit_encoders_to_link(self, ceiling_kbps: int) -> dict:
            return {"ok": False, "error": "Sender not Authorized"}

    tools = CameraTools(ptz=Refusing(), find_paths=lambda s, on_progress: [], diagnose=lambda s: [])
    lines = tools.fit_to_link(settings_with_camera())
    assert any("Authorized" in line for line in lines)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_camera_tools.py -v`
Expected: FAIL with `ImportError: cannot import name 'CameraTools'`

- [ ] **Step 3: Write the implementation**

Append to `vmd/desktop/settings_tab.py`:

```python
class CameraTools:
    """The questions the field kept needing answered.

    "Which path actually gives video" and "does this stream fit the link" are
    both answered by asking the camera, and both were only reachable through the
    browser. They are plain calls into existing code; this is the seam that lets
    them be tested without a camera.
    """

    def __init__(self, ptz, find_paths, diagnose) -> None:
        self._ptz = ptz
        self._find_paths = find_paths
        self._diagnose = diagnose
        self.on_progress = lambda step: None

    def find_paths(self, settings: Settings) -> list[str]:
        return self._find_paths(settings, on_progress=self.on_progress)

    def diagnose(self, settings: Settings) -> list[str]:
        return self._diagnose(settings)

    def fit_to_link(self, settings: Settings) -> list[str]:
        result = self._ptz.fit_encoders_to_link(settings.bitrate.ceiling_kbps)
        if not result.get("ok"):
            return [result.get("error", "the camera refused")]
        return list(result.get("changed", []))

    def write_report(self, settings: Settings, path, extra: list[str]) -> Path:
        """Everything about this installation, in one file that can be sent on.

        Diagnosing a machine at the other end of a conversation fails on missing
        context more than on hard problems. The password is never included: it
        is the one thing in here that must not travel.
        """
        path = Path(path)
        lines = ["VMD report", ""]
        lines.extend(extra)
        lines.append("")
        lines.extend(self.diagnose(settings))
        text = "\n".join(lines)
        if settings.camera.password:
            text = text.replace(settings.camera.password, "****")
        path.write_text(text, encoding="utf-8")
        return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_desktop_camera_tools.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add vmd/desktop/settings_tab.py tests/test_desktop_camera_tools.py
git commit -m "Bring the camera tools across from the browser"
```

---

## Task 13: The window and the entry point

**Files:**
- Create: `vmd/desktop/window.py`, `vmd/desktop/app.py`
- Move: `vmd/webui/updater.py` → `vmd/updater.py`
- Modify: `tests/test_updater.py` (import path only)
- Test: `tests/test_desktop_window.py`

- [ ] **Step 1: Move the updater out of the web package**

```bash
git mv vmd/webui/updater.py vmd/updater.py
```

Then change the import in `tests/test_updater.py`:

```python
from vmd.updater import Updater
```

Run: `uv run pytest tests/test_updater.py -v`
Expected: PASS, 7 tests

- [ ] **Step 2: Write the failing test**

```python
"""The window: four tabs, a status line, and children that outlive a close."""

from __future__ import annotations

from pathlib import Path

from vmd.desktop.window import ConsoleWindow
from vmd.desktop.video import FakeVideoPane
from vmd.settings import Settings, save_settings


class FakeServices:
    def __init__(self) -> None:
        self.ticks = 0
        self.stopped = False

    def start(self) -> None: ...

    def tick(self) -> list[str]:
        self.ticks += 1
        return []

    def stop(self) -> None:
        self.stopped = True

    def local_url(self, name: str) -> str | None:
        return f"rtsp://127.0.0.1:8554/{name}"

    def state(self) -> dict:
        return {"recording": True, "streaming": "streaming", "restarts": {}}


class FakePtz:
    def status(self) -> dict:
        return {"available": False, "reason": "no camera address set"}

    def move(self, pan, tilt, zoom) -> dict:
        return {"ok": True}

    def stop(self) -> dict:
        return {"ok": True}

    def home(self) -> dict:
        return {"ok": True}


class FakeRadio:
    def status(self) -> dict:
        return {"connected": False, "reason": "the radio is not set up"}


def build(qtbot, tmp_path: Path):
    path = tmp_path / "settings.json"
    save_settings(Settings(), path)
    services = FakeServices()
    window = ConsoleWindow(
        settings_path=path,
        services=services,
        ptz=FakePtz(),
        radio=FakeRadio(),
        index_path=tmp_path / "segments.db",
        make_pane=lambda name: FakeVideoPane(),
    )
    qtbot.addWidget(window)
    return window, services


def test_the_window_has_the_four_tabs(qtbot, tmp_path: Path) -> None:
    window, _ = build(qtbot, tmp_path)
    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert titles == ["Live", "Playback", "Settings", "Logs"]


def test_the_heartbeat_restarts_what_died(qtbot, tmp_path: Path) -> None:
    window, services = build(qtbot, tmp_path)
    window.heartbeat()
    assert services.ticks == 1


def test_the_status_line_says_what_is_recording_and_streaming(qtbot, tmp_path: Path) -> None:
    window, _ = build(qtbot, tmp_path)
    window.heartbeat()
    text = window.status_text()
    assert "recording" in text.lower()


def test_closing_the_window_does_not_stop_the_recorder(qtbot, tmp_path: Path) -> None:
    """The first requirement this system was given."""
    window, services = build(qtbot, tmp_path)
    window.close()
    assert services.stopped is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_window.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.desktop.window'`

- [ ] **Step 4: Write the implementation**

Create `vmd/desktop/window.py`:

```python
"""The window: four tabs, one heartbeat, and a status line that tells the truth."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow, QTabWidget, QWidget

from vmd.desktop.live import LiveTab
from vmd.desktop.logs import LogBuffer, LogsTab, attach
from vmd.desktop.playback import PlaybackTab
from vmd.desktop.settings_tab import SettingsTab
from vmd.desktop.video import VideoPane
from vmd.settings import load_settings
from vmd.storage.index import SegmentIndex

logger = logging.getLogger(__name__)

HEARTBEAT_MS = 2000


class ConsoleWindow(QMainWindow):
    def __init__(
        self,
        settings_path: str | Path,
        services,
        ptz,
        radio,
        index_path: str | Path,
        make_pane: Callable[[str], VideoPane],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("VMD")
        self.resize(1440, 900)

        self._settings_path = Path(settings_path)
        self._services = services
        self._ptz = ptz
        self._radio = radio
        self._index = SegmentIndex(index_path)
        self._buffer = attach(LogBuffer())

        settings = load_settings(self._settings_path)

        self.live = LiveTab(ptz=ptz, make_pane=make_pane, local_url=services.local_url)
        self.playback = PlaybackTab(index=self._index, pane=make_pane("playback"))
        self.settings_tab = SettingsTab(settings_path=self._settings_path)
        self.logs = LogsTab(self._buffer)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.live, "Live")
        self.tabs.addTab(self.playback, "Playback")
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.addTab(self.logs, "Logs")
        self.setCentralWidget(self.tabs)

        self.settings_tab.load()
        self.live.apply(settings)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.heartbeat)
        self._timer.start(HEARTBEAT_MS)

    def heartbeat(self) -> None:
        """Restart whatever died, read every pane, refresh what is on screen."""
        try:
            self._services.tick()
        except Exception:  # noqa: BLE001 - a bad tick must not stop the console
            logger.exception("supervising the child processes failed")

        self.live.refresh()
        if self.tabs.currentWidget() is self.logs:
            self.logs.refresh()
        self.statusBar().showMessage(self.status_text())

    def status_text(self) -> str:
        state = self._services.state()
        recording = "recording" if state.get("recording") else "NOT recording"
        link = self._radio.status()
        signal = link.get("signal_dbm")
        link_text = f"link {signal} dBm" if signal is not None else "link —"
        return f"{recording} · streaming: {state.get('streaming')} · {link_text}"

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Close the window. Deliberately does not stop the children: recording
        outlives the interface, which is the point of running it separately."""
        self._index.close()
        super().closeEvent(event)
```

Create `vmd/desktop/app.py`:

```python
"""Start the console: `python -m vmd.desktop`, or double-click VMD.exe."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from vmd.desktop.services import ConsoleServices, RecorderProcess
from vmd.desktop.style import stylesheet
from vmd.desktop.video import VlcVideoPane
from vmd.desktop.window import ConsoleWindow
from vmd.ptz.service import PtzService
from vmd.radio.service import RadioService
from vmd.settings import SettingsError, load_settings
from vmd.streaming.go2rtc import Go2rtcService, find_binary

logger = logging.getLogger("vmd.desktop")


def default_settings_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "settings.json"
    return Path("settings.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vmd", description="VMD console")
    parser.add_argument("--settings", default=str(default_settings_path()))
    parser.add_argument("--no-services", action="store_true",
                        help="do not start go2rtc or the recorder")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    settings_path = Path(args.settings)

    try:
        settings = load_settings(settings_path)
    except SettingsError as exc:
        print(f"\n  The settings file cannot be read: {exc}\n")
        return 1

    streaming = None
    if not args.no_services:
        streaming = Go2rtcService(
            settings,
            config_path=settings_path.parent / "go2rtc.json",
            binary=find_binary(),
        )

    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=streaming,
        recorder=RecorderProcess(settings_path),
    )
    if not args.no_services:
        services.start()

    app = QApplication(sys.argv)
    app.setStyleSheet(stylesheet())

    window = ConsoleWindow(
        settings_path=settings_path,
        services=services,
        ptz=PtzService(settings),
        radio=RadioService(settings),
        index_path=Path(settings.storage.root) / "segments.db",
        make_pane=lambda name: VlcVideoPane(),
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
```

Create `vmd/desktop/__main__.py`:

```python
import sys

from vmd.desktop.app import main

sys.exit(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_desktop_window.py -v`
Expected: PASS, 4 tests

- [ ] **Step 6: Commit**

```bash
git add vmd/desktop/window.py vmd/desktop/app.py vmd/desktop/__main__.py vmd/updater.py tests/test_desktop_window.py tests/test_updater.py
git commit -m "Assemble the window and its entry point"
```

---

## Task 14: Point the launcher at the desktop app

**Files:**
- Modify: `vmd/launcher.py`
- Modify: `VMD.bat`
- Test: `tests/test_launcher.py`

- [ ] **Step 1: Write the failing test**

```python
"""The launcher starts the desktop console, not the web server."""

from __future__ import annotations

from pathlib import Path

from vmd import launcher


def test_it_runs_the_desktop_module(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "vmd" / "desktop").mkdir(parents=True)
    commands: list[list[str]] = []

    class Result:
        returncode = 0

    monkeypatch.setattr(launcher, "project_root", lambda: tmp_path)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "uv")
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, cwd, check: (commands.append(command), Result())[1],
    )

    assert launcher.main([]) == 0
    assert commands[0][-2:] == ["-m", "vmd.desktop"]


def test_a_folder_without_the_app_is_reported(monkeypatch, tmp_path: Path) -> None:
    held: list[str] = []
    monkeypatch.setattr(launcher, "project_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "hold", lambda message: (held.append(message), 1)[1])
    assert launcher.main([]) == 1
    assert "VMD folder" in held[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_launcher.py -v`
Expected: FAIL — the launcher still runs `vmd.webui`

- [ ] **Step 3: Change the launcher**

In `vmd/launcher.py`, change the directory check and the command:

```python
    if not (root / "vmd" / "desktop").is_dir():
        return hold(
            f"\n  This does not look like the VMD folder:\n    {root}\n\n"
            "  Keep VMD.exe in the folder it was installed into."
        )
```

```python
    command = [uv, "run", "python", "-m", "vmd.desktop"]
```

In `VMD.bat`, change the run line:

```bat
uv run python -m vmd.desktop %*
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_launcher.py -v`
Expected: PASS, 2 tests

- [ ] **Step 5: Rebuild the executable and check it starts**

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_exe.ps1
```

Expected: `Built VMD.exe (about 7 MB)`

- [ ] **Step 6: Commit**

```bash
git add vmd/launcher.py VMD.bat tests/test_launcher.py
git commit -m "Point the launcher at the desktop console"
```

---

## Task 15: Delete the browser console

Do this last. Until it is gone, the old console is one `git checkout` away.

**Files:**
- Delete: `vmd/webui/` (server, page, `__main__`, `__init__`)
- Delete: `tests/test_webui.py`, `tests/test_console_page.py`
- Modify: `README.md`, `INSTALL.md`

- [ ] **Step 1: Check nothing still imports it**

Run: `git grep -n "vmd\.webui" -- vmd tests scripts`

Expected: **no output at all**. Anything printed is a live reference that must
be fixed before deleting the package — most likely a leftover import in
`vmd/desktop/` or a script.

- [ ] **Step 2: Delete**

```bash
git rm -r vmd/webui
git rm tests/test_webui.py tests/test_console_page.py
```

- [ ] **Step 3: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. The count drops by the 12 tests that covered the web server and
the page, and every remaining test passes.

- [ ] **Step 4: Update the documents**

In `README.md`, replace the "The console" bullet under Status with:

```markdown
- **The console** — a desktop application: live video rendered by VLC, camera
  steering, playback of what was recorded, settings and logs. It starts the
  streaming server and the recorder as child processes and restarts them if they
  stop; closing the window does not stop recording.
```

In `README.md`, replace the Running section's first paragraph with:

```markdown
**Double-click `VMD.exe`.** It opens the console window. `VMD.bat` does the same
thing without the executable.
```

In `INSTALL.md`, replace the line under "Starting it again, any time after that"
that mentions `http://127.0.0.1:8723/` with:

```markdown
**Double-click `VMD.exe`** in the `C:\VMD` folder. That is all. The console
window opens. There is no web page and no address to type.
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Delete the browser console"
```

---

## Task 16: Run it against the real camera

**Files:**
- Modify: `docs/superpowers/specs/2026-08-11-desktop-console-design.md`

- [ ] **Step 1: Start it**

```bash
uv run python -m vmd.desktop
```

- [ ] **Step 2: Work through this list, writing down what happens**

1. Settings: enter the camera address, username, password, one stream. Save.
   Expect `Saved.`, and `settings.json` to contain exactly what was typed.
2. Live: the pane shows the picture. Note how long the first frame takes.
3. Steer with the arrow keys, including two at once. The camera moves
   diagonally; releasing stops it.
4. Steer for a full minute, then leave it still for five. **The picture must not
   drop.** This is the failure the whole rewrite exists to remove.
5. Enable the second stream. Both play.
6. Playback: pick today, click inside coverage. The recording opens at that time.
7. Logs: the recorder and go2rtc both appear.
8. Close the window. Check the recorder is still running:
   `Get-Process python | Where-Object { $_.CommandLine -match 'record_main' }`
9. Reopen. The picture returns and recording was never interrupted.

- [ ] **Step 3: Record the result in the spec**

Add a section at the end of
`docs/superpowers/specs/2026-08-11-desktop-console-design.md`:

```markdown
## Field result

Tested against the camera on <date>. What worked, what did not, and what was
measured — first frame time, whether steering dropped the picture, whether
recording survived the window closing.
```

Fill it in with what actually happened, including anything that failed.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-08-11-desktop-console-design.md
git commit -m "Record what the desktop console did against the real camera"
```

---

## Notes for whoever implements this

**Do not add recovery timers to the video pane.** The spec says the pane watches
and does not intervene, and Task 4's implementation has exactly one restart
trigger: VLC reporting `Error`. Every disconnection reported from the field came
from code that fired earlier than that. If the picture misbehaves, the answer is
a measurement, not a timer.

**`qtbot` comes from pytest-qt**, already a dev dependency. Tests using it need
no display on Windows.

**Run the whole suite before every commit**, not just the file you touched:
`uv run pytest -q`. Several of these tasks touch shared modules.

**The integration tests** (`-m integration`) spawn go2rtc and ffmpeg and take
seconds rather than milliseconds. Run them before the final commit of each task
that has one; `uv run pytest -m "not integration" -q` is the fast loop.
