"""What the console knows about the disk, and how it says it.

Two questions live here, and they are deliberately answered by one reading of
the folder: how full the disk and the budget are, and whether footage is
actually arriving. Both are filesystem questions, both are asked on a timer, and
neither may be asked on the heartbeat or on the GUI thread.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from vmd.desktop.disk import (
    POLL_SECONDS,
    DiskReading,
    DiskWatcher,
    StoragePanel,
    read_disk,
    storage_lines,
)
from vmd.desktop.style import PALETTE
from vmd.settings import (
    CameraSettings,
    Settings,
    StorageSettings,
    StreamSettings,
)

GB = 1024**3
MB = 1024**2


def settings_for(root: Path, budget_gb: float = 100.0, **storage) -> Settings:
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(name="thermal", url="rtsp://10.0.0.2/t", enabled=True)
            ],
        ),
        storage=StorageSettings(root=root, budget_gb=budget_gb, **storage),
    )


def write_segment(root: Path, stream: str, name: str, size: int, mtime: float) -> Path:
    directory = root / stream
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"\0" * size)
    os.utime(path, (mtime, mtime))
    return path


# ------------------------------------------------------------ reading the disk


def test_a_recordings_folder_that_is_not_there_is_a_sentence_not_an_exception(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path / "nowhere")
    reading = read_disk(settings, now=1000.0)
    assert reading.problem, "a missing folder must be reported in words"
    assert str(tmp_path / "nowhere") in reading.problem
    assert reading.writing is False


def test_a_storage_root_on_a_drive_that_is_not_mounted_is_a_sentence(tmp_path: Path) -> None:
    """A drive letter with nothing behind it is the ordinary field failure."""
    settings = settings_for(Path("Q:/not-a-drive/vmd"))
    reading = read_disk(settings, now=1000.0)
    assert reading.problem
    assert reading.free_bytes is None
    assert reading.writing is False


def test_the_free_space_and_what_the_archive_holds_are_both_measured(tmp_path: Path) -> None:
    write_segment(tmp_path, "thermal", "2026-08-11_10-00-00.mp4", 5 * MB, time.time())
    write_segment(tmp_path, "thermal", "2026-08-11_10-05-00.mp4", 3 * MB, time.time())
    reading = read_disk(settings_for(tmp_path), now=time.time())
    assert reading.problem is None
    assert reading.free_bytes is not None and reading.free_bytes > 0
    assert reading.used_bytes == 8 * MB


def test_footage_from_a_stream_nobody_records_any_more_still_counts(tmp_path: Path) -> None:
    """Renaming a stream leaves its recordings behind, and they still fill the
    disk. A figure that only counted the configured streams would say the budget
    was fine while the drive filled."""
    write_segment(tmp_path, "old-name", "2026-08-11_10-00-00.mp4", 4 * MB, time.time())
    reading = read_disk(settings_for(tmp_path), now=time.time())
    assert reading.used_bytes == 4 * MB


def test_the_growth_rate_is_measured_from_what_was_actually_written(tmp_path: Path) -> None:
    now = 1_000_000.0
    # Three closed segments, ten minutes apart, 60 MB each. The first was
    # already on disk when the window opened, so the measured span carries the
    # two written inside it: 120 MB over 1200 s.
    write_segment(tmp_path, "thermal", "2026-08-11_10-00-00.mp4", 60 * MB, now - 1800)
    write_segment(tmp_path, "thermal", "2026-08-11_10-10-00.mp4", 60 * MB, now - 1200)
    write_segment(tmp_path, "thermal", "2026-08-11_10-20-00.mp4", 60 * MB, now - 600)
    reading = read_disk(settings_for(tmp_path), now=now)
    assert reading.rate_is_estimate is False
    assert reading.bytes_per_second == (120 * MB) / 1200.0


def test_with_nothing_written_yet_the_rate_is_an_estimate_and_says_so(tmp_path: Path) -> None:
    (tmp_path / "thermal").mkdir()
    reading = read_disk(settings_for(tmp_path), now=1000.0)
    assert reading.rate_is_estimate is True
    assert reading.bytes_per_second > 0, "an estimate is still a number"


# ------------------------------------------------- is footage reaching the disk


def test_a_folder_with_no_footage_at_all_reads_as_not_writing(tmp_path: Path) -> None:
    reading = read_disk(settings_for(tmp_path), now=time.time())
    assert reading.writing is False
    assert reading.write_problem
    assert "nothing" in reading.write_problem.lower()


def test_footage_arriving_now_reads_as_writing(tmp_path: Path) -> None:
    now = time.time()
    write_segment(tmp_path, "thermal", "2026-08-11_10-00-00.mp4", 4 * MB, now - 5)
    reading = read_disk(settings_for(tmp_path), now=now)
    assert reading.writing is True
    assert reading.write_problem is None


def test_footage_that_stopped_arriving_reads_as_not_writing(tmp_path: Path) -> None:
    """The recorder's own rule: two segment lengths with nothing written is a
    stalled stream, whatever the process table says."""
    now = time.time()
    settings = settings_for(tmp_path)
    stale = now - 3 * settings.storage.segment_seconds
    write_segment(tmp_path, "thermal", "2026-08-11_10-00-00.mp4", 4 * MB, stale)
    reading = read_disk(settings, now=now)
    assert reading.writing is False
    assert reading.write_problem


# ----------------------------------------------------------- what it looks like


def reading_with(**kwargs) -> DiskReading:
    base = dict(
        at=1000.0,
        free_bytes=500 * GB,
        used_bytes=10 * GB,
        bytes_per_second=1 * MB,
        rate_is_estimate=False,
        newest_write=1000.0,
        writing=True,
        write_problem=None,
        problem=None,
    )
    base.update(kwargs)
    return DiskReading(**base)


def coloured(lines: list[tuple[str, str]], colour: str) -> list[str]:
    return [text for text, own in lines if own == colour]


def test_a_budget_nearly_used_is_warned_about_before_it_is_full(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, budget_gb=100.0)
    reading = reading_with(used_bytes=int(0.95 * 100 * GB))
    lines = storage_lines(reading, settings.storage)
    warned = coloured(lines, PALETTE["warn"])
    assert warned, "warn_at_fraction is 0.9 and the budget is 95% used"
    assert any("budget" in text.lower() for text in warned)
    assert not coloured(lines, PALETTE["alarm"])


def test_a_budget_well_inside_its_limit_is_not_shouted_about(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, budget_gb=100.0)
    lines = storage_lines(reading_with(used_bytes=10 * GB), settings.storage)
    assert not coloured(lines, PALETTE["warn"])
    assert not coloured(lines, PALETTE["alarm"])


def test_a_budget_that_is_full_reads_as_an_alarm_and_says_footage_is_going(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path, budget_gb=100.0)
    lines = storage_lines(reading_with(used_bytes=101 * GB), settings.storage)
    alarmed = coloured(lines, PALETTE["alarm"])
    assert alarmed
    assert any("deleted" in text.lower() for text in alarmed + [t for t, _ in lines])


def test_a_drive_that_will_fill_before_the_budget_says_so_in_its_own_words(
    tmp_path: Path,
) -> None:
    """A different problem with a different fix, and retention will not save it:
    footage is only deleted when the budget is exceeded, and the budget will
    never be reached."""
    settings = settings_for(tmp_path, budget_gb=100.0)
    # The budget still wants 80 GB and the drive has 5 GB: retention will never
    # fire, because the budget it enforces is never exceeded.
    lines = storage_lines(
        reading_with(used_bytes=20 * GB, free_bytes=5 * GB), settings.storage
    )
    explanation = [
        text
        for text, _ in lines
        if "run out before the budget" in text.lower()
    ]
    assert explanation, "the two limits must not be reported as one"
    assert "only deleted when the budget is exceeded" in explanation[0]
    assert all(
        colour == PALETTE["alarm"]
        for text, colour in lines
        if "run out before the budget" in text.lower()
    )


def test_a_drive_down_to_its_last_margin_is_an_alarm(tmp_path: Path) -> None:
    """warn_at_fraction is 0.9, so the last 10 GB of a 100 GB budget is the
    margin the budget itself refuses to spend quietly. The drive gets the same
    margin, because it is the same last slice of room."""
    settings = settings_for(tmp_path, budget_gb=100.0)
    lines = storage_lines(
        reading_with(used_bytes=99 * GB, free_bytes=2 * GB), settings.storage
    )
    drive = [(t, c) for t, c in lines if t.startswith("Drive:")]
    assert drive and drive[0][1] == PALETTE["alarm"]


def test_a_drive_with_room_for_the_budget_but_not_much_more_is_a_warning(
    tmp_path: Path,
) -> None:
    """Above the margin, but not far enough above it to hold the rest of the
    budget and the margin together. Not yet an alarm, and not silence either."""
    settings = settings_for(tmp_path, budget_gb=100.0)
    lines = storage_lines(
        reading_with(used_bytes=50 * GB, free_bytes=55 * GB), settings.storage
    )
    drive = [(t, c) for t, c in lines if t.startswith("Drive:")]
    assert drive and drive[0][1] == PALETTE["warn"], drive


def test_a_drive_with_plenty_of_room_is_drawn_in_the_ordinary_colour(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path, budget_gb=100.0)
    lines = storage_lines(
        reading_with(used_bytes=10 * GB, free_bytes=800 * GB), settings.storage
    )
    drive = [(t, c) for t, c in lines if t.startswith("Drive:")]
    assert drive and drive[0][1] == PALETTE["ink"]


def test_the_time_left_is_stated_in_hours_not_only_a_percentage(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, budget_gb=100.0)
    # 1 MB/s, 10 GB of headroom left in the budget: about 2.8 hours.
    lines = storage_lines(
        reading_with(used_bytes=90 * GB, bytes_per_second=1 * MB), settings.storage
    )
    text = " ".join(t.lower() for t, _ in lines)
    assert "hour" in text, "a percentage is not something an operator can act on"


def test_an_estimated_rate_is_never_presented_as_a_measurement(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    lines = storage_lines(reading_with(rate_is_estimate=True), settings.storage)
    text = " ".join(t.lower() for t, _ in lines)
    assert "estimate" in text or "roughly" in text


def test_a_folder_that_cannot_be_read_is_one_plain_sentence(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    lines = storage_lines(
        reading_with(problem="The recordings folder Q:\\vmd is not there."), settings.storage
    )
    assert [t for t, _ in lines] == ["The recordings folder Q:\\vmd is not there."]
    assert lines[0][1] == PALETTE["alarm"]


def test_with_no_budget_set_the_drive_is_the_only_limit(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, budget_enabled=False)
    lines = storage_lines(reading_with(used_bytes=200 * GB), settings.storage)
    text = " ".join(t.lower() for t, _ in lines)
    assert "never deleted" in text or "no budget" in text


# ------------------------------------------------------------------ the polling


class Executor:
    """A worker that runs when told to, so a test never waits on a thread."""

    def __init__(self) -> None:
        self.jobs: list = []

    def __call__(self, work) -> None:
        self.jobs.append(work)

    def drain(self) -> None:
        while self.jobs:
            self.jobs.pop(0)()


def test_the_folder_is_not_read_on_every_heartbeat(tmp_path: Path) -> None:
    """Two seconds is the heartbeat. Reading free space that often is a
    filesystem call thirty times a minute for months."""
    executor = Executor()
    clock = [1000.0]
    watcher = DiskWatcher(
        settings_for(tmp_path), executor=executor, clock=lambda: clock[0]
    )
    taken = 0
    for _ in range(10):
        watcher.poll()
        taken += len(executor.jobs)
        executor.drain()  # so the in-flight guard is never what is being tested
        clock[0] += 2.0
    assert taken == 1, f"one reading in 20 s of heartbeats, not {taken}"

    clock[0] += POLL_SECONDS
    watcher.poll()
    assert len(executor.jobs) == 1, "and one more once the interval has passed"


def test_the_reading_never_happens_on_the_calling_thread(tmp_path: Path) -> None:
    """The caller is the GUI thread on its 2 s timer. A disconnected drive can
    block a stat call for many seconds."""
    executor = Executor()
    watcher = DiskWatcher(settings_for(tmp_path), executor=executor, clock=lambda: 1000.0)
    watcher.poll()
    assert watcher.reading is None, "poll() must not have done the work itself"
    executor.drain()
    assert watcher.reading is not None


def test_a_reading_that_throws_leaves_a_sentence_rather_than_no_panel(tmp_path: Path) -> None:
    def explode(settings, now):
        raise OSError("the drive went away mid-read")

    executor = Executor()
    watcher = DiskWatcher(
        settings_for(tmp_path), executor=executor, clock=lambda: 1000.0, read=explode
    )
    watcher.poll()
    executor.drain()
    assert watcher.reading is not None
    assert watcher.reading.problem


def test_a_saved_folder_is_read_immediately_rather_than_after_the_interval(
    tmp_path: Path,
) -> None:
    executor = Executor()
    watcher = DiskWatcher(settings_for(tmp_path), executor=executor, clock=lambda: 1000.0)
    watcher.poll()
    executor.drain()
    watcher.apply(settings_for(tmp_path / "elsewhere"))
    watcher.poll()
    assert executor.jobs, "a new folder is a new question, not one to wait 30 s for"


def test_only_one_reading_is_in_flight_at_a_time(tmp_path: Path) -> None:
    """A stat that blocks for a minute on a dead drive must not queue a
    thread per heartbeat behind it."""
    executor = Executor()
    clock = [1000.0]
    watcher = DiskWatcher(
        settings_for(tmp_path), executor=executor, clock=lambda: clock[0]
    )
    watcher.poll()
    clock[0] += 10 * POLL_SECONDS
    watcher.poll()
    assert len(executor.jobs) == 1


# -------------------------------------------------------------------- the panel


def test_the_panel_shows_the_storage_lines(qtbot, tmp_path: Path) -> None:
    executor = Executor()
    watcher = DiskWatcher(
        settings_for(tmp_path), executor=executor, clock=lambda: 1000.0
    )
    panel = StoragePanel(watcher)
    qtbot.addWidget(panel)
    watcher.poll()
    executor.drain()
    panel.refresh()
    assert panel.lines(), "the storage panel must say something"


def test_the_panel_says_something_before_the_first_reading(qtbot, tmp_path: Path) -> None:
    watcher = DiskWatcher(settings_for(tmp_path), executor=Executor(), clock=lambda: 1000.0)
    panel = StoragePanel(watcher)
    qtbot.addWidget(panel)
    panel.refresh()
    assert panel.lines(), "a blank panel is the failure this exists to remove"
