"""How full the disk is, how fast it is filling, and whether it is filling at all.

Three of the failure states the design calls first-class converge on one reading
of the recordings folder:

* **The disk is filling.** Said before it is full, not after, because after is
  too late to do anything about.
* **The budget is nearly used.** A different problem with a different fix.
  Retention deletes the oldest footage when the *budget* is exceeded and never
  because the *drive* is short of room, so a drive that fills before the budget
  is reached stops recording while every retention rule reports itself content.
* **Nothing is being recorded.** "Recording" in the status line used to mean a
  process was alive when the console last looked. With the recordings folder
  pointed at a drive that does not exist, that read "recording" in half the
  samples taken over forty seconds while not one byte was ever written. The only
  honest answer comes from the folder: footage is reaching the disk, or it is
  not.

Every question here touches the filesystem, and the filesystem is exactly what
is broken in the cases that matter - a disconnected drive can leave a stat call
blocked for many seconds. So none of it runs on the GUI thread and none of it
runs on the two-second heartbeat: `DiskWatcher` reads at most once every
`POLL_SECONDS` and always on a worker, and the window only ever reads the answer
it left behind.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QGroupBox, QLabel, QSizePolicy, QVBoxLayout, QWidget

from vmd.desktop.style import PALETTE
from vmd.settings import Settings, StorageSettings, detect_free_bytes

logger = logging.getLogger(__name__)

# How often the folder is read.
#
# Thirty seconds, and the number is a trade between two costs, one of them
# measured on this machine: one `read_disk` over a recordings tree holding a
# full 100 GB budget - 1,360 segment files across two streams - takes a median
# of 13.8 ms (min 10.5, max 20.9 over 20 runs) with `os.scandir`, because on
# Windows the size and mtime come back in the directory entry itself and cost no
# extra call. Once every 30 s that is 0.05% of one core. That is cheap, but it
# is not free, and it is a filesystem call, and the filesystem is the thing that
# is broken in every case this exists to report. On the heartbeat it would run
# 30 times a minute, 43,200 times a day, for months. The things it reports - a
# budget filling, a drive filling, footage that stopped arriving - all move over
# hours. Thirty seconds is far faster than anything it has to notice and a
# fifteenth of the work the heartbeat would have done.
POLL_SECONDS = 30.0

# How far back the growth rate is measured over. Long enough that a quiet ten
# minutes does not read as "the disk has stopped filling", short enough that a
# rate measured today is about today.
RATE_WINDOW_SECONDS = 6 * 3600.0

# The shortest gap in writing that counts as "nothing is being recorded",
# whatever the segment length is set to. The rule is the recorder's own: a
# stream that has produced nothing for two segment lengths is stalled. The floor
# stops a very short segment length from making this jumpy.
WRITE_STALE_FLOOR_SECONDS = 120.0

SEGMENT_SUFFIX = ".mp4"

HOUR = 3600.0
DAY = 86400.0


@dataclass(frozen=True)
class DiskReading:
    """One look at the recordings folder. Never a live view of anything."""

    at: float
    free_bytes: int | None
    used_bytes: int | None
    bytes_per_second: float
    rate_is_estimate: bool
    newest_write: float | None
    writing: bool
    write_problem: str | None
    problem: str | None


def read_disk(settings: Settings, now: float | None = None) -> DiskReading:
    """Read the recordings folder. Runs on a worker thread; never raises.

    Everything it can fail at - a folder that is not there, a drive letter with
    nothing behind it, a folder it may not list - comes back as a sentence in
    `problem`, because the operator has this window and nothing else, and a
    traceback in a log file on a machine with no terminal is the same as silence.
    """
    now = time.time() if now is None else now
    root = Path(settings.storage.root)

    try:
        readable = root.is_dir()
    except OSError as exc:  # a drive letter with nothing behind it
        return _unusable(now, f"The recordings folder {root} cannot be reached: {exc}.")
    if not readable:
        return _unusable(now, f"The recordings folder {root} is not there.")

    try:
        files = _segment_files(root)
    except OSError as exc:
        return _unusable(now, f"The recordings folder {root} cannot be read: {exc}.")

    free = detect_free_bytes(root)
    used = sum(size for _mtime, size in files)
    newest = max((mtime for mtime, _size in files), default=None)

    rate, estimated = _growth_rate(files, now, settings)
    writing, write_problem = _is_writing(newest, now, root, settings)

    return DiskReading(
        at=now,
        free_bytes=free,
        used_bytes=used,
        bytes_per_second=rate,
        rate_is_estimate=estimated,
        newest_write=newest,
        writing=writing,
        write_problem=write_problem,
        problem=None,
    )


def _unusable(now: float, problem: str) -> DiskReading:
    return DiskReading(
        at=now,
        free_bytes=None,
        used_bytes=None,
        bytes_per_second=0.0,
        rate_is_estimate=True,
        newest_write=None,
        writing=False,
        write_problem=problem,
        problem=problem,
    )


def _segment_files(root: Path) -> list[tuple[float, int]]:
    """Every recorded segment under the root, as (mtime, size).

    One level of subdirectories, which is how the recorder lays it out: one
    folder per stream. Folders belonging to a stream nobody records any more are
    counted too - renaming a stream leaves its recordings behind, and they still
    occupy the drive and still count against the budget, which is exactly why
    retention adopts them.

    `os.scandir` rather than `glob` and `stat`: on Windows the size and the
    modification time come back in the directory entry that the listing already
    read, so a thousand files cost one directory walk instead of a thousand
    opens.
    """
    files: list[tuple[float, int]] = []
    with os.scandir(root) as entries:
        directories = [entry.path for entry in entries if entry.is_dir()]
    for directory in directories:
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not entry.name.endswith(SEGMENT_SUFFIX):
                        continue
                    try:
                        stat = entry.stat()
                    except OSError:
                        continue  # deleted by retention between the listing and here
                    if stat.st_size <= 0:
                        continue
                    files.append((stat.st_mtime, int(stat.st_size)))
        except OSError:
            continue  # one unreadable stream folder must not lose the others
    return files


def _growth_rate(
    files: list[tuple[float, int]], now: float, settings: Settings
) -> tuple[float, bool]:
    """Bytes per second, measured where possible. Returns (rate, is_estimate).

    Measured from the files themselves. A segment finished at time `m` was
    written during the interval that ended at `m`, so over the span between the
    oldest and the newest file in the window the recorder wrote every byte
    except the oldest file's - which was already on disk when the span began.
    That is a measurement of what the archive really does, and unlike a
    difference between two readings it is not confused by retention deleting
    from the other end.
    """
    recent = sorted((mtime, size) for mtime, size in files if now - mtime <= RATE_WINDOW_SECONDS)
    if len(recent) >= 2:
        span = recent[-1][0] - recent[0][0]
        if span > 0:
            written = sum(size for _mtime, size in recent) - recent[0][1]
            if written > 0:
                return written / span, False
    return _estimated_rate(settings), True


def _estimated_rate(settings: Settings) -> float:
    """A guess, and labelled as one wherever it is shown.

    This is an ESTIMATE and not a measurement, and it is used only when nothing
    has been written recently enough to measure - a console opened on a machine
    that has just been set up, or one whose recorder has been down longer than
    the measurement window. The number comes from the link ceiling, which is the
    bitrate the camera has been asked to fit into, times the number of streams
    being recorded. The real figure differs from it by however much the encoder
    undershoots on a quiet scene, which on a thermal head watching a still
    perimeter is a great deal - so it is deliberately the pessimistic end, and
    the words on screen say "estimate" rather than stating an hour.
    """
    streams = sum(1 for s in settings.camera.streams if s.enabled and s.url) or 1
    return max(settings.bitrate.ceiling_kbps, 1) * 1000.0 / 8.0 * streams


def _is_writing(
    newest: float | None, now: float, root: Path, settings: Settings
) -> tuple[bool, str | None]:
    """Is footage actually reaching the disk?

    Not "is a process alive". A recorder pointed at a folder it cannot write
    lives for a second or two, dies, and is restarted, forever; every one of
    those instants reads as a running process and none of them records anything.
    """
    if newest is None:
        return False, f"nothing has ever been written to {root}"
    limit = max(WRITE_STALE_FLOOR_SECONDS, 2.0 * settings.storage.segment_seconds)
    # A file dated in the future is a clock that was set by hand, not footage
    # from next week; treat it as recent rather than as a fault.
    silence = now - newest
    if silence <= limit:
        return True, None
    return False, f"nothing has been written for {_duration(silence)}"


# --------------------------------------------------------------- what it reads


def storage_lines(
    reading: DiskReading | None, storage: StorageSettings
) -> list[tuple[str, str]]:
    """The storage panel, as (sentence, colour) pairs. Pure, and tested as such.

    Two limits, kept apart on purpose. The budget is the one retention enforces:
    exceed it and the oldest footage is deleted so recording continues. The drive
    is the one nothing enforces: run out of room there and recording simply
    stops, with every retention rule reporting itself satisfied, because the
    budget it was given was never reached.
    """
    if reading is None:
        return [("Reading the recordings folder...", PALETTE["muted"])]
    if reading.problem:
        return [(reading.problem, PALETTE["alarm"])]

    lines: list[tuple[str, str]] = []
    used = reading.used_bytes or 0
    free = reading.free_bytes
    budget = storage.budget_bytes
    rate = reading.bytes_per_second

    # --- the budget ------------------------------------------------------
    if storage.budget_enabled:
        fraction = used / budget if budget else 1.0
        colour = PALETTE["ink"]
        if fraction >= 1.0:
            colour = PALETTE["alarm"]
        elif fraction >= storage.warn_at_fraction:
            colour = PALETTE["warn"]
        lines.append(
            (
                f"Budget: {_bytes(used)} of {_bytes(budget)} used ({fraction * 100:.0f}%)",
                colour,
            )
        )
        headroom = budget - used
        if headroom <= 0:
            lines.append(
                (
                    "The budget is full: the oldest footage is being deleted to "
                    "keep recording.",
                    PALETTE["alarm"],
                )
            )
        else:
            lines.append(
                (
                    f"{_left(headroom, rate, reading.rate_is_estimate)} before the "
                    f"oldest footage starts being deleted.",
                    colour,
                )
            )
    else:
        lines.append(
            (
                f"Budget: off - {_bytes(used)} recorded and never deleted to make room.",
                PALETTE["ink"],
            )
        )

    # --- the drive -------------------------------------------------------
    if free is None:
        lines.append(
            ("The free space on this drive could not be read.", PALETTE["warn"])
        )
        return lines

    # The margin the budget itself uses, applied to the drive: the last slice of
    # the budget that `warn_at_fraction` refuses to spend quietly.
    margin = max(1.0 - storage.warn_at_fraction, 0.0) * budget
    still_wanted = max(budget - used, 0) if storage.budget_enabled else 0
    colour = PALETTE["ink"]
    if free < margin:
        colour = PALETTE["alarm"]
    elif free < still_wanted + margin:
        colour = PALETTE["warn"]
    lines.append((f"Drive: {_bytes(free)} free", colour))
    lines.append(
        (f"{_left(free, rate, reading.rate_is_estimate)} before the drive is full.", colour)
    )
    if storage.budget_enabled and free < still_wanted:
        lines.append(
            (
                "The drive will run out before the budget is reached, and footage "
                "is only deleted when the budget is exceeded - so lower the budget "
                "or free space on this drive.",
                PALETTE["alarm"],
            )
        )
    return lines


def _left(headroom: float, rate: float, estimated: bool) -> str:
    if rate <= 0:
        return "No idea how long"
    seconds = headroom / rate
    return ("Roughly " if estimated else "About ") + _duration(seconds) + " left"


def _duration(seconds: float) -> str:
    if seconds < HOUR:
        return f"{max(seconds / 60.0, 1):.0f} minutes"
    if seconds < 2 * DAY:
        return f"{seconds / HOUR:.0f} hours"
    return f"{seconds / DAY:.0f} days"


def _bytes(count: float) -> str:
    if count >= 1024**4:
        return f"{count / 1024**4:.1f} TB"
    if count >= 1024**3:
        return f"{count / 1024**3:.1f} GB"
    if count >= 1024**2:
        return f"{count / 1024**2:.0f} MB"
    return f"{count / 1024:.0f} KB"


# ------------------------------------------------------------------- the timer


class DiskWatcher:
    """Reads the folder on a worker, at most once every `POLL_SECONDS`.

    Not a QObject and not tied to a thread pool, so it can be built and driven
    by `ConsoleServices`, which has no window and no event loop, and driven
    synchronously by a test that must never wait on a thread.
    """

    def __init__(
        self,
        settings: Settings,
        executor: Callable[[Callable[[], None]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], float] = time.time,
        read: Callable[[Settings, float], DiskReading] | None = None,
    ) -> None:
        self.settings = settings
        self._executor = executor or _daemon_thread
        self._clock = clock
        self._now = now
        self._read = read or (lambda settings, now: read_disk(settings, now=now))
        self._lock = threading.Lock()
        self._reading: DiskReading | None = None
        self._last_started: float | None = None
        self._in_flight = False

    @property
    def reading(self) -> DiskReading | None:
        with self._lock:
            return self._reading

    def apply(self, settings: Settings) -> None:
        """Take the settings the operator just saved, and ask again at once.

        A saved folder is a new question. Waiting out the rest of the interval
        would leave the panel describing the folder that was replaced.
        """
        with self._lock:
            self.settings = settings
            self._last_started = None

    def poll(self) -> None:
        """Called from the heartbeat. Starts a reading if one is due."""
        with self._lock:
            if self._in_flight:
                # A stat blocked on a dead drive must not queue one worker per
                # heartbeat behind it.
                return
            now = self._clock()
            if self._last_started is not None and now - self._last_started < POLL_SECONDS:
                return
            self._last_started = now
            self._in_flight = True
            settings = self.settings

        def work() -> None:
            try:
                reading = self._read(settings, self._now())
            except Exception as exc:  # noqa: BLE001 - a panel beats a dead thread
                logger.exception("the recordings folder could not be read")
                reading = _unusable(
                    self._now(), f"The recordings folder could not be read: {exc}."
                )
            with self._lock:
                self._reading = reading
                self._in_flight = False

        self._executor(work)


def _daemon_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="vmd-disk", daemon=True).start()


class StoragePanel(QGroupBox):
    """The storage lines in the Live tab's right column.

    It reads whatever the watcher last left behind and never asks the
    filesystem anything itself.
    """

    def __init__(self, watcher: DiskWatcher, parent: QWidget | None = None) -> None:
        super().__init__("Storage", parent)
        self._watcher = watcher
        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(2)
        # The panel asks for as much height as its wrapped sentences need at the
        # width the column gives it. Without this the least it says it can live
        # with is one line per sentence, and a column short of room takes it at
        # its word: the second line of each sentence is drawn over the line
        # beneath it. Here that cuts the sentence saying the drive will run out
        # before the budget is reached - the case where recording stops while
        # every retention rule reports itself content. The link panel beside this
        # one is built the same way, for the same reason.
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
        self.setSizePolicy(policy)
        self._labels: list[QLabel] = []
        self._shown: list[tuple[str, str]] = []
        self.refresh()

    def lines(self) -> list[tuple[str, str]]:
        """What is on screen, for the window and for the tests."""
        return list(self._shown)

    def clipped(self) -> list[str]:
        """Any sentence the panel is not tall enough to show in full.

        Word wrapping is the one way this panel can lose half a sentence without
        anything going wrong: a QLabel's own idea of how tall it should be is a
        guess, and where the guess is short the layout draws the rest of the
        sentence over the line beneath it. The sentence that would be cut is the
        longest one, and the longest one is the warning that the drive will run
        out before the budget is reached.
        """
        cut: list[str] = []
        room = self._layout.contentsRect()
        shown = [label for label in self._labels if label.isVisibleTo(self) and label.text()]
        for index, label in enumerate(shown):
            # Three ways to lose a line: the label is too short for its own
            # wrapped text; it runs off the bottom of the panel; or the panel was
            # given less height than it asked for and the layout has laid the
            # next sentence over the tail of this one.
            box = label.geometry()
            after = shown[index + 1].geometry() if index + 1 < len(shown) else None
            if (
                label.height() < label.heightForWidth(max(label.width(), 1))
                or box.bottom() > room.bottom() + 1
                or (after is not None and box.bottom() >= after.top())
            ):
                cut.append(label.text())
        return cut

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        """Give every label the height its wrapped text actually needs.

        The width is the layout's, once there is one. Before the panel has ever
        been laid out that is nothing useful, so the panel's own width less its
        borders stands in until the first resize corrects it.
        """
        width = max(self._layout.contentsRect().width(), self.width() - 24, 1)
        for label in self._labels:
            if label.text():
                label.setMinimumHeight(label.heightForWidth(width))

    def refresh(self) -> None:
        lines = storage_lines(self._watcher.reading, self._watcher.settings.storage)
        if lines == self._shown:
            return  # this runs every two seconds for months
        self._shown = lines
        while len(self._labels) < len(lines):
            label = QLabel("")
            label.setWordWrap(True)
            # A word-wrapped QLabel asks for the height of ONE line unless its
            # size policy says the height depends on the width, and a layout that
            # believes it draws the second line over the line beneath it. The
            # column is 340 px wide and the longest of these sentences takes four
            # lines of it.
            policy = label.sizePolicy()
            policy.setHeightForWidth(True)
            policy.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
            label.setSizePolicy(policy)
            self._layout.addWidget(label)
            self._labels.append(label)
        for index, label in enumerate(self._labels):
            if index < len(lines):
                text, colour = lines[index]
                label.setText(text)
                label.setStyleSheet(f"color: {colour};")
                label.setVisible(True)
            else:
                label.setText("")
                label.setVisible(False)
        self._fit()
