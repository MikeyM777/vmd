# VMD Plan A — Capture and Motion Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a running desktop application that reads up to four video streams (RTSP or local file), detects motion with background subtraction, filters nuisance motion, and shows everything in a 2×2 live grid with a debug overlay — no AI detection yet.

**Architecture:** Each camera gets a worker that pulls frames from a `FrameSource`, throttles them to an analysis rate, runs a `MotionGate` (MOG2 background subtraction on a quarter-scale grayscale image), then a `BlobFilter` implementing gates 1–3 of the spec's suppression chain (geometry, ignore mask, global motion). Results are published to a single-slot `LatestFrameBuffer` that the Qt UI polls on a timer, so a slow UI never blocks capture and a slow pipeline never queues stale frames.

**Tech Stack:** Python 3.11, uv, OpenCV (`opencv-python`), NumPy, Pydantic v2, PyYAML, PySide6, pytest, pytest-qt.

**Spec:** `docs/superpowers/specs/2026-08-06-vmd-design.md` (this plan covers phases 1–2 only).

---

## File Structure

Created by this plan:

| File | Responsibility |
|---|---|
| `pyproject.toml` | Dependencies, pytest config, package metadata |
| `vmd/__init__.py` | Package marker, version |
| `vmd/config.py` | Pydantic config models + `load_config()` + `ConfigError` |
| `vmd/frames.py` | `Frame` dataclass, `LatestFrameBuffer` (drop-old, thread-safe) |
| `vmd/sources/__init__.py` | `FrameSource` protocol, `SourceHealth` enum |
| `vmd/sources/file.py` | `FileReader` — video file playback |
| `vmd/sources/rtsp.py` | `RtspReader` — RTSP with reconnect + backoff |
| `vmd/motion/__init__.py` | Package marker |
| `vmd/motion/gate.py` | `Blob`, `MotionResult`, `MotionGate` (MOG2) |
| `vmd/motion/filters.py` | `Rejection`, `BlobFilter` — gates 1–3 |
| `vmd/ui/__init__.py` | Package marker |
| `vmd/ui/overlay.py` | `draw_overlay()` — pure NumPy drawing, no Qt |
| `vmd/ui/main_window.py` | `CameraTile`, `MainWindow` |
| `vmd/worker.py` | `CameraWorker`, `WorkerStats` |
| `vmd/app.py` | CLI entry, wiring, `run_headless()` |
| `tests/conftest.py` | Synthetic clip fixture, fake capture helpers |
| `tests/test_*.py` | One test module per source module |
| `config.example.yaml` | Documented example config |

Deferred to Plan B on purpose (YAGNI): storage/alarm/profile config sections, SQLite, clips, detector, tracker.

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `vmd/__init__.py`
- Create: `tests/__init__.py`
- Test: `tests/test_package.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_package.py`:

```python
import vmd


def test_package_exposes_version():
    assert isinstance(vmd.__version__, str)
    assert vmd.__version__ != ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_package.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd'`

- [ ] **Step 3: Write minimal implementation**

Create `pyproject.toml`:

```toml
[project]
name = "vmd"
version = "0.1.0"
description = "Video motion detection with AI verification"
requires-python = ">=3.11"
dependencies = [
    "numpy>=1.26",
    "opencv-python>=4.9",
    "pydantic>=2.6",
    "PyYAML>=6.0",
    "PySide6>=6.6",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-qt>=4.4",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["vmd"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

Create `vmd/__init__.py`:

```python
"""VMD - video motion detection with AI verification."""

__version__ = "0.1.0"
```

Create an empty `tests/__init__.py`.

- [ ] **Step 4: Install and run the test**

Run: `uv sync`
Then: `uv run pytest tests/test_package.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock vmd/__init__.py tests/__init__.py tests/test_package.py
git commit -m "chore: scaffold vmd package with uv and pytest"
```

---

### Task 2: Configuration

**Files:**
- Create: `vmd/config.py`
- Create: `config.example.yaml`
- Test: `tests/test_config.py`

`ignore_mask` is a list of polygons; each polygon is a list of `(x, y)` points in **full-resolution** image coordinates.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
import pytest

from vmd.config import AppConfig, ConfigError, load_config


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_minimal_config(tmp_path):
    path = write(tmp_path, """
cameras:
  - name: front
    url: rtsp://example/1
""")
    cfg = load_config(path)
    assert isinstance(cfg, AppConfig)
    assert cfg.cameras[0].name == "front"
    assert cfg.cameras[0].enabled is True
    assert cfg.cameras[0].analysis_fps == 6.0
    assert cfg.motion.scale == 0.25


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_duplicate_camera_names_rejected(tmp_path):
    path = write(tmp_path, """
cameras:
  - name: front
    url: rtsp://example/1
  - name: front
    url: rtsp://example/2
""")
    with pytest.raises(ConfigError, match="unique"):
        load_config(path)


def test_more_than_four_cameras_rejected(tmp_path):
    cams = "\n".join(
        f"  - name: cam{i}\n    url: rtsp://example/{i}" for i in range(5)
    )
    path = write(tmp_path, "cameras:\n" + cams)
    with pytest.raises(ConfigError, match="at most 4"):
        load_config(path)


def test_zero_analysis_fps_rejected(tmp_path):
    path = write(tmp_path, """
cameras:
  - name: front
    url: rtsp://example/1
    analysis_fps: 0
""")
    with pytest.raises(ConfigError, match="analysis_fps"):
        load_config(path)


def test_ignore_mask_parsed_as_polygons(tmp_path):
    path = write(tmp_path, """
cameras:
  - name: front
    url: rtsp://example/1
    ignore_mask:
      - [[0, 0], [10, 0], [10, 10]]
""")
    cfg = load_config(path)
    assert cfg.cameras[0].ignore_mask == [[(0, 0), (10, 0), (10, 10)]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.config'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/config.py`:

```python
"""Configuration models and loading."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class ConfigError(Exception):
    """Raised when a config file is missing, malformed or invalid."""


class MotionConfig(BaseModel):
    """Tuning for the motion gate and blob filters."""

    scale: float = 0.25
    history: int = 500
    var_threshold: float = 16.0
    detect_shadows: bool = True
    min_blob_area: int = 12
    max_blob_area_frac: float = 0.25
    min_aspect: float = 0.15
    max_aspect: float = 6.0
    global_motion_frac: float = 0.4
    dilate_iterations: int = 2


class CameraConfig(BaseModel):
    name: str
    url: str
    enabled: bool = True
    analysis_fps: float = 6.0
    ignore_mask: list[list[tuple[int, int]]] = Field(default_factory=list)

    @field_validator("analysis_fps")
    @classmethod
    def _fps_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("analysis_fps must be greater than 0")
        return value


class AppConfig(BaseModel):
    cameras: list[CameraConfig]
    motion: MotionConfig = Field(default_factory=MotionConfig)

    @model_validator(mode="after")
    def _check_cameras(self) -> "AppConfig":
        if not self.cameras:
            raise ValueError("at least one camera is required")
        if len(self.cameras) > 4:
            raise ValueError("at most 4 cameras are supported")
        names = [camera.name for camera in self.cameras]
        if len(set(names)) != len(names):
            raise ValueError("camera names must be unique")
        return self


def load_config(path: str | Path) -> AppConfig:
    """Load and validate a YAML config file."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid config in {path}:\n{exc}") from exc
```

