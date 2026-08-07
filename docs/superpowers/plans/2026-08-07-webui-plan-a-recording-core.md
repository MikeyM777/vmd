# VMD Web UI — Plan A: Recording Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A headless service that records one or more RTSP streams continuously to disk as 5-minute segments, indexes them, and deletes old footage by age and by storage budget — warning before it deletes, and never stopping recording.

**Architecture:** Five small modules with no shared state: `settings` (operator config on disk), `index` (SQLite catalogue of segments), `recorder` (wraps one ffmpeg process per stream, copying without re-encoding), `retention` (a pure planner that decides what to delete, plus a thin applier), and `supervisor` (restarts anything that dies). A thin `record_main` wires them into a loop. Every module is testable without a camera: the recorder is driven by an injected process spawner in unit tests and by a real ffmpeg-generated video in the integration test.

**Tech Stack:** Python 3.11+, pydantic v2, SQLite (stdlib), ffmpeg (external binary, already installed), pytest.

**Spec:** `docs/superpowers/specs/2026-08-07-vmd-webui-design.md` — this plan covers §7 (recording), §8 (storage and retention), §11 (settings), and the supervisor half of §4/§12. Live view, PTZ, bitrate control and playback are Plans B–D.

**Repo state:** `vmd/__init__.py`, `tests/__init__.py`, `pyproject.toml` and `uv.lock` already exist from earlier work (commit `8ff0535`). `uv run pytest` works. The earlier `2026-08-06-vmd-plan-a-capture-and-motion.md` plan is superseded except for its detection content; do not implement it.

---

## File Structure

| File | Responsibility |
|---|---|
| `vmd/settings.py` | Operator settings: models, JSON load/save, free-space detection |
| `vmd/storage/__init__.py` | Package marker |
| `vmd/storage/index.py` | `Segment` record and `SegmentIndex` — the SQLite catalogue |
| `vmd/storage/discovery.py` | `find_closed_segments()` — which files on disk ffmpeg has finished writing |
| `vmd/storage/recorder.py` | `SegmentRecorder` — builds and supervises one ffmpeg process |
| `vmd/storage/retention.py` | `RetentionPlan`, `plan_retention()` (pure), `apply_plan()` |
| `vmd/supervisor.py` | `Managed`, `Supervisor` — restart anything that dies |
| `vmd/record_main.py` | CLI entry point and the `run_once()` loop body |
| `tests/test_*.py` | One test module per source module |

Deliberately not in this plan: the web server, go2rtc, PTZ, radio polling, bitrate control, playback. They are Plans B–D and depend on this one.

---

### Task 1: Settings

**Files:**
- Create: `vmd/settings.py`
- Test: `tests/test_settings.py`

Settings are operator-supplied and stored as JSON next to the application. A missing file is not an error — it is first run, and yields defaults.

- [ ] **Step 1: Write the failing test**

Create `tests/test_settings.py`:

```python
import json

import pytest

from vmd.settings import (
    Settings,
    SettingsError,
    detect_free_bytes,
    load_settings,
    save_settings,
)


def test_missing_file_yields_defaults(tmp_path):
    settings = load_settings(tmp_path / "nope.json")
    assert isinstance(settings, Settings)
    assert settings.storage.budget_gb == 100.0
    assert settings.storage.budget_enabled is True
    assert settings.storage.retention_days is None
    assert settings.target_distance_m == 700.0


def test_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.camera.host = "192.168.1.50"
    settings.camera.username = "admin"
    settings.storage.budget_gb = 600.0
    settings.storage.retention_days = 13
    save_settings(settings, path)

    loaded = load_settings(path)
    assert loaded.camera.host == "192.168.1.50"
    assert loaded.camera.username == "admin"
    assert loaded.storage.budget_gb == 600.0
    assert loaded.storage.retention_days == 13


def test_streams_are_loaded(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "camera": {
                    "host": "10.0.0.2",
                    "streams": [
                        {"name": "thermal", "url": "rtsp://10.0.0.2/thermal"},
                        {"name": "visible", "url": "rtsp://10.0.0.2/visible", "enabled": False},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings(path)
    assert [s.name for s in settings.camera.streams] == ["thermal", "visible"]
    assert settings.camera.streams[0].enabled is True
    assert settings.camera.streams[1].enabled is False


def test_malformed_json_raises(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SettingsError, match="could not be read"):
        load_settings(path)


def test_zero_budget_rejected(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"storage": {"budget_gb": 0}}), encoding="utf-8")
    with pytest.raises(SettingsError, match="budget_gb"):
        load_settings(path)


def test_budget_bytes_conversion():
    settings = Settings()
    settings.storage.budget_gb = 2.0
    assert settings.storage.budget_bytes == 2 * 1024**3


def test_detect_free_bytes_on_real_path(tmp_path):
    free = detect_free_bytes(tmp_path)
    assert free is not None
    assert free > 0


def test_detect_free_bytes_returns_none_for_bad_path(tmp_path):
    assert detect_free_bytes(tmp_path / "does" / "not" / "exist") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.settings'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/settings.py`:

```python
"""Operator settings: what the user configures, stored as JSON on disk."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class SettingsError(Exception):
    """Raised when a settings file exists but cannot be read or is invalid."""


class StreamSettings(BaseModel):
    name: str
    url: str
    enabled: bool = True


class CameraSettings(BaseModel):
    host: str = ""
    username: str = ""
    password: str = ""
    streams: list[StreamSettings] = Field(default_factory=list)


class RadioSettings(BaseModel):
    """Ubiquiti airOS radio. Optional: bitrate control falls back to video statistics."""

    host: str = ""
    username: str = ""
    password: str = ""
    enabled: bool = False


class StorageSettings(BaseModel):
    root: Path = Path("recordings")
    budget_gb: float = 100.0
    budget_enabled: bool = True
    retention_days: int | None = None  # None disables the age rule
    warn_at_fraction: float = 0.9
    segment_seconds: int = 300

    @field_validator("budget_gb")
    @classmethod
    def _budget_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("budget_gb must be greater than 0")
        return value

    @field_validator("retention_days")
    @classmethod
    def _days_positive(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("retention_days must be greater than 0, or null to disable")
        return value

    @property
    def budget_bytes(self) -> int:
        return int(self.budget_gb * 1024**3)


class BitrateSettings(BaseModel):
    mode: Literal["auto", "manual"] = "auto"
    floor_kbps: int = 1000
    ceiling_kbps: int = 5000
    manual_kbps: int = 3000


class Settings(BaseModel):
    camera: CameraSettings = Field(default_factory=CameraSettings)
    radio: RadioSettings = Field(default_factory=RadioSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    bitrate: BitrateSettings = Field(default_factory=BitrateSettings)
    target_distance_m: float = 700.0


def load_settings(path: str | Path) -> Settings:
    """Load settings. A missing file means first run and yields defaults."""
    path = Path(path)
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SettingsError(f"settings file could not be read: {path}: {exc}") from exc
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        raise SettingsError(f"invalid settings in {path}:\n{exc}") from exc


def save_settings(settings: Settings, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")


def detect_free_bytes(path: str | Path) -> int | None:
    """Free space on the drive holding `path`, or None if it cannot be determined."""
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_settings.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/settings.py tests/test_settings.py
git commit -m "feat: add operator settings with JSON persistence"
```

---

### Task 2: Segment index

**Files:**
- Create: `vmd/storage/__init__.py`
- Create: `vmd/storage/index.py`
- Test: `tests/test_index.py`

The catalogue of what has been recorded. Everything downstream — retention, the playback timeline, the status display — reads this instead of scanning the disk.

- [ ] **Step 1: Write the failing test**

Create `tests/test_index.py`:

```python
from vmd.storage.index import Segment, SegmentIndex


def build(tmp_path):
    return SegmentIndex(tmp_path / "segments.db")


def test_add_and_read_back(tmp_path):
    index = build(tmp_path)
    segment_id = index.add("thermal", "/rec/a.mp4", start=100.0, end=400.0, size_bytes=1000)
    segments = index.all()
    assert len(segments) == 1
    assert segments[0].id == segment_id
    assert segments[0].stream == "thermal"
    assert segments[0].path == "/rec/a.mp4"
    assert segments[0].start == 100.0
    assert segments[0].size_bytes == 1000
    index.close()


def test_all_is_ordered_by_start(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/c.mp4", 300.0, 600.0, 10)
    index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    index.add("thermal", "/rec/b.mp4", 200.0, 500.0, 10)
    assert [s.path for s in index.all()] == ["/rec/a.mp4", "/rec/b.mp4", "/rec/c.mp4"]
    index.close()


def test_filter_by_stream(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/t.mp4", 100.0, 400.0, 10)
    index.add("visible", "/rec/v.mp4", 100.0, 400.0, 10)
    assert [s.stream for s in index.all(stream="visible")] == ["visible"]
    index.close()


def test_oldest(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/b.mp4", 200.0, 500.0, 10)
    index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    assert index.oldest().path == "/rec/a.mp4"
    index.close()


def test_oldest_is_none_when_empty(tmp_path):
    index = build(tmp_path)
    assert index.oldest() is None
    index.close()


def test_total_bytes(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 1500)
    index.add("visible", "/rec/b.mp4", 100.0, 400.0, 2500)
    assert index.total_bytes() == 4000
    index.close()


def test_delete_removes_row(tmp_path):
    index = build(tmp_path)
    segment_id = index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    index.delete(segment_id)
    assert index.all() == []
    assert index.total_bytes() == 0
    index.close()


def test_adding_the_same_path_twice_is_ignored(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    assert len(index.all()) == 1
    index.close()


def test_gaps_between_segments(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/a.mp4", 0.0, 300.0, 10)
    index.add("thermal", "/rec/b.mp4", 300.0, 600.0, 10)
    index.add("thermal", "/rec/c.mp4", 900.0, 1200.0, 10)  # 300s hole before this
    gaps = index.gaps("thermal", 0.0, 1200.0)
    assert gaps == [(600.0, 900.0)]
    index.close()


def test_gaps_include_edges(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/a.mp4", 200.0, 400.0, 10)
    gaps = index.gaps("thermal", 0.0, 600.0)
    assert gaps == [(0.0, 200.0), (400.0, 600.0)]
    index.close()


def test_gaps_with_no_segments_is_the_whole_window(tmp_path):
    index = build(tmp_path)
    assert index.gaps("thermal", 0.0, 600.0) == [(0.0, 600.0)]
    index.close()


def test_index_survives_reopen(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    index.close()
    reopened = build(tmp_path)
    assert len(reopened.all()) == 1
    reopened.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.storage'`

