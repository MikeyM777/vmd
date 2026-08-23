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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QGroupBox, QLabel, QSizePolicy, QVBoxLayout, QWidget

from vmd.desktop.style import PALETTE
from vmd.desktop.watch import Watched
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

# The smallest a file can be and still hold any video at all.
#
# This was `> 0`, and `> 0` was fitted to the one failure anybody had seen: a
# recorder writing ZERO-byte files. An ffmpeg that opens a file, writes the
# header and then dies writes a few hundred bytes, and every one of those sailed
# through - so a folder filling with headers was counted as footage and the
# console said "recording".
#
# An MP4 carrying an `ftyp` box and a `moov` describing one track, with not one
# frame in it, is well under two kilobytes. Four kilobytes is comfortably past
# that and far below anything real: a segment is `segment_seconds` long - five
# minutes by default - and even a thermal head at the bottom of its bitrate
# range puts hundreds of kilobytes into one. Nothing under this threshold could
# contain video, whatever else it is.
SMALLEST_SEGMENT_BYTES = 4096

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
                    if stat.st_size < SMALLEST_SEGMENT_BYTES:
                        continue  # a header with no video in it is not footage
                    files.append((stat.st_mtime, int(stat.st_size)))
        except OSError:
            continue  # one unreadable stream folder must not lose the others
    return files


def recorded_bytes(root) -> int | None:
    """How much footage is in this folder right now, or None if it cannot be read.

    The one question in this file that is asked from a button press rather than
    from the watcher, and the rule at the top of the file still holds: it is a
    directory walk, so it does not go on the heartbeat and it does not go in the
    panel. It is here because the Save button has to be able to say how much
    footage lowering the budget is about to delete, and that is this number.

    None rather than zero when the folder cannot be read. Nothing can be said
    about what would be deleted if the folder cannot be looked at, and a zero
    would say "nothing" - which is the one answer that must not be guessed.
    """
    try:
        return sum(size for _mtime, size in _segment_files(Path(root)))
    except OSError:
        return None


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
    silence = now - newest
    if silence < -limit:
        # The newest file is dated later than this machine thinks it is. That
        # is not footage from next week, it is a date that was typed wrong -
        # this laptop is offline and its clock is set by hand.
        #
        # It used to be treated as "recent", which is the worst answer
        # available: a clock stepped back an hour, or a year, made every file
        # on the disk look as though it had just been written, so a recorder
        # that had been dead all day read as recording. The console cannot tell
        # whether footage is arriving until the date is right, and saying so is
        # the only honest answer. The same rule as the detector's report, which
        # treats a timestamp from next week as a clock that moved.
        return False, (
            "the newest recording is dated later than this machine's clock, "
            "so the date on this machine is wrong and whether anything is "
            "being recorded cannot be told until it is put right"
        )
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
                f"Budget: {bytes_in_words(used)} of {bytes_in_words(budget)} used ({fraction * 100:.0f}%)",
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
                f"Budget: off - {bytes_in_words(used)} recorded and never deleted to make room.",
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
    lines.append((f"Drive: {bytes_in_words(free)} free", colour))
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


def storage_fault(
    reading: DiskReading | None, storage: StorageSettings
) -> tuple[str, str] | None:
    """The worst thing the storage panel would be saying, or None when all is
    well - as (sentence, colour), for a bar that only appears when it is bad.

    The panel draws every line always; this asks those same lines the one
    question a fault bar has, which is whether any of them is a warning or an
    alarm. It reuses `storage_lines` on purpose rather than re-deciding from the
    thresholds: the panel and the bar must agree, and a bar with its own copy of
    "nearly full" would drift from the panel the first time either changed. The
    worst line wins - alarm before warning - so what reaches a single line is
    the one worth a single line.
    """
    lines = storage_lines(reading, storage)
    for wanted in (PALETTE["alarm"], PALETTE["warn"]):
        for text, colour in lines:
            if colour == wanted:
                return text, colour
    return None


def _left(headroom: float, rate: float, estimated: bool) -> str:
    if rate <= 0:
        return "No idea how long"
    seconds = headroom / rate
    return ("Roughly " if estimated else "About ") + _duration(seconds) + " left"


def _duration(seconds: float) -> str:
    if seconds < HOUR:
        return _plural(max(seconds / 60.0, 1), "minute")
    if seconds < 2 * DAY:
        return _plural(seconds / HOUR, "hour")
    return _plural(seconds / DAY, "day")


def _plural(count: float, word: str) -> str:
    """`1 minute`, `2 minutes`, and never `1 minutes`.

    The count is rounded first and the word chosen from what was rounded to,
    not from the unrounded figure: 1.4 minutes is drawn as `1` and so has to
    read `1 minute`. This shows in the state the panel matters most in - the
    last minute before a drive fills, and the last hour before it - which is
    the one place a sentence that looks unfinished is worst.
    """
    whole = round(count)
    return f"{whole:.0f} {word}" + ("" if whole == 1 else "s")


def bytes_in_words(count: float) -> str:
    """A number of bytes in the largest unit that keeps it readable."""
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

    A `Watched` with the recordings folder as its question. The mechanism moved
    out to `vmd/desktop/watch.py` when two more questions about this same folder
    - the detector's report and the movement list - had to stop being asked on
    the heartbeat: the rule at the top of this file is not about the disk, and
    three copies of it would have been three rules within a month.
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
        self._now = now
        self._read = read or (lambda settings, now: read_disk(settings, now=now))
        self._watched: Watched[DiskReading] = Watched(
            read=self._look,
            every=POLL_SECONDS,
            executor=executor,
            clock=clock,
            name="the recordings folder",
        )

    def _look(self) -> DiskReading:
        """One reading, on the worker. Never raises: a panel beats a dead thread.

        The settings are read here rather than captured when the reading was
        started, so a folder saved a moment ago is the folder that gets read.
        """
        settings = self.settings
        try:
            return self._read(settings, self._now())
        except Exception as exc:  # noqa: BLE001 - a panel beats a dead thread
            logger.exception("the recordings folder could not be read")
            return _unusable(
                self._now(), f"The recordings folder could not be read: {exc}."
            )

    @property
    def reading(self) -> DiskReading | None:
        return self._watched.value

    def apply(self, settings: Settings) -> None:
        """Take the settings the operator just saved, and ask again at once.

        A saved folder is a new question. Waiting out the rest of the interval
        would leave the panel describing the folder that was replaced.
        """
        self.settings = settings
        self._watched.again()

    def poll(self) -> None:
        """Called from the heartbeat. Starts a reading if one is due."""
        self._watched.poll()


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