Create `config.example.yaml`:

```yaml
# Up to 4 cameras. `url` may be an RTSP URL or a local video file path.
cameras:
  - name: front
    url: rtsp://user:pass@192.168.1.10:554/Streaming/Channels/101
    enabled: true
    analysis_fps: 6.0        # frames per second fed to the motion gate
    ignore_mask: []          # list of polygons, each a list of [x, y] in full-res pixels

motion:
  scale: 0.25                # analyse at quarter resolution
  history: 500               # MOG2 background history, frames
  var_threshold: 16.0
  detect_shadows: true
  min_blob_area: 12          # minimum blob area in downscaled pixels
  max_blob_area_frac: 0.25   # reject blobs larger than this fraction of the frame
  min_aspect: 0.15           # width / height bounds
  max_aspect: 6.0
  global_motion_frac: 0.4    # above this moving fraction, treat as camera/lighting change
  dilate_iterations: 2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/config.py config.example.yaml tests/test_config.py
git commit -m "feat: add config models and YAML loading"
```

---

### Task 3: Frame and LatestFrameBuffer

**Files:**
- Create: `vmd/frames.py`
- Test: `tests/test_frames.py`

The buffer holds exactly one frame. Publishing a second frame before the consumer reads discards the first and increments a counter. This is the drop-frame policy from the spec.

- [ ] **Step 1: Write the failing test**

Create `tests/test_frames.py`:

```python
import numpy as np

from vmd.frames import Frame, LatestFrameBuffer


def make_frame(seq):
    return Frame(camera="front", seq=seq, ts=float(seq), image=np.zeros((4, 4, 3), np.uint8))


def test_get_returns_none_when_empty():
    buffer = LatestFrameBuffer()
    assert buffer.get() is None


def test_get_returns_last_put_frame():
    buffer = LatestFrameBuffer()
    buffer.put(make_frame(1))
    got = buffer.get()
    assert got is not None
    assert got.seq == 1


def test_older_frame_is_dropped_and_counted():
    buffer = LatestFrameBuffer()
    buffer.put(make_frame(1))
    buffer.put(make_frame(2))
    assert buffer.dropped == 1
    got = buffer.get()
    assert got.seq == 2


def test_get_consumes_the_frame():
    buffer = LatestFrameBuffer()
    buffer.put(make_frame(1))
    buffer.get()
    assert buffer.get() is None


def test_peek_does_not_consume():
    buffer = LatestFrameBuffer()
    buffer.put(make_frame(1))
    assert buffer.peek().seq == 1
    assert buffer.peek().seq == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_frames.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.frames'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/frames.py`:

```python
"""Frame container and the single-slot drop-old buffer."""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Frame:
    """One decoded video frame."""

    camera: str
    seq: int
    ts: float
    image: np.ndarray  # BGR, shape (H, W, 3), dtype uint8


class LatestFrameBuffer:
    """Thread-safe one-slot buffer. Writers never block; old frames are dropped."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: Frame | None = None
        self.dropped = 0

    def put(self, frame: Frame) -> None:
        with self._lock:
            if self._frame is not None:
                self.dropped += 1
            self._frame = frame

    def get(self) -> Frame | None:
        """Return and consume the newest frame, or None."""
        with self._lock:
            frame, self._frame = self._frame, None
            return frame

    def peek(self) -> Frame | None:
        """Return the newest frame without consuming it."""
        with self._lock:
            return self._frame
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_frames.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/frames.py tests/test_frames.py
git commit -m "feat: add Frame and drop-old LatestFrameBuffer"
```

---

### Task 4: FrameSource protocol and FileReader

**Files:**
- Create: `vmd/sources/__init__.py`
- Create: `vmd/sources/file.py`
- Create: `tests/conftest.py`
- Test: `tests/test_file_source.py`

`FileReader` reads a video file frame by frame. It exists so the whole pipeline can be tuned and tested on recorded clips, exactly as the spec requires.

- [ ] **Step 1: Write the test fixture and the failing test**

Create `tests/conftest.py`:

```python
"""Shared test fixtures."""

import os

# Must be set before PySide6 creates a QApplication, so it lives at import time.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

WIDTH, HEIGHT = 320, 240
STATIC_FRAMES = 20
MOVING_FRAMES = 20
RECT_W, RECT_H = 24, 40


def _background():
    """Dark frame with fixed low-level noise so MOG2 has something to model."""
    rng = np.random.default_rng(1234)
    noise = rng.integers(0, 12, size=(HEIGHT, WIDTH, 3), dtype=np.uint8)
    return noise


@pytest.fixture
def synthetic_clip(tmp_path):
    """Write an AVI: 20 empty frames, then 20 with a bright rectangle moving right.

    Returns the path. The rectangle's top-left x at moving frame i is 40 + i * 6,
    y is fixed at 100.
    """
    path = tmp_path / "clip.avi"
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    writer = cv2.VideoWriter(str(path), fourcc, 25.0, (WIDTH, HEIGHT))
    assert writer.isOpened(), "could not open VideoWriter with MJPG"
    for _ in range(STATIC_FRAMES):
        writer.write(_background())
    for i in range(MOVING_FRAMES):
        frame = _background()
        x = 40 + i * 6
        cv2.rectangle(frame, (x, 100), (x + RECT_W, 100 + RECT_H), (255, 255, 255), -1)
        writer.write(frame)
    writer.release()
    return path
```

Create `tests/test_file_source.py`:

```python
import pytest

from vmd.sources import SourceHealth
from vmd.sources.file import FileReader


def test_reads_all_frames_then_returns_none(synthetic_clip):
    reader = FileReader(str(synthetic_clip), name="front")
    count = 0
    while True:
        frame = reader.read()
        if frame is None:
            break
        count += 1
    assert count == 40
    reader.close()


def test_frames_carry_camera_name_and_increasing_seq(synthetic_clip):
    reader = FileReader(str(synthetic_clip), name="front")
    first = reader.read()
    second = reader.read()
    assert first.camera == "front"
    assert second.seq == first.seq + 1
    assert first.image.shape == (240, 320, 3)
    reader.close()


def test_health_is_live_then_dead(synthetic_clip):
    reader = FileReader(str(synthetic_clip), name="front")
    reader.read()
    assert reader.health is SourceHealth.LIVE
    while reader.read() is not None:
        pass
    assert reader.health is SourceHealth.DEAD
    reader.close()


def test_loop_restarts_at_beginning(synthetic_clip):
    reader = FileReader(str(synthetic_clip), name="front", loop=True)
    for _ in range(45):
        frame = reader.read()
        assert frame is not None
    assert reader.health is SourceHealth.LIVE
    reader.close()


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        FileReader(str(tmp_path / "nope.avi"), name="front")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_file_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.sources'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/sources/__init__.py`:

```python
"""Frame sources: anything that produces Frames."""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from vmd.frames import Frame


class SourceHealth(str, Enum):
    INIT = "init"
    LIVE = "live"
    RECONNECTING = "reconnecting"
    DEAD = "dead"


class FrameSource(Protocol):
    """Pull-based frame producer. `read()` must never block indefinitely."""

    name: str
    health: SourceHealth

    def read(self) -> Frame | None:
        """Return the next frame, or None if none is available right now."""

    def close(self) -> None:
        """Release underlying resources."""
```

Create `vmd/sources/file.py`:

```python
"""Video file source, used for testing and offline tuning."""

from __future__ import annotations

import time
from pathlib import Path

import cv2

from vmd.frames import Frame
from vmd.sources import SourceHealth


class FileReader:
    """Reads frames from a video file as fast as the caller asks for them."""

    def __init__(self, path: str, name: str, loop: bool = False) -> None:
        if not Path(path).exists():
            raise FileNotFoundError(f"video file not found: {path}")
        self.name = name
        self.path = path
        self.loop = loop
        self.health = SourceHealth.INIT
        self._seq = 0
        self._capture = cv2.VideoCapture(path)
        if not self._capture.isOpened():
            self.health = SourceHealth.DEAD
            raise RuntimeError(f"could not open video file: {path}")

    def read(self) -> Frame | None:
        ok, image = self._capture.read()
        if not ok and self.loop:
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, image = self._capture.read()
        if not ok:
            self.health = SourceHealth.DEAD
            return None
        self.health = SourceHealth.LIVE
        frame = Frame(camera=self.name, seq=self._seq, ts=time.monotonic(), image=image)
        self._seq += 1
        return frame

    def close(self) -> None:
        self._capture.release()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_file_source.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/sources tests/conftest.py tests/test_file_source.py
git commit -m "feat: add FrameSource protocol and FileReader"
```

---

### Task 5: RtspReader with reconnect and backoff

**Files:**
- Create: `vmd/sources/rtsp.py`
- Test: `tests/test_rtsp_source.py`

No network is used in tests. The capture object and the clock are injected, so backoff timing is asserted deterministically.

- [ ] **Step 1: Write the failing test**

Create `tests/test_rtsp_source.py`:

```python
import numpy as np

from vmd.sources import SourceHealth
from vmd.sources.rtsp import RtspReader


class FakeCapture:
    """Stands in for cv2.VideoCapture."""

    def __init__(self, opens: bool, reads: list[bool]):
        self._opens = opens
        self._reads = list(reads)
        self.released = False

    def isOpened(self):  # noqa: N802 - mirrors the OpenCV API
        return self._opens

    def read(self):
        if not self._reads:
            return False, None
        ok = self._reads.pop(0)
        image = np.zeros((8, 8, 3), np.uint8) if ok else None
        return ok, image

    def release(self):
        self.released = True


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def build(captures, clock=None):
    clock = clock or FakeClock()
    queue = list(captures)
    reader = RtspReader(
        url="rtsp://example/1",
        name="front",
        capture_factory=lambda url: queue.pop(0),
        clock=clock,
    )
    return reader, clock


def test_reads_frame_when_stream_is_healthy():
    reader, _ = build([FakeCapture(True, [True, True])])
    frame = reader.read()
    assert frame is not None
    assert frame.camera == "front"
    assert reader.health is SourceHealth.LIVE


def test_failed_open_sets_reconnecting_and_returns_none():
    reader, _ = build([FakeCapture(False, [])])
    assert reader.read() is None
    assert reader.health is SourceHealth.RECONNECTING


def test_backoff_doubles_and_is_capped():
    reader, clock = build([FakeCapture(False, []) for _ in range(6)])
    delays = []
    for _ in range(6):
        reader.read()
        delays.append(reader.last_delay)  # the delay actually applied by this failure
        clock.advance(reader.last_delay)
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]


def test_no_reconnect_attempt_before_backoff_expires():
    attempts = {"count": 0}

    def factory(url):
        attempts["count"] += 1
        return FakeCapture(False, [])

    clock = FakeClock()
    reader = RtspReader("rtsp://x", "front", capture_factory=factory, clock=clock)
    reader.read()
    assert attempts["count"] == 1
    reader.read()  # clock has not advanced, so no new attempt
    assert attempts["count"] == 1
    clock.advance(1.0)
    reader.read()
    assert attempts["count"] == 2


def test_recovers_to_live_after_a_successful_reconnect():
    clock = FakeClock()
    queue = [FakeCapture(False, []), FakeCapture(True, [True])]
    reader = RtspReader(
        "rtsp://x", "front", capture_factory=lambda url: queue.pop(0), clock=clock
    )
    assert reader.read() is None
    clock.advance(1.0)
    frame = reader.read()
    assert frame is not None
    assert reader.health is SourceHealth.LIVE
    assert reader.retry_delay == 1.0


def test_marked_dead_after_dead_after_seconds_of_failure():
    clock = FakeClock()
    reader = RtspReader(
        "rtsp://x",
        "front",
        capture_factory=lambda url: FakeCapture(False, []),
        clock=clock,
        dead_after=60.0,
    )
    reader.read()
    assert reader.health is SourceHealth.RECONNECTING
    clock.advance(61.0)
    reader.read()
    assert reader.health is SourceHealth.DEAD


def test_read_failure_on_open_stream_triggers_reconnect():
    clock = FakeClock()
    queue = [FakeCapture(True, [False]), FakeCapture(True, [True])]
    reader = RtspReader(
        "rtsp://x", "front", capture_factory=lambda url: queue.pop(0), clock=clock
    )
    assert reader.read() is None
    assert reader.health is SourceHealth.RECONNECTING
    clock.advance(1.0)
    assert reader.read() is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rtsp_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.sources.rtsp'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/sources/rtsp.py`:

```python
"""RTSP source with non-blocking reconnect and exponential backoff."""

from __future__ import annotations

import time
from typing import Callable

import cv2

from vmd.frames import Frame
from vmd.sources import SourceHealth


def _default_factory(url: str):
    return cv2.VideoCapture(url, cv2.CAP_FFMPEG)


class RtspReader:
    """Reads an RTSP stream. Never raises on network failure; reports health instead."""

    def __init__(
        self,
        url: str,
        name: str,
        capture_factory: Callable[[str], object] = _default_factory,
        clock: Callable[[], float] = time.monotonic,
        initial_delay: float = 1.0,
        max_delay: float = 30.0,
        dead_after: float = 60.0,
    ) -> None:
        self.url = url
        self.name = name
        self.health = SourceHealth.INIT
        self.retry_delay = initial_delay  # delay the NEXT failure will apply
        self.last_delay = 0.0  # delay the most recent failure applied
        self._factory = capture_factory
        self._clock = clock
        self._initial_delay = initial_delay
        self._max_delay = max_delay
        self._dead_after = dead_after
        self._capture = None
        self._seq = 0
        self._next_attempt_at = 0.0
        self._failing_since: float | None = None

    def read(self) -> Frame | None:
        if self._capture is None:
            if self._clock() < self._next_attempt_at:
                return None
            self._connect()
            if self._capture is None:
                return None
        ok, image = self._capture.read()
        if not ok:
            self._fail()
            return None
        self._succeed()
        frame = Frame(camera=self.name, seq=self._seq, ts=self._clock(), image=image)
        self._seq += 1
        return frame

    def close(self) -> None:
        self._drop_capture()

    def _connect(self) -> None:
        capture = self._factory(self.url)
        if capture.isOpened():
            self._capture = capture
            return
        capture.release()
        self._fail()

    def _succeed(self) -> None:
        self.health = SourceHealth.LIVE
        self.retry_delay = self._initial_delay
        self._failing_since = None

    def _fail(self) -> None:
        now = self._clock()
        self._drop_capture()
        if self._failing_since is None:
            self._failing_since = now
            self.health = SourceHealth.RECONNECTING
        elif now - self._failing_since >= self._dead_after:
            self.health = SourceHealth.DEAD
        else:
            self.health = SourceHealth.RECONNECTING
        self.last_delay = self.retry_delay
        self._next_attempt_at = now + self.last_delay
        self.retry_delay = min(self.retry_delay * 2, self._max_delay)

    def _drop_capture(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_rtsp_source.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/sources/rtsp.py tests/test_rtsp_source.py
git commit -m "feat: add RtspReader with reconnect backoff and health states"
```