- [ ] **Step 3: Write minimal implementation**

Create an empty `vmd/storage/__init__.py`.

Create `vmd/storage/index.py`:

```python
"""SQLite catalogue of recorded segments."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    stream     TEXT    NOT NULL,
    path       TEXT    NOT NULL UNIQUE,
    start      REAL    NOT NULL,
    end        REAL    NOT NULL,
    size_bytes INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS segments_start ON segments (stream, start);
"""


@dataclass(frozen=True)
class Segment:
    id: int
    stream: str
    path: str
    start: float  # epoch seconds
    end: float
    size_bytes: int

    @property
    def duration(self) -> float:
        return self.end - self.start


class SegmentIndex:
    """The record of what exists on disk. Never scans the filesystem."""

    def __init__(self, db_path: str | Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(db_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def add(self, stream: str, path: str, start: float, end: float, size_bytes: int) -> int:
        """Register a segment. Adding the same path twice is a no-op."""
        cursor = self._connection.execute(
            "INSERT OR IGNORE INTO segments (stream, path, start, end, size_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            (stream, path, start, end, size_bytes),
        )
        self._connection.commit()
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        existing = self._connection.execute(
            "SELECT id FROM segments WHERE path = ?", (path,)
        ).fetchone()
        return int(existing["id"])

    def all(self, stream: str | None = None) -> list[Segment]:
        if stream is None:
            rows = self._connection.execute(
                "SELECT * FROM segments ORDER BY start, id"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM segments WHERE stream = ? ORDER BY start, id", (stream,)
            ).fetchall()
        return [self._to_segment(row) for row in rows]

    def oldest(self, stream: str | None = None) -> Segment | None:
        segments = self.all(stream)
        return segments[0] if segments else None

    def total_bytes(self) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM segments"
        ).fetchone()
        return int(row["total"])

    def delete(self, segment_id: int) -> None:
        self._connection.execute("DELETE FROM segments WHERE id = ?", (segment_id,))
        self._connection.commit()

    def gaps(
        self, stream: str, window_start: float, window_end: float, min_gap: float = 1.0
    ) -> list[tuple[float, float]]:
        """Periods inside the window with no recorded coverage."""
        segments = [
            s for s in self.all(stream) if s.end > window_start and s.start < window_end
        ]
        gaps: list[tuple[float, float]] = []
        cursor = window_start
        for segment in segments:
            if segment.start - cursor >= min_gap:
                gaps.append((cursor, segment.start))
            cursor = max(cursor, segment.end)
        if window_end - cursor >= min_gap:
            gaps.append((cursor, window_end))
        return gaps

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _to_segment(row: sqlite3.Row) -> Segment:
        return Segment(
            id=int(row["id"]),
            stream=row["stream"],
            path=row["path"],
            start=float(row["start"]),
            end=float(row["end"]),
            size_bytes=int(row["size_bytes"]),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_index.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/storage tests/test_index.py
git commit -m "feat: add SQLite segment index with coverage gap queries"
```

---

### Task 3: Closed-segment discovery

**Files:**
- Create: `vmd/storage/discovery.py`
- Test: `tests/test_discovery.py`

ffmpeg writes segments continuously; the newest file is still open. This decides which files are finished and safe to index.

- [ ] **Step 1: Write the failing test**

Create `tests/test_discovery.py`:

```python
import os

from vmd.storage.discovery import find_closed_segments, parse_segment_start


def touch(path, mtime):
    path.write_bytes(b"x" * 10)
    os.utime(path, (mtime, mtime))


def test_no_files_returns_nothing(tmp_path):
    assert find_closed_segments(tmp_path, now=1000.0) == []


def test_single_file_is_never_closed(tmp_path):
    touch(tmp_path / "2026-08-07_10-00-00.mp4", 100.0)
    assert find_closed_segments(tmp_path, now=1000.0) == []


def test_older_file_is_closed_when_a_newer_one_exists(tmp_path):
    touch(tmp_path / "2026-08-07_10-00-00.mp4", 100.0)
    touch(tmp_path / "2026-08-07_10-05-00.mp4", 400.0)
    closed = find_closed_segments(tmp_path, now=1000.0)
    assert [p.name for p in closed] == ["2026-08-07_10-00-00.mp4"]


def test_recently_written_file_is_not_closed_yet(tmp_path):
    touch(tmp_path / "2026-08-07_10-00-00.mp4", 998.0)
    touch(tmp_path / "2026-08-07_10-05-00.mp4", 999.0)
    assert find_closed_segments(tmp_path, now=1000.0, settle_seconds=5.0) == []


def test_empty_files_are_ignored(tmp_path):
    (tmp_path / "2026-08-07_10-00-00.mp4").write_bytes(b"")
    os.utime(tmp_path / "2026-08-07_10-00-00.mp4", (100.0, 100.0))
    touch(tmp_path / "2026-08-07_10-05-00.mp4", 400.0)
    assert find_closed_segments(tmp_path, now=1000.0) == []


def test_non_mp4_files_are_ignored(tmp_path):
    touch(tmp_path / "notes.txt", 100.0)
    touch(tmp_path / "2026-08-07_10-05-00.mp4", 400.0)
    assert find_closed_segments(tmp_path, now=1000.0) == []


def test_already_seen_paths_are_skipped(tmp_path):
    first = tmp_path / "2026-08-07_10-00-00.mp4"
    touch(first, 100.0)
    touch(tmp_path / "2026-08-07_10-05-00.mp4", 400.0)
    closed = find_closed_segments(tmp_path, now=1000.0, seen={str(first)})
    assert closed == []


def test_parse_segment_start():
    assert parse_segment_start("2026-08-07_14-35-00.mp4") is not None


def test_parse_segment_start_returns_none_for_junk():
    assert parse_segment_start("recording.mp4") is None


def test_parse_segment_start_is_local_time_epoch():
    import datetime

    parsed = parse_segment_start("2026-08-07_14-35-00.mp4")
    expected = datetime.datetime(2026, 8, 7, 14, 35, 0).timestamp()
    assert parsed == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.storage.discovery'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/storage/discovery.py`:

```python
"""Deciding which segment files ffmpeg has finished writing."""

from __future__ import annotations

import datetime
from pathlib import Path

SEGMENT_FORMAT = "%Y-%m-%d_%H-%M-%S"


def parse_segment_start(filename: str) -> float | None:
    """Epoch seconds encoded in a segment filename, or None if it does not match.

    Filenames are UTC: the recorder runs ffmpeg with TZ=UTC so that names stay monotonic
    across daylight-saving transitions. Reading them as local time would shift every
    timestamp by the UTC offset.
    """
    stem = Path(filename).stem
    try:
        parsed = datetime.datetime.strptime(stem, SEGMENT_FORMAT)
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.timezone.utc).timestamp()


def find_closed_segments(
    directory: str | Path,
    now: float,
    settle_seconds: float = 5.0,
    seen: set[str] | None = None,
) -> list[Path]:
    """Segment files that are finished and not yet indexed.

    A file counts as finished when a newer file exists (ffmpeg has moved on) and it has
    not been written to for `settle_seconds`. Empty files and non-mp4 files are ignored.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    seen = seen or set()

    candidates = []
    for path in directory.glob("*.mp4"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size == 0:
            continue
        candidates.append((stat.st_mtime, path))

    if len(candidates) < 2:
        return []  # the only file present is the one being written

    candidates.sort()
    newest_mtime = candidates[-1][0]
    closed = []
    for mtime, path in candidates:
        if mtime == newest_mtime:
            continue
        if now - mtime < settle_seconds:
            continue
        if str(path) in seen:
            continue
        closed.append(path)
    return closed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_discovery.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/storage/discovery.py tests/test_discovery.py
git commit -m "feat: detect which segment files ffmpeg has finished writing"
```

---

### Task 4: Segment recorder

**Files:**
- Create: `vmd/storage/recorder.py`
- Modify: `vmd/storage/discovery.py` — `parse_segment_start` must read filenames as UTC
- Test: `tests/test_recorder.py`
- Modify: `tests/test_discovery.py` — replace the local-time assertion with a UTC one

