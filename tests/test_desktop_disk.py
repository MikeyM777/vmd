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

import pytest

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


def watcher_showing(reading: DiskReading, tmp_path: Path, **settings) -> DiskWatcher:
    """A watcher whose answer is already this reading, with no thread involved."""
    executor = Executor()
    watcher = DiskWatcher(
        settings_for(tmp_path, **settings),
        executor=executor,
        clock=lambda: 1000.0,
        read=lambda _settings, _now: reading,
    )
    watcher.poll()
    executor.drain()
    return watcher


# The longest sentence this panel can produce, and the reading that produces it:
# the budget still wants 80 GB, the drive has 5 GB, so retention will never fire.
# 159 characters, which is four wrapped lines in the 340 px column, and the panel
# needs 174 px to draw all five of its sentences. The other two are the states
# whose sentences also run past one line.
SQUEEZED = {
    "the drive will run out before the budget": dict(used_bytes=20 * GB, free_bytes=5 * GB),
    "the budget is full": dict(used_bytes=101 * GB, free_bytes=400 * GB),
    "the folder cannot be read": dict(
        problem="The recordings folder D:\\vmd\\recordings cannot be reached: "
        "[WinError 21] The device is not ready."
    ),
}


@pytest.mark.parametrize("state", sorted(SQUEEZED))
def test_no_sentence_is_cut_in_half_when_the_column_is_short_of_room(
    qtbot, tmp_path: Path, state: str
) -> None:
    """The side column carries five boxes and on a laptop screen that is more
    than fits. A Qt layout short of room does not shrink a word-wrapped sentence
    to make it fit: it hands each box the least height that box says it can live
    with, and draws the next line over the tail of the last one. So the least
    this panel says it can live with has to be the height its sentences actually
    need - otherwise the sentence that gets cut in half is the longest one, and
    the longest one is the warning that the drive will run out before the budget
    is reached, which is the case where recording stops while every retention
    rule reports itself content.
    """
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    watcher = watcher_showing(reading_with(**SQUEEZED[state]), tmp_path)
    column = QWidget()
    qtbot.addWidget(column)
    column.setFixedWidth(340)  # the width the Live tab gives its side column
    layout = QVBoxLayout(column)
    panel = StoragePanel(watcher)
    layout.addWidget(panel)
    # The movement list below it, which takes every pixel it is allowed to: that
    # is what leaves the panel with no more than the height it asked for.
    layout.addWidget(QWidget(), 1)
    column.resize(340, 860)
    column.show()
    QApplication.processEvents()
    assert panel.lines(), "the panel must be saying something to be cut in half"

    # Now the squeeze: the column is short of room, so the panel gets the least
    # height it said it could live with. Nothing about that may cost a sentence.
    panel.setFixedHeight(panel.minimumSizeHint().height())
    QApplication.processEvents()
    assert panel.clipped() == [], "half a sentence is worse than none"


# ------------------------------------------- what counts as footage, and when
#
# Both of these are the same failure as the one that started this review: the
# guard was fitted to the one incident that had already happened rather than to
# what a real recording looks like, and the state it lets through is the state
# where the console says "recording" and nothing is being written.


def test_a_file_with_a_header_and_no_video_in_it_is_not_footage(tmp_path: Path) -> None:
    """The guard was `size > 0`, because the failure everybody had seen wrote
    ZERO-byte files. An ffmpeg that writes the header and then dies writes a few
    hundred bytes and went straight through."""
    from vmd.desktop.disk import SMALLEST_SEGMENT_BYTES

    now = time.time()
    for n in range(20):
        write_segment(
            tmp_path, "thermal", f"2026-08-11_10-{n:02d}-00.mp4", 900, now - 10
        )
    reading = read_disk(settings_for(tmp_path), now=now)

    assert reading.writing is False, "a folder of headers was reported as footage"
    assert reading.used_bytes == 0
    assert reading.newest_write is None
    assert "nothing has ever been written" in (reading.write_problem or "")

    # And a real segment is still a real segment.
    write_segment(
        tmp_path, "thermal", "2026-08-11_11-00-00.mp4", SMALLEST_SEGMENT_BYTES, now - 10
    )
    assert read_disk(settings_for(tmp_path), now=now).writing is True


def test_a_clock_stepped_backwards_does_not_make_a_dead_recorder_look_alive(
    tmp_path: Path
) -> None:
    """This laptop is offline and its date is typed by a person. A step back an
    hour made every file on the disk look as though it had just been written."""
    now = time.time()
    write_segment(tmp_path, "thermal", "2026-08-11_10-00-00.mp4", 4 * MB, now)

    for stepped_back in (3600.0, 86400.0, 365 * 86400.0):
        reading = read_disk(settings_for(tmp_path), now=now - stepped_back)
        assert reading.writing is False, (
            f"a clock {stepped_back:.0f} s behind the newest file read as recording"
        )
        assert "date on this machine is wrong" in (reading.write_problem or "")


def test_a_file_a_moment_ahead_of_the_clock_is_still_footage(tmp_path: Path) -> None:
    """A file written while the folder was being read is a second or two ahead
    of the moment the reading started. That is not a clock that moved."""
    now = time.time()
    write_segment(tmp_path, "thermal", "2026-08-11_10-00-00.mp4", 4 * MB, now + 2.0)
    assert read_disk(settings_for(tmp_path), now=now).writing is True


def test_the_last_hour_of_footage_is_not_reported_as_1_minutes(tmp_path: Path) -> None:
    """The plural is wrong exactly once, and it is in the state that matters.

    "Roughly 1 minutes left before the drive is full" is the sentence this panel
    exists to produce, on the morning it produces it, and it is the one sentence
    in it that reads as though nobody has ever seen it. The same arithmetic says
    "1 hours" for the hour before that.
    """
    settings = settings_for(tmp_path, budget_gb=100.0)
    # 30 MB left at 1 MB/s: half a minute, which the panel floors to one.
    lines = storage_lines(
        reading_with(
            used_bytes=100 * GB - 30 * MB,
            free_bytes=30 * MB,
            bytes_per_second=float(MB),
            rate_is_estimate=False,
        ),
        settings.storage,
    )
    said = " ".join(text for text, _colour in lines)
    assert "1 minutes" not in said, said
    assert "1 minute " in said or said.endswith("1 minute"), said

    # And an hour and a bit of it, which takes the same road.
    lines = storage_lines(
        reading_with(
            used_bytes=100 * GB - 4 * GB,
            free_bytes=4 * GB,
            bytes_per_second=4 * GB / 3900.0,
            rate_is_estimate=False,
        ),
        settings.storage,
    )
    said = " ".join(text for text, _colour in lines)
    assert "1 hours" not in said, said