---

### Task 6: Motion gate

**Files:**
- Create: `vmd/motion/__init__.py`
- Create: `vmd/motion/gate.py`
- Test: `tests/test_motion_gate.py`

The gate works on a downscaled grayscale image for speed, but reports blob boxes in **full-resolution** coordinates so later stages can crop at native resolution.

- [ ] **Step 1: Write the failing test**

Create `tests/test_motion_gate.py`:

```python
import cv2
import numpy as np

from vmd.config import MotionConfig
from vmd.motion.gate import MotionGate


def background(seed=7):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 12, size=(240, 320, 3), dtype=np.uint8)


def with_rect(x=120, y=100, w=24, h=40):
    frame = background()
    cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 255, 255), -1)
    return frame


def train(gate, frames=25):
    for _ in range(frames):
        gate.process(background())


def test_quiet_scene_produces_no_blobs():
    gate = MotionGate(MotionConfig())
    train(gate)
    result = gate.process(background())
    assert result.blobs == []
    assert result.moving_frac < 0.05


def test_moving_rectangle_produces_one_blob_near_its_position():
    gate = MotionGate(MotionConfig())
    train(gate)
    result = gate.process(with_rect())
    assert len(result.blobs) >= 1
    blob = max(result.blobs, key=lambda b: b.w * b.h)
    assert 100 <= blob.x <= 140
    assert 80 <= blob.y <= 120


def test_blob_coordinates_are_full_resolution():
    gate = MotionGate(MotionConfig())
    train(gate)
    result = gate.process(with_rect())
    blob = max(result.blobs, key=lambda b: b.w * b.h)
    # A 24x40 rect at quarter scale would be ~6x10 if coordinates were downscaled.
    assert blob.w > 12
    assert blob.h > 20


def test_mask_is_downscaled():
    gate = MotionGate(MotionConfig())
    result = gate.process(background())
    assert result.mask.shape == (60, 80)


def test_whole_frame_change_reports_global_motion():
    gate = MotionGate(MotionConfig())
    train(gate)
    result = gate.process(np.full((240, 320, 3), 255, np.uint8))
    assert result.moving_frac > 0.4


def test_reset_clears_background_model():
    gate = MotionGate(MotionConfig())
    train(gate)
    gate.reset()
    first = gate.process(with_rect())
    # A freshly reset model treats the first frame as background, so nothing moves.
    assert first.blobs == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_motion_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.motion'`

- [ ] **Step 3: Write minimal implementation**

Create an empty `vmd/motion/__init__.py`.

Create `vmd/motion/gate.py`:

```python
"""Background-subtraction motion gate."""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from vmd.config import MotionConfig


@dataclass(frozen=True)
class Blob:
    """A moving region. Coordinates are full-resolution pixels."""

    x: int
    y: int
    w: int
    h: int
    area_ds: int  # contour area measured in downscaled pixels

    @property
    def aspect(self) -> float:
        return self.w / self.h if self.h else 0.0

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


@dataclass
class MotionResult:
    blobs: list[Blob] = field(default_factory=list)
    mask: np.ndarray | None = None
    moving_frac: float = 0.0


class MotionGate:
    """Detects moving regions using MOG2 on a downscaled grayscale image."""

    def __init__(self, config: MotionConfig) -> None:
        self.config = config
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._subtractor = self._new_subtractor()

    def _new_subtractor(self):
        return cv2.createBackgroundSubtractorMOG2(
            history=self.config.history,
            varThreshold=self.config.var_threshold,
            detectShadows=self.config.detect_shadows,
        )

    def reset(self) -> None:
        """Throw away the learned background. Used after a camera move or light change."""
        self._subtractor = self._new_subtractor()

    def process(self, image: np.ndarray) -> MotionResult:
        scale = self.config.scale
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        mask = self._subtractor.apply(gray)
        mask[mask < 255] = 0  # MOG2 marks shadows as 127; drop them
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        mask = cv2.dilate(mask, self._kernel, iterations=self.config.dilate_iterations)

        moving_frac = float(np.count_nonzero(mask)) / mask.size
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        blobs: list[Blob] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            blobs.append(
                Blob(
                    x=int(x / scale),
                    y=int(y / scale),
                    w=int(w / scale),
                    h=int(h / scale),
                    area_ds=int(cv2.contourArea(contour)),
                )
            )
        return MotionResult(blobs=blobs, mask=mask, moving_frac=moving_frac)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_motion_gate.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/motion tests/test_motion_gate.py
git commit -m "feat: add MOG2 motion gate producing full-res blob boxes"
```

---

### Task 7: Blob filters — spec gates 1, 2 and 3

**Files:**
- Create: `vmd/motion/filters.py`
- Test: `tests/test_filters.py`

Gates are applied in spec order and every rejection records its reason, which is what makes tuning evidence-driven.

- [ ] **Step 1: Write the failing test**

Create `tests/test_filters.py`:

```python
import numpy as np

from vmd.config import MotionConfig
from vmd.motion.filters import BlobFilter
from vmd.motion.gate import Blob, MotionResult

SHAPE = (240, 320, 3)


def result(blobs, moving_frac=0.01):
    return MotionResult(blobs=blobs, mask=np.zeros((60, 80), np.uint8), moving_frac=moving_frac)


def person_like():
    # 24x40 full-res, ~60 downscaled px of area
    return Blob(x=120, y=100, w=24, h=40, area_ds=60)


def test_person_like_blob_is_kept():
    kept, rejected, suppressed = BlobFilter(MotionConfig()).apply(result([person_like()]), SHAPE)
    assert kept == [person_like()]
    assert rejected == []
    assert suppressed is False


def test_tiny_blob_rejected_by_area():
    tiny = Blob(x=10, y=10, w=8, h=8, area_ds=3)
    kept, rejected, _ = BlobFilter(MotionConfig()).apply(result([tiny]), SHAPE)
    assert kept == []
    assert rejected[0].reason.startswith("gate1")
    assert "area" in rejected[0].reason


def test_oversized_blob_rejected_by_area_fraction():
    huge = Blob(x=0, y=0, w=320, h=240, area_ds=4000)  # mask is 60x80 = 4800 px
    kept, rejected, _ = BlobFilter(MotionConfig()).apply(result([huge]), SHAPE)
    assert kept == []
    assert "area" in rejected[0].reason


def test_wide_thin_blob_rejected_by_aspect():
    streak = Blob(x=10, y=10, w=200, h=8, area_ds=100)
    kept, rejected, _ = BlobFilter(MotionConfig()).apply(result([streak]), SHAPE)
    assert kept == []
    assert "aspect" in rejected[0].reason


def test_blob_inside_ignore_polygon_rejected():
    polygon = [(100, 80), (200, 80), (200, 180), (100, 180)]
    blob_filter = BlobFilter(MotionConfig(), ignore_mask=[polygon])
    kept, rejected, _ = blob_filter.apply(result([person_like()]), SHAPE)
    assert kept == []
    assert rejected[0].reason.startswith("gate2")


def test_blob_outside_ignore_polygon_kept():
    polygon = [(0, 0), (40, 0), (40, 40), (0, 40)]
    blob_filter = BlobFilter(MotionConfig(), ignore_mask=[polygon])
    kept, rejected, _ = blob_filter.apply(result([person_like()]), SHAPE)
    assert kept == [person_like()]
    assert rejected == []


def test_global_motion_suppresses_everything():
    kept, rejected, suppressed = BlobFilter(MotionConfig()).apply(
        result([person_like()], moving_frac=0.9), SHAPE
    )
    assert kept == []
    assert suppressed is True
    assert rejected[0].reason.startswith("gate3")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_filters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.motion.filters'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/motion/filters.py`:

```python
"""Gates 1-3 of the false-alarm suppression chain."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from vmd.config import MotionConfig
from vmd.motion.gate import Blob, MotionResult

Polygon = list[tuple[int, int]]


@dataclass(frozen=True)
class Rejection:
    blob: Blob
    reason: str


class BlobFilter:
    """Applies geometry, ignore-mask and global-motion gates, recording every reason."""

    def __init__(self, config: MotionConfig, ignore_mask: list[Polygon] | None = None) -> None:
        self.config = config
        self.ignore_mask = ignore_mask or []
        self._mask_image: np.ndarray | None = None
        self._mask_shape: tuple[int, int] | None = None

    def apply(
        self, result: MotionResult, frame_shape: tuple[int, ...]
    ) -> tuple[list[Blob], list[Rejection], bool]:
        """Return (kept blobs, rejections, global_motion_suppressed)."""
        rejected: list[Rejection] = []
        mask_pixels = result.mask.size if result.mask is not None else 1

        # Gate 1: geometry
        after_geometry: list[Blob] = []
        for blob in result.blobs:
            reason = self._geometry_reason(blob, mask_pixels)
            if reason:
                rejected.append(Rejection(blob, f"gate1: {reason}"))
            else:
                after_geometry.append(blob)

        # Gate 2: ignore mask
        after_mask: list[Blob] = []
        for blob in after_geometry:
            if self._in_ignore_mask(blob, frame_shape):
                rejected.append(Rejection(blob, "gate2: inside ignore mask"))
            else:
                after_mask.append(blob)

        # Gate 3: global motion
        suppressed = result.moving_frac > self.config.global_motion_frac
        if suppressed:
            reason = f"gate3: global motion {result.moving_frac:.2f}"
            rejected.extend(Rejection(blob, reason) for blob in after_mask)
            return [], rejected, True

        return after_mask, rejected, False

    def _geometry_reason(self, blob: Blob, mask_pixels: int) -> str | None:
        if blob.area_ds < self.config.min_blob_area:
            return f"area {blob.area_ds} < min {self.config.min_blob_area}"
        max_area = self.config.max_blob_area_frac * mask_pixels
        if blob.area_ds > max_area:
            return f"area {blob.area_ds} > max {max_area:.0f}"
        if not (self.config.min_aspect <= blob.aspect <= self.config.max_aspect):
            return f"aspect {blob.aspect:.2f} out of range"
        return None

    def _in_ignore_mask(self, blob: Blob, frame_shape: tuple[int, ...]) -> bool:
        if not self.ignore_mask:
            return False
        height, width = frame_shape[0], frame_shape[1]
        if self._mask_image is None or self._mask_shape != (height, width):
            image = np.zeros((height, width), np.uint8)
            for polygon in self.ignore_mask:
                cv2.fillPoly(image, [np.array(polygon, np.int32)], 255)
            self._mask_image = image
            self._mask_shape = (height, width)
        cx, cy = blob.center
        cx = min(max(cx, 0), width - 1)
        cy = min(max(cy, 0), height - 1)
        return bool(self._mask_image[cy, cx])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_filters.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/motion/filters.py tests/test_filters.py
git commit -m "feat: add blob filters for geometry, ignore mask and global motion"
```

---

### Task 8: Overlay drawing

**Files:**
- Create: `vmd/ui/__init__.py`
- Create: `vmd/ui/overlay.py`
- Test: `tests/test_overlay.py`

Pure NumPy and OpenCV — no Qt — so it is fast to test and reusable by the headless mode.

- [ ] **Step 1: Write the failing test**

Create `tests/test_overlay.py`:

```python
import numpy as np

from vmd.motion.filters import Rejection
from vmd.motion.gate import Blob
from vmd.ui.overlay import draw_overlay

GREEN = (0, 255, 0)
RED = (0, 0, 255)


def blank():
    return np.zeros((240, 320, 3), np.uint8)


def test_kept_blob_drawn_in_green():
    image = draw_overlay(blank(), kept=[Blob(50, 50, 40, 60, 100)], rejected=[])
    assert tuple(image[50, 50]) == GREEN


def test_source_image_not_mutated():
    original = blank()
    draw_overlay(original, kept=[Blob(50, 50, 40, 60, 100)], rejected=[])
    assert original.max() == 0


def test_rejected_blobs_hidden_unless_debug():
    rejection = Rejection(Blob(10, 10, 20, 20, 5), "gate1: area 5 < min 12")
    image = draw_overlay(blank(), kept=[], rejected=[rejection], debug=False)
    assert image.max() == 0


def test_rejected_blobs_drawn_in_red_when_debug():
    rejection = Rejection(Blob(10, 10, 20, 20, 5), "gate1: area 5 < min 12")
    image = draw_overlay(blank(), kept=[], rejected=[rejection], debug=True)
    assert tuple(image[10, 10]) == RED


def test_debug_mask_is_blended_in():
    mask = np.full((60, 80), 255, np.uint8)
    image = draw_overlay(blank(), kept=[], rejected=[], debug=True, mask=mask)
    assert image.max() > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_overlay.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.ui'`

- [ ] **Step 3: Write minimal implementation**

Create an empty `vmd/ui/__init__.py`.

Create `vmd/ui/overlay.py`:

```python
"""Draws motion results onto a frame. No Qt dependency."""

from __future__ import annotations

import cv2
import numpy as np

from vmd.motion.filters import Rejection
from vmd.motion.gate import Blob

GREEN = (0, 255, 0)
RED = (0, 0, 255)
BLUE = (255, 128, 0)


def draw_overlay(
    image: np.ndarray,
    kept: list[Blob],
    rejected: list[Rejection],
    debug: bool = False,
    mask: np.ndarray | None = None,
) -> np.ndarray:
    """Return a copy of `image` with motion annotations drawn on it."""
    canvas = image.copy()

    if debug and mask is not None:
        height, width = canvas.shape[:2]
        upscaled = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
        tint = np.zeros_like(canvas)
        tint[:, :, 0] = upscaled  # blue channel
        canvas = cv2.addWeighted(canvas, 1.0, tint, 0.35, 0.0)

    for blob in kept:
        cv2.rectangle(canvas, (blob.x, blob.y), (blob.x + blob.w, blob.y + blob.h), GREEN, 2)

    if debug:
        for rejection in rejected:
            blob = rejection.blob
            cv2.rectangle(
                canvas, (blob.x, blob.y), (blob.x + blob.w, blob.y + blob.h), RED, 1
            )
            cv2.putText(
                canvas,
                rejection.reason,
                (blob.x, max(blob.y - 4, 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                RED,
                1,
                cv2.LINE_AA,
            )
    return canvas
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_overlay.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/ui tests/test_overlay.py
git commit -m "feat: add motion overlay drawing"
```

---

### Task 9: CameraWorker

**Files:**
- Create: `vmd/worker.py`
- Test: `tests/test_worker.py`

`step()` does exactly one unit of work and is synchronous, so the whole pipeline is testable without threads. `run()` just loops over `step()` until stopped.

- [ ] **Step 1: Write the failing test**

Create `tests/test_worker.py`:

```python
import cv2
import numpy as np

from vmd.config import CameraConfig, MotionConfig
from vmd.frames import Frame, LatestFrameBuffer
from vmd.sources import SourceHealth
from vmd.worker import CameraWorker


class FakeSource:
    def __init__(self, images):
        self.name = "front"
        self.health = SourceHealth.LIVE
        self._images = list(images)
        self._seq = 0
        self.closed = False

    def read(self):
        if not self._images:
            self.health = SourceHealth.DEAD
            return None
        image = self._images.pop(0)
        frame = Frame(camera=self.name, seq=self._seq, ts=float(self._seq), image=image)
        self._seq += 1
        return frame

    def close(self):
        self.closed = True


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def background(seed=3):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 12, size=(240, 320, 3), dtype=np.uint8)


def with_rect():
    frame = background()
    cv2.rectangle(frame, (120, 100), (144, 140), (255, 255, 255), -1)
    return frame


def build(images, analysis_fps=1000.0, clock=None):
    clock = clock or FakeClock()
    camera = CameraConfig(name="front", url="x", analysis_fps=analysis_fps)
    buffer = LatestFrameBuffer()
    worker = CameraWorker(
        camera=camera,
        source=FakeSource(images),
        motion=MotionConfig(),
        buffer=buffer,
        clock=clock,
    )
    return worker, buffer, clock


def test_step_returns_false_when_source_is_exhausted():
    worker, _, _ = build([])
    assert worker.step() is False


def test_step_publishes_an_annotated_frame():
    worker, buffer, _ = build([background()])
    assert worker.step() is True
    published = buffer.get()
    assert published is not None
    assert published.image.shape == (240, 320, 3)


def test_stats_count_read_and_analysed_frames():
    worker, _, _ = build([background() for _ in range(3)])
    for _ in range(3):
        worker.step()
    assert worker.stats.frames_read == 3
    assert worker.stats.frames_analysed == 3


def test_analysis_is_throttled_to_analysis_fps():
    clock = FakeClock()
    worker, _, _ = build([background() for _ in range(4)], analysis_fps=1.0, clock=clock)
    worker.step()                 # t=0, analysed
    worker.step()                 # t=0, too soon
    clock.advance(1.0)
    worker.step()                 # t=1, analysed
    worker.step()                 # t=1, too soon
    assert worker.stats.frames_read == 4
    assert worker.stats.frames_analysed == 2


def test_motion_produces_kept_blobs_after_background_is_learned():
    images = [background() for _ in range(25)] + [with_rect()]
    worker, _, _ = build(images)
    for _ in range(len(images)):
        worker.step()
    assert worker.stats.last_kept >= 1


def test_global_motion_resets_the_gate_and_suppresses():
    images = [background() for _ in range(25)] + [np.full((240, 320, 3), 255, np.uint8)]
    worker, _, _ = build(images)
    for _ in range(len(images)):
        worker.step()
    assert worker.stats.last_kept == 0
    assert worker.stats.gate_resets == 1


def test_close_closes_the_source():
    worker, _, _ = build([])
    worker.close()
    assert worker.source.closed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.worker'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/worker.py`:

```python
"""Per-camera pipeline: source -> motion gate -> filters -> published frame."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from vmd.config import CameraConfig, MotionConfig
from vmd.frames import Frame, LatestFrameBuffer
from vmd.motion.filters import BlobFilter, Rejection
from vmd.motion.gate import Blob, MotionGate
from vmd.sources import FrameSource, SourceHealth
from vmd.ui.overlay import draw_overlay


@dataclass
class WorkerStats:
    frames_read: int = 0
    frames_analysed: int = 0
    last_kept: int = 0
    last_rejected: int = 0
    gate_resets: int = 0
    suppressed: bool = False
    fps: float = 0.0
    health: SourceHealth = SourceHealth.INIT


class CameraWorker:
    """Owns one camera's pipeline. `step()` is one iteration; `run()` loops it."""

    def __init__(
        self,
        camera: CameraConfig,
        source: FrameSource,
        motion: MotionConfig,
        buffer: LatestFrameBuffer,
        clock: Callable[[], float] = time.monotonic,
        debug: bool = False,
    ) -> None:
        self.camera = camera
        self.source = source
        self.buffer = buffer
        self.debug = debug
        self.stats = WorkerStats()
        self._clock = clock
        self._gate = MotionGate(motion)
        self._filter = BlobFilter(motion, ignore_mask=camera.ignore_mask)
        self._interval = 1.0 / camera.analysis_fps
        self._next_analysis_at = 0.0
        self._last_kept: list[Blob] = []
        self._last_rejected: list[Rejection] = []
        self._last_mask = None
        self._fps_window_start = 0.0
        self._fps_window_count = 0
        self._stop = threading.Event()

    def step(self) -> bool:
        """Read one frame and publish it. Returns False when the source is finished."""
        frame = self.source.read()
        self.stats.health = self.source.health
        if frame is None:
            return self.source.health is not SourceHealth.DEAD

        self.stats.frames_read += 1
        self._update_fps()

        now = self._clock()
        if now >= self._next_analysis_at:
            self._next_analysis_at = now + self._interval
            self._analyse(frame)

        annotated = draw_overlay(
            frame.image,
            kept=self._last_kept,
            rejected=self._last_rejected,
            debug=self.debug,
            mask=self._last_mask,
        )
        self.buffer.put(
            Frame(camera=frame.camera, seq=frame.seq, ts=frame.ts, image=annotated)
        )
        return True

    def run(self) -> None:
        """Loop until stop() is called or the source dies."""
        while not self._stop.is_set():
            if not self.step():
                break
            time.sleep(0)

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.stop()
        self.source.close()

    def _analyse(self, frame: Frame) -> None:
        result = self._gate.process(frame.image)
        kept, rejected, suppressed = self._filter.apply(result, frame.image.shape)
        if suppressed:
            self._gate.reset()
            self.stats.gate_resets += 1
        self._last_kept = kept
        self._last_rejected = rejected
        self._last_mask = result.mask
        self.stats.frames_analysed += 1
        self.stats.last_kept = len(kept)
        self.stats.last_rejected = len(rejected)
        self.stats.suppressed = suppressed

    def _update_fps(self) -> None:
        now = self._clock()
        self._fps_window_count += 1
        elapsed = now - self._fps_window_start
        if elapsed >= 1.0:
            self.stats.fps = self._fps_window_count / elapsed
            self._fps_window_start = now
            self._fps_window_count = 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_worker.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/worker.py tests/test_worker.py
git commit -m "feat: add CameraWorker wiring source, gate, filters and overlay"
```