One ffmpeg process per stream, copying the incoming H.264 without re-encoding. The process spawner is injected so the lifecycle is testable without running ffmpeg.

**Timezone decision, and why this task changes Task 3's module.** ffmpeg's `-strftime 1`
formats the output filename using the process's local time. In a timezone with daylight
saving, the autumn transition repeats an hour of local time, so ffmpeg writes a filename
that already exists and **silently overwrites an hour of footage**, once a year. The
deployment is in Israel, which observes DST, so this is a real annual data-loss bug.

The fix is to run ffmpeg with `TZ=UTC` in its environment, making segment filenames UTC
and therefore always monotonic, and to parse them back as UTC. Local time becomes purely
a display concern for the playback UI, which is standard practice for surveillance
recording. Both halves must change together or timestamps will be wrong by the UTC
offset, which is why they are in one task.

- [ ] **Step 1: Write the failing test**

Create `tests/test_recorder.py`:

```python
import pytest

from vmd.storage.recorder import SegmentRecorder


class FakeProcess:
    def __init__(self, exit_codes=None):
        self._exit_codes = list(exit_codes or [])
        self.terminated = False

    def poll(self):
        return self._exit_codes.pop(0) if self._exit_codes else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def build(tmp_path, url="rtsp://example/stream", processes=None):
    spawned = []

    def spawn(command):
        process = (processes or []).pop(0) if processes else FakeProcess()
        spawned.append(command)
        return process

    recorder = SegmentRecorder(
        stream="thermal",
        source_url=url,
        output_dir=tmp_path / "thermal",
        segment_seconds=300,
        spawn=spawn,
    )
    return recorder, spawned


def test_command_copies_without_reencoding(tmp_path):
    recorder, _ = build(tmp_path)
    command = recorder.build_command()
    assert "-c" in command
    assert command[command.index("-c") + 1] == "copy"
    assert "libx264" not in command


def test_command_uses_rtsp_over_tcp_for_rtsp_urls(tmp_path):
    recorder, _ = build(tmp_path, url="rtsp://example/stream")
    command = recorder.build_command()
    assert "-rtsp_transport" in command
    assert command[command.index("-rtsp_transport") + 1] == "tcp"


def test_command_omits_rtsp_options_for_file_sources(tmp_path):
    recorder, _ = build(tmp_path, url=str(tmp_path / "clip.mp4"))
    assert "-rtsp_transport" not in recorder.build_command()


def test_command_sets_segment_duration_and_naming(tmp_path):
    recorder, _ = build(tmp_path)
    command = recorder.build_command()
    assert command[command.index("-segment_time") + 1] == "300"
    assert command[command.index("-f") + 1] == "segment"
    assert command[-1].endswith("%Y-%m-%d_%H-%M-%S.mp4")


def test_start_creates_output_directory(tmp_path):
    recorder, _ = build(tmp_path)
    recorder.start()
    assert (tmp_path / "thermal").is_dir()


def test_start_spawns_the_process(tmp_path):
    recorder, spawned = build(tmp_path)
    recorder.start()
    assert len(spawned) == 1
    assert recorder.running is True


def test_running_is_false_after_the_process_exits(tmp_path):
    recorder, _ = build(tmp_path, processes=[FakeProcess(exit_codes=[1])])
    recorder.start()
    assert recorder.running is False


def test_stop_terminates_the_process(tmp_path):
    process = FakeProcess()
    recorder, _ = build(tmp_path, processes=[process])
    recorder.start()
    recorder.stop()
    assert process.terminated is True
    assert recorder.running is False


def test_starting_twice_does_not_spawn_twice(tmp_path):
    recorder, spawned = build(tmp_path)
    recorder.start()
    recorder.start()
    assert len(spawned) == 1


def test_running_is_false_before_start(tmp_path):
    recorder, _ = build(tmp_path)
    assert recorder.running is False
```

Also update the timezone half. In `tests/test_discovery.py`, **replace** the existing
`test_parse_segment_start_is_local_time_epoch` with this test — the other ten tests in
that file stay exactly as they are:

```python
def test_parse_segment_start_is_utc_epoch(tmp_path):
    # Segment filenames are written by ffmpeg under TZ=UTC, so they must be read back
    # as UTC. Reading them as local time would shift every timestamp by the UTC offset
    # and would make the autumn daylight-saving hour ambiguous.
    import datetime

    parsed = parse_segment_start("2026-08-07_14-35-00.mp4")
    expected = datetime.datetime(
        2026, 8, 7, 14, 35, 0, tzinfo=datetime.timezone.utc
    ).timestamp()
    assert parsed == expected
```

And add this test to `tests/test_recorder.py`, proving the recorder really pins the
timezone rather than relying on the machine's:

```python
def test_default_spawn_pins_the_timezone_to_utc(monkeypatch):
    from vmd.storage import recorder as recorder_module

    captured = {}

    def fake_popen(command, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(recorder_module.subprocess, "Popen", fake_popen)
    recorder_module._default_spawn(["ffmpeg", "-version"])
    assert captured["env"]["TZ"] == "UTC"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.storage.recorder'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/storage/recorder.py`:

```python
"""One ffmpeg process per stream, writing timestamped segments."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from vmd.storage.discovery import SEGMENT_FORMAT

RTSP_SCHEMES = ("rtsp://", "rtsps://")


def _default_spawn(command: list[str]):
    # TZ=UTC so ffmpeg's -strftime filenames are UTC and therefore monotonic. With local
    # time, the autumn daylight-saving transition repeats an hour and ffmpeg overwrites
    # the segments it already wrote for that hour.
    environment = {**os.environ, "TZ": "UTC"}
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=environment,
    )


class SegmentRecorder:
    """Records one stream to disk as fixed-length segments, without re-encoding."""

    def __init__(
        self,
        stream: str,
        source_url: str,
        output_dir: str | Path,
        segment_seconds: int = 300,
        ffmpeg: str = "ffmpeg",
        spawn: Callable[[list[str]], object] = _default_spawn,
    ) -> None:
        self.stream = stream
        self.source_url = source_url
        self.output_dir = Path(output_dir)
        self.segment_seconds = segment_seconds
        self.ffmpeg = ffmpeg
        self._spawn = spawn
        self._process = None

    def build_command(self) -> list[str]:
        command = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if self.source_url.lower().startswith(RTSP_SCHEMES):
            # Do not add -stimeout here: it was renamed and then removed in modern
            # ffmpeg builds, and an unknown option makes ffmpeg exit immediately.
            command += ["-rtsp_transport", "tcp"]
        command += [
            "-i", self.source_url,
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(self.segment_seconds),
            "-segment_format", "mp4",
            "-reset_timestamps", "1",
            "-strftime", "1",
            str(self.output_dir / f"{SEGMENT_FORMAT}.mp4"),
        ]
        return command

    def start(self) -> None:
        if self.running:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._process = self._spawn(self.build_command())

    def stop(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except Exception:  # noqa: BLE001 - a stuck ffmpeg must not block shutdown
            pass
        self._process = None

    @property
    def running(self) -> bool:
        if self._process is None:
            return False
        return self._process.poll() is None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_recorder.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/storage/recorder.py tests/test_recorder.py
git commit -m "feat: add ffmpeg segment recorder with injectable process spawner"
```

---

### Task 5: Recorder integration test with real ffmpeg

**Files:**
- Test: `tests/test_recorder_integration.py`

Proves the ffmpeg command actually produces playable segments. Uses ffmpeg's built-in test pattern generator, so no camera and no network are needed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_recorder_integration.py`:

```python
"""Runs real ffmpeg. Skipped automatically if ffmpeg is not on PATH."""

import shutil
import subprocess
import time

import pytest

from vmd.storage.discovery import find_closed_segments, parse_segment_start
from vmd.storage.recorder import SegmentRecorder

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def source_clip(tmp_path):
    """12 seconds of H.264 test pattern, so segmenting has something to copy."""
    path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
            "-t", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-g", "10", str(path),
        ],
        check=True,
    )
    return path


def test_produces_multiple_playable_segments(tmp_path, source_clip):
    recorder = SegmentRecorder(
        stream="test",
        source_url=str(source_clip),
        output_dir=tmp_path / "out",
        segment_seconds=4,
    )
    recorder.start()
    deadline = time.time() + 60
    while recorder.running and time.time() < deadline:
        time.sleep(0.5)
    recorder.stop()

    written = sorted((tmp_path / "out").glob("*.mp4"))
    assert len(written) >= 2, f"expected several segments, got {[p.name for p in written]}"
    for path in written:
        assert path.stat().st_size > 0
        assert parse_segment_start(path.name) is not None


def test_segments_are_readable_by_ffprobe(tmp_path, source_clip):
    recorder = SegmentRecorder(
        stream="test",
        source_url=str(source_clip),
        output_dir=tmp_path / "out",
        segment_seconds=4,
    )
    recorder.start()
    deadline = time.time() + 60
    while recorder.running and time.time() < deadline:
        time.sleep(0.5)
    recorder.stop()

    first = sorted((tmp_path / "out").glob("*.mp4"))[0]
    result = subprocess.run(
        [
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-show_entries", "format=duration", "-of", "csv=p=0", str(first),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert float(result.stdout.strip()) > 0


def test_discovery_finds_the_completed_segments(tmp_path, source_clip):
    recorder = SegmentRecorder(
        stream="test",
        source_url=str(source_clip),
        output_dir=tmp_path / "out",
        segment_seconds=4,
    )
    recorder.start()
    deadline = time.time() + 60
    while recorder.running and time.time() < deadline:
        time.sleep(0.5)
    recorder.stop()

    closed = find_closed_segments(tmp_path / "out", now=time.time() + 10)
    assert len(closed) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_recorder_integration.py -v`