---

### Task 10: Qt live grid

**Files:**
- Create: `vmd/ui/main_window.py`
- Test: `tests/test_main_window.py`

Tests run with the offscreen Qt platform, so no display is needed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_main_window.py`:

```python
import numpy as np

from vmd.config import AppConfig, CameraConfig
from vmd.frames import Frame, LatestFrameBuffer
from vmd.sources import SourceHealth
from vmd.ui.main_window import CameraTile, MainWindow
from vmd.worker import WorkerStats

# QT_QPA_PLATFORM=offscreen is set at import time in tests/conftest.py.


def frame(camera="front"):
    return Frame(camera=camera, seq=0, ts=0.0, image=np.zeros((240, 320, 3), np.uint8))


def test_tile_shows_placeholder_before_any_frame(qtbot):
    tile = CameraTile("front")
    qtbot.addWidget(tile)
    assert tile.video.pixmap().isNull()


def test_tile_renders_a_frame(qtbot):
    tile = CameraTile("front")
    qtbot.addWidget(tile)
    tile.update_view(frame(), WorkerStats(health=SourceHealth.LIVE, fps=12.5))
    assert not tile.video.pixmap().isNull()


def test_tile_status_text_shows_health_and_fps(qtbot):
    tile = CameraTile("front")
    qtbot.addWidget(tile)
    tile.update_view(frame(), WorkerStats(health=SourceHealth.LIVE, fps=12.5))
    assert "front" in tile.status.text()
    assert "live" in tile.status.text()
    assert "12.5" in tile.status.text()


def test_tile_marks_reconnecting(qtbot):
    tile = CameraTile("front")
    qtbot.addWidget(tile)
    tile.update_view(None, WorkerStats(health=SourceHealth.RECONNECTING))
    assert "reconnecting" in tile.status.text()


def test_window_builds_one_tile_per_camera(qtbot):
    config = AppConfig(
        cameras=[
            CameraConfig(name="a", url="x"),
            CameraConfig(name="b", url="y"),
            CameraConfig(name="c", url="z"),
        ]
    )
    window = MainWindow(config, buffers={}, stats={})
    qtbot.addWidget(window)
    assert set(window.tiles) == {"a", "b", "c"}


def test_refresh_pulls_from_buffers(qtbot):
    config = AppConfig(cameras=[CameraConfig(name="a", url="x")])
    buffer = LatestFrameBuffer()
    buffer.put(frame("a"))
    stats = {"a": WorkerStats(health=SourceHealth.LIVE, fps=9.0)}
    window = MainWindow(config, buffers={"a": buffer}, stats=stats)
    qtbot.addWidget(window)
    window.refresh()
    assert not window.tiles["a"].video.pixmap().isNull()


def test_toggle_debug_emits_flag(qtbot):
    config = AppConfig(cameras=[CameraConfig(name="a", url="x")])
    window = MainWindow(config, buffers={}, stats={})
    qtbot.addWidget(window)
    seen = []
    window.debug_toggled.connect(seen.append)
    window.debug_action.setChecked(True)
    assert seen == [True]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_main_window.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.ui.main_window'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/ui/main_window.py`:

```python
"""Qt live grid: one tile per camera, refreshed from the frame buffers."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from vmd.config import AppConfig
from vmd.frames import Frame, LatestFrameBuffer
from vmd.sources import SourceHealth
from vmd.worker import WorkerStats

HEALTH_COLOURS = {
    SourceHealth.INIT: "#888888",
    SourceHealth.LIVE: "#2ecc71",
    SourceHealth.RECONNECTING: "#f39c12",
    SourceHealth.DEAD: "#e74c3c",
}


def to_pixmap(image: np.ndarray) -> QPixmap:
    """Convert a BGR NumPy image to a QPixmap."""
    height, width, _ = image.shape
    rgb = np.ascontiguousarray(image[:, :, ::-1])
    qimage = QImage(rgb.data, width, height, 3 * width, QImage.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


class CameraTile(QWidget):
    """One camera: video area plus a status line."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.video = QLabel()
        self.video.setMinimumSize(320, 240)
        self.video.setAlignment(Qt.AlignCenter)
        self.video.setStyleSheet("background: #101010;")
        self.status = QLabel(f"{name} — init")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.video, 1)
        layout.addWidget(self.status)

    def update_view(self, frame: Frame | None, stats: WorkerStats) -> None:
        if frame is not None:
            pixmap = to_pixmap(frame.image)
            self.video.setPixmap(
                pixmap.scaled(
                    self.video.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
        colour = HEALTH_COLOURS.get(stats.health, "#888888")
        self.status.setStyleSheet(f"color: {colour};")
        self.status.setText(
            f"{self.name} — {stats.health.value} — {stats.fps:.1f} fps — "
            f"motion {stats.last_kept}"
        )


class MainWindow(QMainWindow):
    """2x2 grid of camera tiles with a debug toggle."""

    debug_toggled = Signal(bool)

    def __init__(
        self,
        config: AppConfig,
        buffers: dict[str, LatestFrameBuffer],
        stats: dict[str, WorkerStats],
        refresh_ms: int = 40,
    ) -> None:
        super().__init__()
        self.setWindowTitle("VMD")
        self.buffers = buffers
        self.stats = stats

        central = QWidget()
        grid = QGridLayout(central)
        self.tiles: dict[str, CameraTile] = {}
        for index, camera in enumerate(config.cameras):
            tile = CameraTile(camera.name)
            self.tiles[camera.name] = tile
            grid.addWidget(tile, index // 2, index % 2)

        self.debug_action = QCheckBox("Debug overlay")
        self.debug_action.toggled.connect(self.debug_toggled.emit)
        grid.addWidget(self.debug_action, 2, 0, 1, 2)

        self.setCentralWidget(central)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(refresh_ms)

    def refresh(self) -> None:
        for name, tile in self.tiles.items():
            buffer = self.buffers.get(name)
            frame = buffer.get() if buffer else None
            tile.update_view(frame, self.stats.get(name, WorkerStats()))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_main_window.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/ui/main_window.py tests/test_main_window.py
git commit -m "feat: add Qt live grid with per-camera status and debug toggle"
```

---

### Task 11: Application entry point and end-to-end test

**Files:**
- Create: `vmd/app.py`
- Test: `tests/test_app.py`