Expected: PASS if Tasks 3 and 4 are correct. If it FAILS, the ffmpeg command in Task 4 is wrong — fix `build_command()` until real segments appear. This is the point of the task: the unit tests only check the command's shape, not that it works.

- [ ] **Step 3: Run the whole suite**

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_recorder_integration.py
git commit -m "test: verify the recorder produces playable segments with real ffmpeg"
```

---

### Task 6: Retention planner

**Files:**
- Create: `vmd/storage/retention.py`
- Test: `tests/test_retention.py`

A pure function that decides what to delete and whether to warn. No filesystem access, so every rule is trivially testable.

- [ ] **Step 1: Write the failing test**

Create `tests/test_retention.py`:

```python
from vmd.storage.index import Segment
from vmd.storage.retention import plan_retention

HOUR = 3600.0
DAY = 86400.0
GB = 1024**3


def segment(index, start, size_bytes=GB, stream="thermal"):
    return Segment(
        id=index,
        stream=stream,
        path=f"/rec/{index}.mp4",
        start=start,
        end=start + 300.0,
        size_bytes=size_bytes,
    )


def test_nothing_to_do_when_under_budget_and_within_age():
    segments = [segment(1, 0.0), segment(2, 300.0)]
    plan = plan_retention(
        segments, now=600.0, budget_bytes=100 * GB, budget_enabled=True,
        retention_days=30, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert plan.delete == []
    assert plan.warning is None


def test_age_rule_deletes_old_segments():
    segments = [segment(1, 0.0), segment(2, 20 * DAY)]
    plan = plan_retention(
        segments, now=21 * DAY, budget_bytes=100 * GB, budget_enabled=False,
        retention_days=13, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert [s.id for s in plan.delete] == [1]


def test_age_rule_disabled_when_days_is_none():
    segments = [segment(1, 0.0)]
    plan = plan_retention(
        segments, now=999 * DAY, budget_bytes=100 * GB, budget_enabled=False,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert plan.delete == []


def test_budget_rule_deletes_oldest_first():
    segments = [segment(i, i * 300.0) for i in range(1, 6)]  # 5 GB total
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=3 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert [s.id for s in plan.delete] == [1, 2]
    assert plan.used_bytes == 5 * GB


def test_budget_rule_disabled_leaves_everything():
    segments = [segment(i, i * 300.0) for i in range(1, 6)]
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=1 * GB, budget_enabled=False,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert plan.delete == []


def test_both_rules_together_do_not_double_count():
    segments = [segment(1, 0.0), segment(2, 20 * DAY), segment(3, 20 * DAY + 300)]
    plan = plan_retention(
        segments, now=21 * DAY, budget_bytes=1 * GB, budget_enabled=True,
        retention_days=13, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    deleted = [s.id for s in plan.delete]
    assert deleted == sorted(set(deleted))
    assert 1 in deleted


def test_warning_appears_near_the_budget():
    segments = [segment(i, i * 300.0) for i in range(1, 10)]  # 9 GB
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=10 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=GB / HOUR,
    )
    assert plan.delete == []
    assert plan.warning is not None
    assert "will be deleted" in plan.warning


def test_no_warning_when_comfortably_under_budget():
    segments = [segment(1, 0.0)]
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=100 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert plan.warning is None


def test_no_warning_when_budget_rule_is_off():
    segments = [segment(i, i * 300.0) for i in range(1, 10)]
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=10 * GB, budget_enabled=False,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=GB / HOUR,
    )
    assert plan.warning is None


def test_used_and_budget_are_reported():
    segments = [segment(1, 0.0, size_bytes=2 * GB)]
    plan = plan_retention(
        segments, now=10.0, budget_bytes=5 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert plan.used_bytes == 2 * GB
    assert plan.budget_bytes == 5 * GB


def test_zero_write_rate_does_not_divide_by_zero():
    segments = [segment(i, i * 300.0) for i in range(1, 10)]
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=10 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=0.0,
    )
    assert plan.warning is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_retention.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.storage.retention'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/storage/retention.py`:

```python
"""Deciding what footage to delete, and warning before it happens."""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from vmd.storage.index import Segment, SegmentIndex

DAY_SECONDS = 86400.0


@dataclass
class RetentionPlan:
    delete: list[Segment] = field(default_factory=list)
    warning: str | None = None
    used_bytes: int = 0
    budget_bytes: int = 0


def plan_retention(
    segments: list[Segment],
    now: float,
    budget_bytes: int,
    budget_enabled: bool,
    retention_days: int | None,
    warn_at_fraction: float,
    bytes_per_second: float,
) -> RetentionPlan:
    """Decide which segments to remove. Pure: no filesystem, no clock, no side effects.

    Two independent rules, either of which may be disabled:
      age    - remove anything that ended more than `retention_days` ago
      budget - while the total exceeds `budget_bytes`, remove the oldest
    """
    ordered = sorted(segments, key=lambda s: (s.start, s.id))
    used_bytes = sum(s.size_bytes for s in ordered)
    plan = RetentionPlan(used_bytes=used_bytes, budget_bytes=budget_bytes)

    doomed_ids: set[int] = set()

    if retention_days is not None:
        cutoff = now - retention_days * DAY_SECONDS
        for segment in ordered:
            if segment.end < cutoff:
                plan.delete.append(segment)
                doomed_ids.add(segment.id)

    if budget_enabled:
        remaining = used_bytes - sum(s.size_bytes for s in plan.delete)
        for segment in ordered:
            if remaining <= budget_bytes:
                break
            if segment.id in doomed_ids:
                continue
            plan.delete.append(segment)
            doomed_ids.add(segment.id)
            remaining -= segment.size_bytes

    if budget_enabled and not plan.delete and used_bytes >= warn_at_fraction * budget_bytes:
        plan.warning = _warning_text(ordered, used_bytes, budget_bytes, bytes_per_second)

    return plan


def _warning_text(
    ordered: list[Segment], used_bytes: int, budget_bytes: int, bytes_per_second: float
) -> str:
    percent = 100.0 * used_bytes / budget_bytes if budget_bytes else 100.0
    oldest = ordered[0] if ordered else None
    when = (
        datetime.datetime.fromtimestamp(oldest.start).strftime("%d %B")
        if oldest
        else "the oldest footage"
    )
    headroom = max(budget_bytes - used_bytes, 0)
    if bytes_per_second > 0:
        hours = headroom / (bytes_per_second * 3600.0)
        timing = f"in about {hours:.0f} hours" if hours >= 1 else "within the hour"
    else:
        timing = "once recording resumes"
    return f"Storage {percent:.0f}% full. Footage from {when} will be deleted {timing}."


def apply_plan(
    plan: RetentionPlan,
    index: SegmentIndex,
    unlink: Callable[[str], None] = os.unlink,
) -> int:
    """Delete the planned segments from disk and from the index. Returns the count.

    A file that is already gone is not an error - the index row is still removed, so the
    catalogue converges on the truth rather than accumulating dead entries.
    """
    removed = 0
    for segment in plan.delete:
        try:
            unlink(segment.path)
        except FileNotFoundError:
            pass
        except OSError:
            continue  # locked or unreadable: leave the row, try again next pass
        index.delete(segment.id)
        removed += 1
    return removed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_retention.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/storage/retention.py tests/test_retention.py
git commit -m "feat: add retention planner with age and budget rules"
```

---

### Task 7: Retention applier

**Files:**
- Test: `tests/test_retention_apply.py`

`apply_plan` was written in Task 6; this task proves it behaves correctly against a real index and real files.

- [ ] **Step 1: Write the failing test**

Create `tests/test_retention_apply.py`:

```python
from vmd.storage.index import SegmentIndex
from vmd.storage.retention import apply_plan, plan_retention

GB = 1024**3


def make_file(tmp_path, name, size=1024):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def test_deletes_files_and_index_rows(tmp_path):
    index = SegmentIndex(tmp_path / "segments.db")
    paths = []
    for i in range(4):
        path = make_file(tmp_path, f"seg{i}.mp4")
        paths.append(path)
        index.add("thermal", str(path), start=i * 300.0, end=i * 300.0 + 300.0, size_bytes=GB)

    plan = plan_retention(
        index.all(), now=10000.0, budget_bytes=2 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    removed = apply_plan(plan, index)

    assert removed == 2
    assert not paths[0].exists()
    assert not paths[1].exists()
    assert paths[2].exists()
    assert [s.path for s in index.all()] == [str(paths[2]), str(paths[3])]
    index.close()


def test_missing_file_still_clears_the_index_row(tmp_path):
    index = SegmentIndex(tmp_path / "segments.db")
    index.add("thermal", str(tmp_path / "ghost.mp4"), 0.0, 300.0, size_bytes=GB)
    plan = plan_retention(
        index.all(), now=10000.0, budget_bytes=1, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert apply_plan(plan, index) == 1
    assert index.all() == []
    index.close()


def test_undeletable_file_keeps_its_row_for_a_later_attempt(tmp_path):
    index = SegmentIndex(tmp_path / "segments.db")
    path = make_file(tmp_path, "locked.mp4")
    index.add("thermal", str(path), 0.0, 300.0, size_bytes=GB)
    plan = plan_retention(
        index.all(), now=10000.0, budget_bytes=1, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )

    def refuse(_path):
        raise PermissionError("file is in use")

    assert apply_plan(plan, index, unlink=refuse) == 0
    assert len(index.all()) == 1
    index.close()


def test_empty_plan_does_nothing(tmp_path):
    index = SegmentIndex(tmp_path / "segments.db")
    path = make_file(tmp_path, "keep.mp4")
    index.add("thermal", str(path), 0.0, 300.0, size_bytes=1024)
    plan = plan_retention(
        index.all(), now=400.0, budget_bytes=100 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert apply_plan(plan, index) == 0
    assert path.exists()
    index.close()
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run pytest tests/test_retention_apply.py -v`
Expected: PASS if Task 6 is correct. If any test FAILS, fix `apply_plan()` in `vmd/storage/retention.py` — do not change the tests.

- [ ] **Step 3: Commit**

```bash
git add tests/test_retention_apply.py
git commit -m "test: verify retention deletes files and index rows correctly"
```

---

### Task 8: Supervisor

**Files:**
- Create: `vmd/supervisor.py`
- Test: `tests/test_supervisor.py`

Restarts anything that dies. This is the mechanism behind the spec's requirement that no component failure can take down another.

- [ ] **Step 1: Write the failing test**

Create `tests/test_supervisor.py`:

```python
from vmd.supervisor import Managed, Supervisor


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeService:
    """Stands in for a recorder: start/stop plus a `running` flag."""

    def __init__(self, alive=True):
        self.running = alive
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1
        self.running = True

    def stop(self):
        self.stops += 1
        self.running = False


def build(services, clock=None):
    clock = clock or FakeClock()
    managed = [Managed(name=name, service=service) for name, service in services.items()]
    return Supervisor(managed, clock=clock, restart_delay=2.0), clock


def test_first_tick_starts_everything():
    service = FakeService(alive=False)
    supervisor, _ = build({"recorder": service})
    assert supervisor.tick() == ["recorder"]
    assert service.starts == 1


def test_healthy_service_is_not_restarted():
    service = FakeService(alive=False)
    supervisor, clock = build({"recorder": service})
    supervisor.tick()
    clock.advance(10.0)
    assert supervisor.tick() == []
    assert service.starts == 1


def test_dead_service_is_restarted_after_the_delay():
    service = FakeService(alive=False)
    supervisor, clock = build({"recorder": service})
    supervisor.tick()
    service.running = False  # it died
    clock.advance(10.0)
    assert supervisor.tick() == ["recorder"]
    assert service.starts == 2


def test_restart_waits_for_the_delay():
    service = FakeService(alive=False)
    supervisor, clock = build({"recorder": service})
    supervisor.tick()
    service.running = False
    clock.advance(0.5)  # less than restart_delay
    assert supervisor.tick() == []
    assert service.starts == 1


def test_one_service_dying_does_not_touch_another():
    dying = FakeService(alive=False)
    healthy = FakeService(alive=False)
    supervisor, clock = build({"recorder": dying, "streamer": healthy})
    supervisor.tick()
    dying.running = False
    clock.advance(10.0)
    assert supervisor.tick() == ["recorder"]
    assert healthy.starts == 1


def test_restart_counts_are_tracked():
    service = FakeService(alive=False)
    supervisor, clock = build({"recorder": service})
    supervisor.tick()
    for _ in range(3):
        service.running = False
        clock.advance(10.0)
        supervisor.tick()
    assert supervisor.restarts["recorder"] == 3


def test_stop_all_stops_every_service():
    first = FakeService(alive=False)
    second = FakeService(alive=False)
    supervisor, _ = build({"a": first, "b": second})
    supervisor.tick()
    supervisor.stop_all()
    assert first.stops == 1
    assert second.stops == 1


def test_a_service_that_throws_on_start_does_not_break_the_tick():
    class Exploding(FakeService):
        def start(self):
            self.starts += 1
            raise RuntimeError("cannot start")

    exploding = Exploding(alive=False)
    healthy = FakeService(alive=False)
    supervisor, _ = build({"bad": exploding, "good": healthy})
    restarted = supervisor.tick()
    assert "good" in restarted
    assert healthy.starts == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_supervisor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.supervisor'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/supervisor.py`:

```python
"""Keeps services alive. One failing service must never affect another."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Protocol

logger = logging.getLogger(__name__)


class Service(Protocol):
    running: bool

    def start(self) -> None: ...
    def stop(self) -> None: ...


@dataclass
class Managed:
    name: str
    service: Service


class Supervisor:
    """Restarts any service that is not running, after a short delay."""

    def __init__(
        self,
        managed: list[Managed],
        clock: Callable[[], float] = time.monotonic,
        restart_delay: float = 2.0,
    ) -> None:
        self.managed = managed
        self.restarts: dict[str, int] = {entry.name: 0 for entry in managed}
        self._clock = clock
        self._restart_delay = restart_delay
        self._next_attempt: dict[str, float] = {entry.name: 0.0 for entry in managed}
        self._started_once: set[str] = set()

    def tick(self) -> list[str]:
        """Check every service, start whatever is down. Returns the names started."""
        started: list[str] = []
        now = self._clock()
        for entry in self.managed:
            if entry.service.running:
                continue
            if now < self._next_attempt[entry.name]:
                continue
            try:
                entry.service.start()
            except Exception:  # noqa: BLE001 - one bad service must not stop the others
                logger.exception("failed to start %s", entry.name)
                self._next_attempt[entry.name] = now + self._restart_delay
                continue
            started.append(entry.name)
            self._next_attempt[entry.name] = now + self._restart_delay
            if entry.name in self._started_once:
                self.restarts[entry.name] += 1
            else:
                self._started_once.add(entry.name)
        return started

    def stop_all(self) -> None:
        for entry in self.managed:
            try:
                entry.service.stop()
            except Exception:  # noqa: BLE001 - shutdown must always complete
                logger.exception("failed to stop %s", entry.name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_supervisor.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add vmd/supervisor.py tests/test_supervisor.py
git commit -m "feat: add supervisor that restarts failed services independently"
```

---

### Task 9: Recording service wire-up

**Files:**
- Create: `vmd/record_main.py`
- Modify: `vmd/storage/index.py` — enable WAL and a busy timeout
- Test: `tests/test_record_main.py`

Ties everything together: start a recorder per enabled stream, index finished segments, run retention, keep everything alive.

**Findings carried in from earlier reviews.** Each was raised while building Tasks 1–8 and
deliberately deferred to here, because this is the first task where the pieces run together.

1. **SQLite is opened with defaults that break under a second connection.** `SegmentIndex`
   uses `check_same_thread=True` and no busy timeout, so sharing one index between this
   service loop and a future web request raises immediately, and a second connection can
   hit `database is locked` with no retry. Plan B will add a web server that reads this
   index. Fix the pragmas here, and record the threading rule explicitly.
2. **Retention must not re-read the whole index every 5 seconds.** `plan_retention` needs
   every segment, and `index.all()` at 100,000 rows costs hundreds of milliseconds. At a
   5-second cadence that is wasteful for a job whose input changes every 5 minutes. Run
   retention on its own slower cadence.
3. **A stuck deletion is invisible.** `apply_plan` returns how many segments it removed,
   not how many it was asked to remove. A permanently locked file makes it report a
   healthy-looking number forever while the budget is never met.
4. **A permanently failing recorder reports as healthy.** `Supervisor.restarts` counts only
   successful re-starts, so a stream that has failed every 2 seconds for a week still shows
   `0`. The status output must not imply health when a stream is down.
5. **Which settings file was used is not logged.** "Running with defaults" currently looks
   identical whether it is genuine first run or a typo'd path.
6. **The in-progress segment must never be deleted.** Discovery already excludes the file
   ffmpeg is still writing, so it never reaches the index and therefore never reaches
   retention. That is a load-bearing invariant of the whole design and deserves an explicit
   test rather than an assumption.

Restart-storm log volume and stalled-but-alive recorder detection are also real, but they
are behavioural additions rather than wiring, so they are Task 10.

- [ ] **Step 1: Write the failing test**

Create `tests/test_record_main.py`:

```python
import os
import time

from vmd.record_main import RecordingService, parse_args
from vmd.settings import Settings, StreamSettings
from vmd.storage.index import SegmentIndex

GB = 1024**3


def build_settings(tmp_path, budget_gb=100.0, retention_days=None):
    settings = Settings()
    settings.camera.streams = [
        StreamSettings(name="thermal", url="rtsp://example/thermal"),
        StreamSettings(name="visible", url="rtsp://example/visible", enabled=False),
    ]
    settings.storage.root = tmp_path / "recordings"
    settings.storage.budget_gb = budget_gb
    settings.storage.retention_days = retention_days
    settings.storage.segment_seconds = 4
    return settings


class FakeProcess:
    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def spawn_fake(command, log_path=None):
    # SegmentRecorder passes the log path as a second argument, so this stand-in
    # must accept it even though the fake never writes anything.
    return FakeProcess()


def test_only_enabled_streams_get_recorders(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    assert [r.stream for r in service.recorders] == ["thermal"]
    service.stop()


def test_each_stream_records_into_its_own_directory(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once()
    assert (tmp_path / "recordings" / "thermal").is_dir()
    service.stop()


def test_run_once_starts_the_recorder(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once()
    assert service.recorders[0].running is True
    service.stop()


def test_finished_segments_are_indexed(tmp_path):
    settings = build_settings(tmp_path)
    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once()

    directory = tmp_path / "recordings" / "thermal"
    for name, mtime in (("2026-08-07_10-00-00.mp4", 100.0), ("2026-08-07_10-05-00.mp4", 400.0)):
        path = directory / name
        path.write_bytes(b"x" * 2048)
        os.utime(path, (mtime, mtime))

    service.run_once(now=1000.0)
    indexed = service.index.all()
    assert [os.path.basename(s.path) for s in indexed] == ["2026-08-07_10-00-00.mp4"]
    assert indexed[0].stream == "thermal"
    assert indexed[0].size_bytes == 2048
    service.stop()


def test_a_segment_is_not_indexed_twice(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once()
    directory = tmp_path / "recordings" / "thermal"
    for name, mtime in (("2026-08-07_10-00-00.mp4", 100.0), ("2026-08-07_10-05-00.mp4", 400.0)):
        path = directory / name
        path.write_bytes(b"x" * 2048)
        os.utime(path, (mtime, mtime))
    service.run_once(now=1000.0)
    service.run_once(now=2000.0)
    assert len(service.index.all()) == 1
    service.stop()


def test_retention_deletes_over_budget(tmp_path):
    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3)  # a 3000-byte budget
    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once()
    directory = tmp_path / "recordings" / "thermal"
    names = ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4", "2026-08-07_10-10-00.mp4"]
    for offset, name in enumerate(names):
        path = directory / name
        path.write_bytes(b"x" * 2000)
        os.utime(path, (100.0 + offset, 100.0 + offset))
    service.run_once(now=1000.0)
    assert not (directory / names[0]).exists()
    service.stop()


def test_status_reports_what_the_ui_needs(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once()
    status = service.status()
    assert "streams" in status
    assert status["streams"][0]["name"] == "thermal"
    assert "used_bytes" in status
    assert "budget_bytes" in status
    assert "oldest" in status
    assert "warning" in status
    service.stop()


def test_index_persists_across_restarts(tmp_path):
    settings = build_settings(tmp_path)
    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once()
    directory = tmp_path / "recordings" / "thermal"
    for name, mtime in (("2026-08-07_10-00-00.mp4", 100.0), ("2026-08-07_10-05-00.mp4", 400.0)):
        path = directory / name
        path.write_bytes(b"x" * 2048)
        os.utime(path, (mtime, mtime))
    service.run_once(now=1000.0)
    service.stop()

    restarted = RecordingService(settings, spawn=spawn_fake)
    assert len(restarted.index.all()) == 1
    restarted.stop()


def test_parse_args_defaults():
    args = parse_args([])
    assert args.settings == "settings.json"
    assert args.once is False


def test_parse_args_accepts_settings_path():
    args = parse_args(["--settings", "/tmp/s.json", "--once"])
    assert args.settings == "/tmp/s.json"
    assert args.once is True


def test_status_reports_unhealthy_when_a_stream_is_down(tmp_path):
    class DeadProcess(FakeProcess):
        def poll(self):
            return 1

    service = RecordingService(build_settings(tmp_path), spawn=lambda c, log=None: DeadProcess())
    service.run_once()
    status = service.status()
    # A stream that never starts keeps `restarts` at zero, so health must never be
    # inferred from the restart count alone.
    assert status["streams"][0]["running"] is False
    assert status["streams"][0]["restarts"] == 0
    assert status["healthy"] is False
    service.stop()


def test_stuck_deletions_are_reported(tmp_path, monkeypatch):
    # `apply_plan`'s `unlink` default is bound at import time, so patching os.unlink
    # afterwards cannot reach it. The seam that does work is record_main's own module
    # reference to apply_plan, which is resolved at call time.
    from vmd import record_main as record_main_module
    from vmd.storage.retention import apply_plan as real_apply_plan

    def refuse(_path):
        raise PermissionError("file is in use")

    def refusing_apply_plan(plan, index, unlink=None):
        return real_apply_plan(plan, index, unlink=refuse)

    monkeypatch.setattr(record_main_module, "apply_plan", refusing_apply_plan)

    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3)
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=0.0)
    service.run_once()
    directory = tmp_path / "recordings" / "thermal"
    names = ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4", "2026-08-07_10-10-00.mp4"]
    for offset, name in enumerate(names):
        path = directory / name
        path.write_bytes(b"x" * 2000)
        os.utime(path, (100.0 + offset, 100.0 + offset))

    service.run_once(now=1000.0)
    status = service.status()
    assert status["stuck_deletions"] > 0
    assert status["healthy"] is False
    assert (directory / names[0]).exists(), "a refused deletion must leave the file alone"
    service.stop()


def test_the_segment_being_written_is_never_deleted(tmp_path):
    # The whole design rests on this: discovery excludes the file ffmpeg still has
    # open, so it never enters the index and retention can never reach it. If that
    # ever stopped being true, retention would delete a recording in progress.
    settings = build_settings(tmp_path, budget_gb=1 / 1024**3)  # absurdly small budget
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=0.0)
    service.run_once()
    directory = tmp_path / "recordings" / "thermal"
    closed = directory / "2026-08-07_10-00-00.mp4"
    open_now = directory / "2026-08-07_10-05-00.mp4"
    closed.write_bytes(b"x" * 2000)
    open_now.write_bytes(b"x" * 2000)
    os.utime(closed, (100.0, 100.0))
    os.utime(open_now, (400.0, 400.0))

    service.run_once(now=1000.0)

    assert open_now.exists(), "the segment still being written must never be deleted"
    assert str(open_now) not in [s.path for s in service.index.all()]
    service.stop()


def test_retention_survives_the_clock_going_backwards(tmp_path):
    # This machine may correct its clock by NTP after boot. A backwards step must not
    # stall retention until the clock catches up, or the disk fills in the meantime.
    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3)
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=60.0)
    service.run_once(now=1_000_000.0)  # retention runs, remembers a large timestamp

    directory = tmp_path / "recordings" / "thermal"
    names = ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4", "2026-08-07_10-10-00.mp4"]
    for offset, name in enumerate(names):
        path = directory / name
        path.write_bytes(b"x" * 2000)
        os.utime(path, (100.0 + offset, 100.0 + offset))

    service.run_once(now=500.0)  # clock stepped far backwards
    assert not (directory / names[0]).exists(), "retention must still run after a clock step"
    service.stop()


def test_retention_does_not_run_on_every_pass(tmp_path):
    settings = build_settings(tmp_path)
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=60.0)
    calls = []
    original = service._apply_retention

    def counted(now):
        calls.append(now)
        return original(now)

    service._apply_retention = counted
    service.run_once(now=1000.0)
    service.run_once(now=1005.0)
    service.run_once(now=1010.0)
    # Called every pass, but the expensive index read inside is rate-limited.
    assert len(calls) == 3
    assert service._last_retention == 1000.0
    service.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_record_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vmd.record_main'`

- [ ] **Step 3: Write minimal implementation**

Create `vmd/record_main.py`:

```python
"""The recording service: record every enabled stream, index it, enforce retention."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Callable

from vmd.settings import Settings, load_settings
from vmd.storage.discovery import find_closed_segments, parse_segment_start
from vmd.storage.index import SegmentIndex
from vmd.storage.recorder import SegmentRecorder
from vmd.storage.retention import apply_plan, plan_retention
from vmd.supervisor import Managed, Supervisor

logger = logging.getLogger(__name__)


class RecordingService:
    """Owns the recorders, the index and the retention pass."""

    def __init__(
        self,
        settings: Settings,
        spawn: Callable | None = None,
        retention_interval: float = 60.0,
    ) -> None:
        self.settings = settings
        self.root = Path(settings.storage.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = SegmentIndex(self.root / "segments.db")

        recorder_kwargs = {"spawn": spawn} if spawn else {}
        self.recorders = [
            SegmentRecorder(
                stream=stream.name,
                source_url=stream.url,
                output_dir=self.root / stream.name,
                segment_seconds=settings.storage.segment_seconds,
                **recorder_kwargs,
            )
            for stream in settings.camera.streams
            if stream.enabled
        ]
        self.supervisor = Supervisor(
            [Managed(name=r.stream, service=r) for r in self.recorders]
        )
        self._seen: set[str] = {s.path for s in self.index.all()}
        self._last_warning: str | None = None
        # Retention runs on its own slower cadence; see _apply_retention.
        self.retention_interval = retention_interval
        self._last_retention = 0.0
        self._stuck_deletions = 0

    def run_once(self, now: float | None = None) -> None:
        """One pass: keep recorders alive, index finished segments, apply retention."""
        now = time.time() if now is None else now
        self.supervisor.tick()
        self._index_new_segments(now)
        self._apply_retention(now)

    def run_forever(self, interval: float = 5.0) -> None:
        try:
            while True:
                self.run_once()
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        self.supervisor.stop_all()
        self.index.close()

    def status(self) -> dict:
        segments = self.index.all()
        used = sum(s.size_bytes for s in segments)
        oldest = segments[0].start if segments else None
        streams = [
            {
                "name": r.stream,
                "running": r.running,
                "restarts": self.supervisor.restarts.get(r.stream, 0),
                "exit_code": r.exit_code,
            }
            for r in self.recorders
        ]
        return {
            "streams": streams,
            # A stream that never starts successfully keeps `restarts` at zero, so
            # health must be derived from `running`, never from the restart count.
            "healthy": all(s["running"] for s in streams) and not self._stuck_deletions,
            "segments": len(segments),
            "used_bytes": used,
            "budget_bytes": self.settings.storage.budget_bytes,
            "oldest": oldest,
            "warning": self._last_warning,
            "stuck_deletions": self._stuck_deletions,
            "restarts": dict(self.supervisor.restarts),
        }

    def _index_new_segments(self, now: float) -> None:
        for recorder in self.recorders:
            for path in find_closed_segments(recorder.output_dir, now=now, seen=self._seen):
                start = parse_segment_start(path.name)
                if start is None:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                self.index.add(
                    stream=recorder.stream,
                    path=str(path),
                    start=start,
                    end=start + self.settings.storage.segment_seconds,
                    size_bytes=size,
                )
                self._seen.add(str(path))

    def _apply_retention(self, now: float) -> None:
        # Retention reads the entire index, which is expensive once the catalogue is
        # large. Its input only changes when a segment closes, so running it on the
        # 5-second loop cadence would be pure waste.
        #
        # The elapsed check deliberately tolerates a clock that moves backwards. This
        # machine may correct its time by NTP after boot, and a backwards step would
        # otherwise stall retention for the length of the jump while the disk fills.
        # A negative elapsed means the clock changed, so run rather than wait.
        elapsed = now - self._last_retention
        if self._last_retention and 0 <= elapsed < self.retention_interval:
            return
        self._last_retention = now

        storage = self.settings.storage
        segments = self.index.all()
        plan = plan_retention(
            segments,
            now=now,
            budget_bytes=storage.budget_bytes,
            budget_enabled=storage.budget_enabled,
            retention_days=storage.retention_days,
            warn_at_fraction=storage.warn_at_fraction,
            bytes_per_second=self._write_rate(segments),
        )
        self._last_warning = plan.warning
        if plan.warning:
            logger.warning(plan.warning)
        removed = apply_plan(plan, self.index)
        if removed:
            for segment in plan.delete:
                self._seen.discard(segment.path)
            logger.info("retention removed %d segments", removed)

        # A file that cannot be deleted is retried forever. Counting only what was
        # removed would report a healthy number every pass while the budget is never
        # actually met, so the shortfall is tracked and surfaced in status().
        self._stuck_deletions = len(plan.delete) - removed
        if self._stuck_deletions:
            logger.warning(
                "%d segment(s) could not be deleted; storage budget cannot be met",
                self._stuck_deletions,
            )

    @staticmethod
    def _write_rate(segments) -> float:
        """Bytes per second, measured from what has actually been recorded."""
        if len(segments) < 2:
            return 0.0
        span = segments[-1].end - segments[0].start
        if span <= 0:
            return 0.0
        return sum(s.size_bytes for s in segments) / span


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vmd-record", description="VMD recording service")
    parser.add_argument("--settings", default="settings.json", help="path to settings.json")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between passes")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    settings = load_settings(args.settings)
    # Say which file was used. Without this, "running with defaults" looks identical
    # whether it is a genuine first run or a mistyped path.
    if Path(args.settings).exists():
        logger.info("settings loaded from %s", Path(args.settings).resolve())
    else:
        logger.warning(
            "no settings file at %s; using defaults", Path(args.settings).resolve()
        )
    if not [s for s in settings.camera.streams if s.enabled]:
        print(f"no enabled streams in {args.settings}; nothing to record")
        return 1
    service = RecordingService(settings)
    if args.once:
        service.run_once()
        print(service.status())
        service.stop()
        return 0
    service.run_forever(interval=args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add the console script to `pyproject.toml` under `[project.scripts]` (create the section if it does not exist):

```toml
[project.scripts]
vmd-record = "vmd.record_main:main"
```

Also change `SegmentIndex.__init__` in `vmd/storage/index.py` so a second connection can
coexist. Plan B adds a web server that will read this index while the service writes to it:

```python
    def __init__(self, db_path: str | Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(db_path))
        self._connection.row_factory = sqlite3.Row
        # WAL lets a reader and the writer work at the same time; the busy timeout
        # makes a reader wait for a brief write lock instead of failing immediately
        # with "database is locked". Both matter once the web UI reads this file.
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.executescript(SCHEMA)
        self._connection.commit()
```

**Threading rule, to be stated in the class docstring:** one `SegmentIndex` instance
belongs to one thread. `sqlite3` connections default to `check_same_thread=True` and will
raise if shared. A second consumer must construct its own instance against the same file,
which is exactly what WAL makes safe. Add this to `SegmentIndex`'s docstring:

```python
class SegmentIndex:
    """The record of what exists on disk. Never scans the filesystem.

    One instance belongs to one thread. Another thread or process that needs to read
    the catalogue must open its own instance against the same file; WAL mode makes
    concurrent readers safe alongside the single writer.
    """
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_record_main.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -v`
Expected: PASS — every test in the project.

- [ ] **Step 6: Manual smoke check with real ffmpeg**

```bash
uv run python -c "
from pathlib import Path
from vmd.settings import Settings, StreamSettings, save_settings
s = Settings()
s.camera.streams = [StreamSettings(name='test', url='footage/walk_3mbps.mp4')]
s.storage.root = Path('recordings')
s.storage.segment_seconds = 5
s.storage.budget_gb = 0.05
save_settings(s, 'settings.json')
"
uv run vmd-record --settings settings.json --interval 3
```

Expected: segment files appear under `recordings/test/`, the log reports segments being indexed, and once the 50 MB budget is exceeded the oldest segments are deleted while recording continues. Stop with Ctrl-C.

- [ ] **Step 7: Commit**

```bash
git add vmd/record_main.py tests/test_record_main.py pyproject.toml
git commit -m "feat: add recording service wiring recorders, index and retention"
```

---

### Task 10: Health — stalled streams, orphan segments and log storms

**Files:**
- Modify: `vmd/record_main.py`
- Modify: `vmd/supervisor.py`
- Test: `tests/test_health.py`

Three failure modes that the wiring in Task 9 cannot detect. Two were raised during review
of Tasks 4 and 8; the third was found while smoke-testing Task 9 against real recordings.
All only appear after the system has been running unattended for a while, which is exactly
why they need building deliberately rather than being noticed in the field.

**0. Segments on disk that the index does not know about.** `_index_new_segments` only
looks inside the output directory of a *currently configured* recorder. Rename a stream,
disable one, or change the recordings folder, and the files it already wrote stay on disk
while vanishing from the catalogue — never counted against the budget, never deleted. On a
system whose whole purpose is managing a fixed amount of disk, that is a silent leak. This
was observed for real during the Task 9 smoke check: four files on disk, two in the index.

The fix is to reconcile at startup — walk every subdirectory of `storage.root`, and adopt
any `.mp4` whose path is not already indexed, so the catalogue converges on what the disk
actually holds. Add to `RecordingService.__init__`, after the index is opened:

```python
        self._adopt_orphans()
```

and:

```python
    def _adopt_orphans(self) -> None:
        """Index segments already on disk that no current recorder is responsible for.

        Renaming or disabling a stream leaves its recordings behind. Without this they
        occupy the storage budget forever while being invisible to retention.

        Directories belonging to a currently configured recorder are deliberately
        skipped. Those are handled by _index_new_segments, which uses
        find_closed_segments and therefore never touches the file ffmpeg still has
        open. Sweeping them here would index the in-progress segment and expose a live
        recording to retention.
        """
        owned = {recorder.stream for recorder in self.recorders}
        for directory in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if directory.name in owned:
                continue
            for path in sorted(directory.glob("*.mp4")):
                if str(path) in self._seen:
                    continue
                start = parse_segment_start(path.name)
                if start is None:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size == 0:
                    continue
                self.index.add(
                    stream=directory.name,
                    path=str(path),
                    start=start,
                    end=start + self.settings.storage.segment_seconds,
                    size_bytes=size,
                )
                self._seen.add(str(path))
                logger.info("adopted orphaned segment %s", path.name)
```

Test it:

```python
def test_orphaned_segments_on_disk_are_adopted(tmp_path):
    # A stream that was renamed or disabled leaves recordings behind. They must still
    # be counted and eventually deleted, or they occupy the budget forever.
    settings = build_settings(tmp_path)
    root = tmp_path / "recordings"
    orphan_dir = root / "an_old_stream_name"
    orphan_dir.mkdir(parents=True)
    orphan = orphan_dir / "2026-08-07_09-00-00.mp4"
    orphan.write_bytes(b"x" * 4096)

    service = RecordingService(settings, spawn=spawn_fake)

    indexed = [s.path for s in service.index.all()]
    assert str(orphan) in indexed
    assert service.index.total_bytes() == 4096
    service.stop()
```

**1. A recorder that is alive but producing nothing.** `running` only reports whether the
ffmpeg process exists. If the RTSP socket dies without closing — routine on a long wireless
link — ffmpeg can block on a read forever. `poll()` returns None, `running` stays True, the
supervisor is satisfied, and not one frame is recorded. The supervisor cannot see this,
because from its point of view nothing failed.

The signal that does detect it is segment production. A recorder that has produced no new
segment for more than twice its segment length is stalled regardless of what `poll()` says.
`RecordingService` already watches the output directory, so it is the right owner.

- [ ] **Step 1: Write the failing test**

Create `tests/test_health.py`:

```python
import os

from vmd.record_main import RecordingService
from vmd.settings import Settings, StreamSettings


class FakeProcess:
    def poll(self):
        return None

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def spawn_fake(command, log_path=None):
    return FakeProcess()


def build_settings(tmp_path):
    settings = Settings()
    settings.camera.streams = [StreamSettings(name="thermal", url="rtsp://example/thermal")]
    settings.storage.root = tmp_path / "recordings"
    settings.storage.segment_seconds = 10
    return settings


def write_segment(directory, name, mtime):
    path = directory / name
    path.write_bytes(b"x" * 2048)
    os.utime(path, (mtime, mtime))
    return path


def test_a_stream_producing_segments_is_not_stalled(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=100.0)
    directory = tmp_path / "recordings" / "thermal"
    write_segment(directory, "2026-08-07_10-00-00.mp4", 100.0)
    write_segment(directory, "2026-08-07_10-00-10.mp4", 110.0)
    service.run_once(now=115.0)
    assert service.stalled_streams(now=115.0) == []
    service.stop()


def test_a_stream_with_no_new_segment_is_stalled(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=100.0)
    directory = tmp_path / "recordings" / "thermal"
    write_segment(directory, "2026-08-07_10-00-00.mp4", 100.0)
    write_segment(directory, "2026-08-07_10-00-10.mp4", 110.0)
    service.run_once(now=115.0)
    # Twice the 10-second segment length has passed with nothing new.
    assert service.stalled_streams(now=140.0) == ["thermal"]
    service.stop()


def test_a_stream_is_not_stalled_before_it_has_had_time(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=100.0)
    # No segments yet, but it has only just started - not a stall.
    assert service.stalled_streams(now=105.0) == []
    service.stop()


def test_a_service_with_no_streams_is_not_healthy(tmp_path):
    # `all([])` is True, so an empty stream list would otherwise report healthy while
    # recording nothing at all.
    settings = build_settings(tmp_path)
    settings.camera.streams = []
    service = RecordingService(settings, spawn=spawn_fake)
    assert service.status()["healthy"] is False
    service.stop()


def test_status_reports_a_stalled_stream_as_unhealthy(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=100.0)
    directory = tmp_path / "recordings" / "thermal"
    write_segment(directory, "2026-08-07_10-00-00.mp4", 100.0)
    write_segment(directory, "2026-08-07_10-00-10.mp4", 110.0)
    service.run_once(now=115.0)
    status = service.status(now=140.0)
    assert status["streams"][0]["stalled"] is True
    assert status["healthy"] is False
    service.stop()


def test_repeated_identical_start_failures_are_logged_once(caplog):
    from vmd.supervisor import Managed, Supervisor

    class Broken:
        running = False

        def start(self):
            raise RuntimeError("cannot start")

        def stop(self):
            pass

    clock = {"now": 0.0}
    supervisor = Supervisor(
        [Managed(name="broken", service=Broken())],
        clock=lambda: clock["now"],
        restart_delay=1.0,
    )
    with caplog.at_level("WARNING"):
        for _ in range(50):
            supervisor.tick()
            clock["now"] += 2.0

    tracebacks = [r for r in caplog.records if r.exc_info]
    # A stream that is broken for a month must not write a traceback every two
    # seconds; that alone would fill the disk this system exists to manage.
    assert len(tracebacks) <= 2
    assert supervisor.failures["broken"] == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_health.py -v`
Expected: FAIL — `RecordingService` has no `stalled_streams`, `status()` takes no `now`, and `Supervisor` has no `failures`.

- [ ] **Step 3: Implement stall detection**

In `vmd/record_main.py`, record when each stream last produced a segment. In `__init__`:

```python
        self._last_segment_at: dict[str, float] = {}
        self._started_at: dict[str, float] = {}
```

In `_index_new_segments`, after a segment is successfully added:

```python
                self._last_segment_at[recorder.stream] = now
```

And in `run_once`, before `self.supervisor.tick()`, note when each stream first started so a
freshly started stream is not immediately called stalled:

```python
        for recorder in self.recorders:
            self._started_at.setdefault(recorder.stream, now)
```

Then add:

```python
    def stalled_streams(self, now: float | None = None) -> list[str]:
        """Streams whose process is alive but which have produced nothing recently.

        `running` only says the ffmpeg process exists. On a long wireless link the
        RTSP socket can die without closing, leaving ffmpeg blocked on a read: the
        process is alive, the supervisor is satisfied, and nothing is recorded.
        Segment production is the only signal that distinguishes the two.
        """
        now = time.time() if now is None else now
        limit = 2 * self.settings.storage.segment_seconds
        stalled = []
        for recorder in self.recorders:
            if not recorder.running:
                continue  # already visibly down; the supervisor handles that
            last = self._last_segment_at.get(
                recorder.stream, self._started_at.get(recorder.stream, now)
            )
            if now - last > limit:
                stalled.append(recorder.stream)
        return stalled
```

Change `status` to accept `now` and include the flag:

```python
    def status(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        stalled = set(self.stalled_streams(now))
        ...
        streams = [
            {
                "name": r.stream,
                "running": r.running,
                "stalled": r.stream in stalled,
                "restarts": self.supervisor.restarts.get(r.stream, 0),
                "exit_code": r.exit_code,
            }
            for r in self.recorders
        ]
        ...
            # `all([])` is True, so a service with no streams at all would otherwise
            # report itself healthy while recording nothing. The CLI refuses to start
            # in that state, but status() is about to become a web API and must be
            # trustworthy on its own.
            "healthy": (
                bool(streams)
                and all(s["running"] and not s["stalled"] for s in streams)
                and not self._stuck_deletions
            ),
```

- [ ] **Step 4: Implement log throttling**

In `vmd/supervisor.py`, add a `failures` counter and log the full traceback only for the
first couple of consecutive failures, then a single line:

```python
        self.failures: dict[str, int] = {entry.name: 0 for entry in managed}
```

and in `tick`'s except block:

```python
            except Exception:  # noqa: BLE001 - one bad service must not stop the others
                self.failures[entry.name] += 1
                # A permanently broken stream is retried every couple of seconds for
                # months. Logging a full traceback each time would write hundreds of
                # thousands of them and fill the disk this system exists to manage.
                if self.failures[entry.name] <= 2:
                    logger.exception("failed to start %s", entry.name)
                elif self.failures[entry.name] % 100 == 0:
                    logger.warning(
                        "%s has failed to start %d times",
                        entry.name,
                        self.failures[entry.name],
                    )
                self._next_attempt[entry.name] = now + self._restart_delay
                continue
```

Reset the counter on a successful start, next to the existing restart bookkeeping:

```python
            self.failures[entry.name] = 0
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_health.py -v` — expect 5 passed.
Then `uv run pytest -v` — everything must pass.

- [ ] **Step 6: Commit**

```bash
git add vmd/record_main.py vmd/supervisor.py tests/test_health.py
git commit -m "feat: detect stalled streams and throttle repeated failure logging"
```

---

## Self-review notes

**Spec coverage for this plan's scope.** §7 recording: Tasks 3–5, 9 (5-minute raw segments, timestamped names, gaps on dropout, segment index). §8 storage and retention: Tasks 1, 6, 7, 9 (budget detection with manual fallback, budget cap, independent age and space rules either of which can be disabled, warning before deletion, recording never stops, status reporting). §11 settings: Task 1 (camera, radio, storage, bitrate, target distance — all operator-supplied, nothing hardcoded). §4/§12 supervisor: Task 8 plus the wiring in Task 9.

**Deliberately not in this plan**, recorded so nobody mistakes them for gaps: go2rtc and the live view, the fallback ladder, the web server and UI, PTZ control and click-to-centre, the person-height readout, radio polling, adaptive bitrate, playback and export, and the events table. They are Plans B, C and D.

**Carried-forward decisions worth re-examining in Plan B.** `detect_free_bytes()` exists in Task 1 but nothing calls it yet — the first-run flow that displays detected free space belongs to the settings UI in Plan B, and it is defined here so the UI has it ready. `RecordingService.status()` returns exactly the fields the status bar in §5 needs, so Plan B can render it without changing this module.

**Known limitation.** A segment's `end` is computed as `start + segment_seconds` rather than read from the file, so the last segment before a dropout is recorded as slightly longer than it really is. Correcting it means running `ffprobe` on every segment, which costs a process spawn per five minutes of footage. If the playback timeline in Plan D shows visible drift at gap boundaries, that is the cause and the fix.