`run_headless()` runs the identical pipeline without Qt. It is used by the integration test now and becomes the basis of `--bench` in Plan C.

- [ ] **Step 1: Write the failing test**

Create `tests/test_app.py`:

```python
from vmd.app import build_source, parse_args, run_headless
from vmd.config import AppConfig, CameraConfig
from vmd.sources.file import FileReader
from vmd.sources.rtsp import RtspReader


def test_file_url_builds_a_file_reader(synthetic_clip):
    camera = CameraConfig(name="front", url=str(synthetic_clip))
    assert isinstance(build_source(camera), FileReader)


def test_rtsp_url_builds_an_rtsp_reader():
    camera = CameraConfig(name="front", url="rtsp://example/1")
    assert isinstance(build_source(camera), RtspReader)


def test_headless_run_processes_every_frame(synthetic_clip):
    config = AppConfig(cameras=[CameraConfig(name="front", url=str(synthetic_clip))])
    report = run_headless(config, max_frames=200)
    assert report["front"]["frames_read"] == 40
    assert report["front"]["frames_analysed"] > 0


def test_headless_run_detects_the_moving_rectangle(synthetic_clip):
    config = AppConfig(
        cameras=[CameraConfig(name="front", url=str(synthetic_clip), analysis_fps=1000.0)]
    )
    report = run_headless(config, max_frames=200)
    assert report["front"]["motion_frames"] > 0


def test_ignore_mask_covering_the_rectangle_suppresses_motion(synthetic_clip):
    covering = [(0, 60), (320, 60), (320, 200), (0, 200)]
    config = AppConfig(
        cameras=[
            CameraConfig(
                name="front",
                url=str(synthetic_clip),
                analysis_fps=1000.0,
                ignore_mask=[covering],
            )
        ]
    )
    report = run_headless(config, max_frames=200)
    assert report["front"]["motion_frames"] == 0


def test_parse_args_reads_config_and_debug_flags():
    args = parse_args(["--config", "c.yaml", "--debug", "--headless", "50"])
    assert args.config == "c.yaml"
    assert args.debug is True
    assert args.headless == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.app'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/app.py`:

```python
"""Application wiring and CLI entry point."""

from __future__ import annotations

import argparse
import sys
import threading

from vmd.config import AppConfig, CameraConfig, load_config
from vmd.frames import LatestFrameBuffer
from vmd.sources.file import FileReader
from vmd.sources.rtsp import RtspReader
from vmd.worker import CameraWorker


def build_source(camera: CameraConfig, loop: bool = False):
    """Pick a source implementation from the camera URL."""
    if camera.url.lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
        return RtspReader(camera.url, camera.name)
    return FileReader(camera.url, camera.name, loop=loop)


def build_workers(
    config: AppConfig, debug: bool = False, loop: bool = False
) -> tuple[list[CameraWorker], dict[str, LatestFrameBuffer]]:
    workers: list[CameraWorker] = []
    buffers: dict[str, LatestFrameBuffer] = {}
    for camera in config.cameras:
        if not camera.enabled:
            continue
        buffer = LatestFrameBuffer()
        buffers[camera.name] = buffer
        workers.append(
            CameraWorker(
                camera=camera,
                source=build_source(camera, loop=loop),
                motion=config.motion,
                buffer=buffer,
                debug=debug,
            )
        )
    return workers, buffers


def run_headless(config: AppConfig, max_frames: int = 1000, debug: bool = False) -> dict:
    """Run the pipeline without a UI. Returns a per-camera report."""
    workers, buffers = build_workers(config, debug=debug)
    report: dict[str, dict] = {}
    for worker in workers:
        motion_frames = 0
        for _ in range(max_frames):
            if not worker.step():
                break
            buffers[worker.camera.name].get()  # drain so drops are not counted
            if worker.stats.last_kept > 0:
                motion_frames += 1
        report[worker.camera.name] = {
            "frames_read": worker.stats.frames_read,
            "frames_analysed": worker.stats.frames_analysed,
            "motion_frames": motion_frames,
            "gate_resets": worker.stats.gate_resets,
        }
        worker.close()
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vmd", description="Video motion detection")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--debug", action="store_true", help="start with debug overlay on")
    parser.add_argument(
        "--headless",
        type=int,
        default=0,
        metavar="N",
        help="process N frames per camera without a UI and print a report",
    )
    parser.add_argument("--loop", action="store_true", help="loop file sources forever")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)

    if args.headless:
        report = run_headless(config, max_frames=args.headless, debug=args.debug)
        for name, values in report.items():
            print(name, values)
        return 0

    from PySide6.QtWidgets import QApplication

    from vmd.ui.main_window import MainWindow

    workers, buffers = build_workers(config, debug=args.debug, loop=args.loop)
    stats = {worker.camera.name: worker.stats for worker in workers}

    app = QApplication(sys.argv)
    window = MainWindow(config, buffers=buffers, stats=stats)

    def set_debug(enabled: bool) -> None:
        for worker in workers:
            worker.debug = enabled

    window.debug_toggled.connect(set_debug)
    window.show()

    threads = [threading.Thread(target=worker.run, daemon=True) for worker in workers]
    for thread in threads:
        thread.start()

    try:
        return app.exec()
    finally:
        for worker in workers:
            worker.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

Add the console script to `pyproject.toml` under `[project]`:

```toml
[project.scripts]
vmd = "vmd.app:main"
```

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest -v`
Expected: PASS — all tests across all modules.

- [ ] **Step 5: Manual smoke check**

Record a short clip of yourself walking, then:

```bash
cp config.example.yaml config.yaml
# edit config.yaml: set the single camera's url to the clip path
uv run vmd --config config.yaml --headless 500
uv run vmd --config config.yaml --debug --loop
```

Expected: the headless run prints non-zero `motion_frames`; the windowed run shows the clip in the top-left tile with green boxes around you and, with **Debug overlay** ticked, the blue motion mask and red rejected blobs with reasons.

- [ ] **Step 6: Commit**

```bash
git add vmd/app.py pyproject.toml tests/test_app.py
git commit -m "feat: add CLI entry point, headless runner and end-to-end tests"
```

---

## Self-review notes

**Spec coverage for phases 1–2:** RTSP reader with backoff (Task 5), file reader (Task 4), 2×2 live grid with health badges and fps (Task 10), motion gate (Task 6), gates 1–3 of the suppression chain with per-rejection reasons (Task 7), debug view showing mask and rejected blobs (Tasks 8, 10), drop-frame policy (Task 3), file-input mode running the identical pipeline (Tasks 4, 11).

**Deliberately not in this plan** — they belong to Plan B and Plan C, and this is recorded so nobody treats them as gaps: detector and crops, tracker and N-of-M, gates 4–7, alarm sound and acknowledge, SQLite events, snapshots and clips, retention, mask editor, sensitivity presets, OpenVINO and CUDA backends, `--bench`, tiled sweep, PTZ and thermal.

**Known limitation carried forward:** the RTSP reader is polled by the worker, so a stalled TCP read inside OpenCV can block that camera's thread. Plan B should set `CAP_PROP_OPEN_TIMEOUT_MSEC` and `CAP_PROP_READ_TIMEOUT_MSEC` on the capture, and this is noted as the first task there.
