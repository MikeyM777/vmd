"""The recording service: record every enabled stream, index it, enforce retention.

A separate process from the console on purpose, and one that deliberately
outlives the window. Two things leave it, and neither of them is a function
call:

* Logging. When the console spawned this process it pumps stderr into the Logs
  tab. When the **logon task** spawned it - which is the machine's ordinary boot
  path, an hour before any human opens the console - stdout goes to
  `bin\\logs\\recorder.out.log`, which the operator has no way to open. So on the
  path that matters most, logging reaches nobody.
* `recording.json`, written beside the recordings every `interval` seconds. That
  makes it the only channel this process has on that boot path, which is what
  made it worth doing.

**The shape of `recording.json`, for whoever reads it.** It is `status()` plus
two fields, written whole or not at all (temporary file in the same directory,
fsync, rename):

* `written_at`, `interval` - when it was written and how often it is being
  written. **A reader must treat a file older than `max(STATUS_STALE_SECONDS,
  STATUS_STALE_INTERVALS * interval)` - or dated in the future, because this
  laptop's clock is typed in by hand - as *unknown*, never as healthy.** A
  recorder that wedged an hour ago left a file saying every stream was fine, and
  repeating that is worse than saying nothing. `status_fresh()` below is that
  rule; use it rather than a second copy of it. A missing file is also unknown:
  this process removes its report when it stops.
* `streams[]` - per stream `name`, `running`, `stalled`, `held_back`,
  `restarts`, `exit_code`, and `source` / `source_url`. `source` is `"local"` or
  `"camera"` and `source_url` has the password taken out of it. Those two names
  are the detector's, out of `detection.json`, deliberately: both processes are
  answering the same question - am I crossing the radio link a second time - and
  two vocabularies for one fact is how the sentence on screen ends up wrong.
* Everything else `status()` computes, of which the ones nothing could see
  before are `link_doubled`, `on_camera`, `retention_declined`, `index_broken`,
  `stage_failures`, `held_back`, `stuck_deletions` and `empty_segments`.
* `pid`, so a reader can tell this report apart from one left by a different
  recorder against the same folder. It is the same number as `recorder.pid`.

`healthy` is a summary and not a substitute for the fields: it is false for a
stream that is held back, for footage that cannot be deleted, for empty
segments, and for a catalogue that has stopped answering.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from vmd.detect.events import EventStore
from vmd.settings import Settings, SettingsError, load_settings
from vmd.streaming.endpoint import is_live, local_source, read_endpoint
from vmd.storage.discovery import (
    find_closed_segments,
    next_segment_start,
    parse_segment_start,
    segment_starts,
)
from vmd.storage.index import SegmentIndex
from vmd.storage.recorder import SegmentRecorder
from vmd.storage.retention import ClockWatch, apply_plan, plan_retention
from vmd.supervisor import Managed, Supervisor

logger = logging.getLogger(__name__)

# Written by the console when it starts the streaming server, beside the
# settings it was started with.
DEFAULT_ENDPOINT_PATH = Path("streaming.json")

# ------------------------------------------------- where the streams are read
#
# How often that file, and the port it names, are asked about again.
#
# It used to be asked exactly once, in __init__. The scheduled task starts this
# process at logon, before any human has opened the console, so there is no
# streaming server to find and every stream is recorded straight from the
# camera. The operator opens the console an hour later; go2rtc starts and opens
# its own connection to the camera, and from that moment every stream crosses
# the 15 km, ~5 Mb/s radio link twice - for months, with nothing saying so,
# because recording still works. That is the contention the whole architecture
# exists to prevent. The detector fixed exactly this and wrote down why; see
# `_try_the_other_address` in vmd\detect\runner.py.
SOURCE_CHECK_SECONDS = 15.0

# How long the new answer must hold before ffmpeg is moved to it.
#
# Moving means stopping and restarting ffmpeg, which is a cut in the footage. A
# go2rtc that comes and goes - one that is failing to start, or a console being
# opened and closed - must not cut the recording every few seconds. This is the
# same rule the supervisor applies to a service it has just started
# (`stable_after`): a thing that has only just appeared has proved nothing yet.
SOURCE_SETTLE_SECONDS = 60.0

# How soon after a segment boundary the move is allowed to happen.
#
# ffmpeg is killed rather than asked politely - on Windows `terminate` is
# `TerminateProcess` - so whatever file it has open at that moment is left
# without its index. Waiting until it has only just opened a new one makes that
# file a few seconds long instead of a few minutes: every segment before it was
# closed by ffmpeg itself and is whole. Cheaper than it looks, because the wait
# only runs while a move is pending.
BOUNDARY_GRACE_SECONDS = 15.0

# Nothing, as distinct from "no streaming server". `None` is a real answer here
# - it means the camera - so it cannot double as "no answer yet".
_NO_PENDING = object()

# The detector's database, beside this service's own. Opened only if it is
# already there; see _event_store.
EVENTS_FILENAME = "events.db"

# Where this process publishes what it knows, beside the recordings and beside
# the detector's `detection.json`. See the module docstring for the shape, and
# `vmd\detect_main.py` for the pattern this is a copy of rather than a second
# design.
STATUS_FILENAME = "recording.json"

# How old that file may be before a reader stops believing it, and how many of
# the recorder's own reporting intervals it is allowed to miss.
#
# The same two numbers, for the same reasons, as the console already applies to
# `detection.json` (DETECTION_STATUS_STALE_SECONDS / _INTERVALS in
# vmd\desktop\services.py). Thirty seconds is six missed writes at the default
# five-second pass: long enough that a laptop busy encoding two streams does not
# make a reader cry wolf, short enough that a wedged recorder is noticed within
# a handful of heartbeats. The interval multiple is what stops a recorder told
# to report less often from being permanently stale - the window follows what
# the writer said it was doing, rather than being decided here.
STATUS_STALE_SECONDS = 30.0
STATUS_STALE_INTERVALS = 4

# The last time retention believed, beside the catalogue it belongs to. See
# ClockWatch: this machine is offline, its date is typed in by a person, and
# nothing else on it would notice that the year had changed.
CLOCK_FILENAME = "retention-clock.json"

# ------------------------------------------------ when the catalogue breaks
#
# The index was opened once, in __init__, and replaced nowhere but a change of
# recording folder. A sqlite connection that has taken a `disk I/O error` is
# dead for good, and the recordings folder is one the operator may point at an
# external or network drive - so a USB reseat, or a share that drops with the
# radio link, ended indexing AND retention for the life of the process. ffmpeg
# does not use the database, so recording carried on; the console reads the
# folder rather than the catalogue, so it went on saying "recording"; nothing
# was ever deleted again, so the budget stopped being enforced. A full disk on
# a machine reporting itself healthy is the shape this system least affords.
#
# How many sqlite failures out of the indexing and retention stages before the
# connection is thrown away and opened again. Three, for the reason FLAPPING_AFTER
# is three in vmd\supervisor.py and the reason everything here is loud three
# times and rare afterwards: one failure is a blip, two is a drive settling,
# three is a connection that is not coming back on its own. The counter is
# cleared the moment the index is proven to work - after a successful read in
# retention, after a successful insert in indexing - so these are consecutive in
# the only sense that matters, and a reopen costs one connect and a re-read of
# the paths already indexed.
INDEX_FAILURES_BEFORE_REOPEN = 3

# How many reopens that did not work before this stops trying on the five-second
# loop. A database that is genuinely broken - the file corrupted, the folder
# read-only, the drive simply gone - cannot be fixed by opening it again, and
# reopening it every few seconds for months is the same fault as restarting an
# ffmpeg that will never start: it is the log flood that destroys the one
# diagnostic surface the operator has.
INDEX_REOPEN_LIMIT = 3

# ...and how long it is left alone after that. Bounded rather than latched, for
# the reason RESTART_WINDOW_SECONDS is bounded in vmd\storage\recorder.py:
# giving up must never be permanent on a machine nobody visits. A drive plugged
# back in at three in the morning has to be picked up without anyone doing
# anything, and one connect attempt every five minutes is not a flood by any
# measure. Measured on the monotonic clock, because the wall clock here is typed
# in by a person and can move by a year.
INDEX_REOPEN_QUIET_SECONDS = 300.0

# The two stages whose failures are worth reopening the index for. The others
# fail for their own reasons - a directory that cannot be listed, a settings
# file half-written - and none of those is answered by a new connection.
INDEX_STAGES = ("indexing", "retention")

# ---------------------------------------------------------------- the claim
#
# Which process is recording, written by the process that is recording.
#
# Three things have wanted to know that: the console, which adopts a live
# recorder rather than starting a second one; the scheduled task that starts
# recording at logon; and now this. Until now none of them owned the file -
# the console wrote it on the recorder's behalf and the logon task wrote its
# own copy - and two writers of one file, neither of whom owns it, is how a
# stale or simply wrong PID gets adopted. Adopting a wrong PID means the
# console says "recording" while nothing is written to disk, which is the worst
# shape a failure can take here.
#
# With the recorder writing and removing its own claim, every supervisor
# becomes safe by construction: whatever starts a second recorder, the second
# recorder itself refuses.

PID_FILENAME = "recorder.pid"

# What this process exits with when another recorder already holds the claim.
#
# Not 0, and not 1. Whoever started this one has to be able to tell the three
# apart: 0 is "it ran and finished", 1 is "it cannot record at all", and this is
# "somebody else is already recording, and that is the right outcome". The
# console read a 0 here as an ordinary death and started another recorder two
# seconds later, which stood down as well - sixteen processes in thirty seconds
# on the operator's laptop. With a code of its own the console adopts the
# recorder that answered instead of starting another. See
# vmd\desktop\services.py, which names this constant.
ALREADY_RECORDING_EXIT = 3

# The claim file holds a bare integer and nothing else, because another program
# already parses it that way: vmd\desktop\services.py does int(text.strip()).
# Anything richer would make it read "no recorder is running" and start a second
# one, which is the exact accident this file exists to prevent. Everything that
# will not fit in an integer goes in a companion file beside it, and any reader
# that does not know about the companion is left exactly as well off as it was.
#
# scripts\recorder_service.ps1 used to be a second reader, and a second writer -
# it recorded what Start-Process handed back, which on this venv is the
# trampoline that launches the real interpreter rather than the recorder itself.
# The recorder then found its own launcher in the file, stood down to it, and the
# supervisor started another. It no longer touches this file at all: one writer,
# and that writer is the process the claim is about.
IDENTITY_SUFFIX = ".json"

# What a live process must be running for the PID in the claim to be believed,
# when the companion file is not there to be more specific. The recorder is
# always started as `python -m vmd.record_main`.
RECORDER_IMAGES = ("python.exe", "pythonw.exe", "python3.exe", "python", "python3")


def _write_json_atomically(payload: dict, path: Path) -> None:
    """Write JSON so that the file on disk is never half written.

    The same shape as `save_settings` and as the detector's writer of
    `detection.json`, and for the same reason: a plain write truncates first, so
    a reader arriving mid-write finds an empty or spliced file - which, for this
    file, reads as a recorder with no streams at all. The temporary file is in
    the destination's own directory because os.replace is only atomic within one
    filesystem.

    Written out here rather than imported from `vmd\\detect_main.py` for the
    reason the console repeats `detection.json`'s name rather than importing it:
    a recording-only laptop must not need the detector's module to record.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(json.dumps(payload, indent=2))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


# Bounded against a pathological address; the same expression the detector and
# the Logs tab use, for the same reason and with the same limits.
_CREDENTIALS = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]{0,15}://[^\s/@:]{1,256}):([^\s/@]{0,256})@")


def without_credentials(text: str) -> str:
    """The same address with the password inside it taken out.

    RTSP carries the camera's credentials in the address, and this file is read
    by anything on the machine and copied into any report of a fault. The
    username stays: which account was refused is half the diagnosis of a 401,
    and it is not the secret.
    """
    if "://" not in text or "@" not in text:
        return text
    return _CREDENTIALS.sub(r"\1:****@", text)


def status_fresh(status: dict, now: float) -> bool:
    """Is a `recording.json` recent enough to believe? Stale means unknown.

    A recorder that wedged an hour ago left a file saying every stream was fine,
    and repeating that is worse than saying nothing, because it is the answer
    the operator would have wanted to hear.

    A file dated in the future is treated the same way. This laptop's clock is
    set by hand and gets set wrong, and a timestamp from next week is a clock
    that moved rather than a recorder that is well.

    The window follows the `interval` the writer published, so a recorder told to
    report less often cannot silently become permanently stale.
    """
    try:
        written_at = float(status["written_at"])
    except (KeyError, TypeError, ValueError):
        return False
    try:
        interval = float(status.get("interval") or 0.0)
    except (TypeError, ValueError):
        interval = 0.0
    limit = max(STATUS_STALE_SECONDS, STATUS_STALE_INTERVALS * interval)
    return -limit <= (now - written_at) <= limit


@dataclass(frozen=True)
class RecorderIdentity:
    """Enough about the claiming process to tell it from a recycled PID."""

    pid: int
    executable: str = ""
    settings: str = ""
    written_at: float = 0.0

    def as_dict(self) -> dict:
        return {
            "pid": self.pid,
            "executable": self.executable,
            "settings": self.settings,
            "written_at": self.written_at,
        }

    @classmethod
    def for_this_process(cls, settings_path: str | Path | None = None) -> "RecorderIdentity":
        return cls(
            pid=os.getpid(),
            # The interpreter, which is what the logon wrapper already compares
            # against the venv's python.exe. A PID that has been handed out
            # again is almost never running the same image.
            executable=sys.executable or "",
            settings=str(settings_path or ""),
            written_at=time.time(),
        )


def identity_path(pid_path: str | Path) -> Path:
    return Path(str(pid_path) + IDENTITY_SUFFIX)


def read_pid(pid_path: str | Path) -> int | None:
    """The number in the claim file, or None if there is not one."""
    try:
        return int(Path(pid_path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def read_identity(pid_path: str | Path) -> RecorderIdentity | None:
    """What the claiming process said about itself, if it said anything.

    None whenever the companion is missing or unusable, which includes every
    claim written by an older console or by the logon wrapper. Callers treat
    that as "less is known", never as "the claim is bad".
    """
    try:
        payload = json.loads(identity_path(pid_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return RecorderIdentity(
            pid=int(payload["pid"]),
            executable=str(payload.get("executable") or ""),
            settings=str(payload.get("settings") or ""),
            written_at=float(payload.get("written_at") or 0.0),
        )
    except (KeyError, TypeError, ValueError):
        return None


# Windows says ERROR_SHARING_VIOLATION when a file cannot be opened exclusively
# because somebody else already has a handle to it. It is the one answer that
# distinguishes a file still being written from a file that merely happens to be
# the newest one in its directory.
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33


def held_open(path: str | Path) -> bool | None:
    """Whether another process has this file open. None when it cannot be told.

    Asked by opening the file with no sharing allowed and closing it again: if
    anything else holds a handle, Windows refuses with ERROR_SHARING_VIOLATION
    and nothing is opened. ffmpeg's output handle is an ordinary one, so a
    segment it is still writing answers "yes" here.

    This is asked instead of trusting the modification time, because on Windows
    the modification time of an open file is not written back to the directory
    entry until the handle is closed. A segment ffmpeg has been writing for ten
    minutes can therefore look untouched for ten minutes, which is exactly what
    a settle window would read as "finished".

    None - "cannot be told" - covers a machine that is not Windows and every
    refusal that is not a sharing violation, a missing file included. The caller
    falls back to the settle window there, which is what this file did before.
    """
    if os.name != "nt":  # pragma: no cover - not the deployment platform
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        invalid = ctypes.c_void_p(-1).value
        handle = kernel32.CreateFileW(
            str(path),
            0x80000000,  # GENERIC_READ
            0,  # share with nobody: this is the whole question
            None,
            3,  # OPEN_EXISTING
            0x80,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        if handle and ctypes.c_void_p(handle).value != invalid:
            kernel32.CloseHandle(handle)
            return False
        error = ctypes.get_last_error()
    except Exception:  # noqa: BLE001 - a probe that cannot run proves nothing
        logger.debug("could not ask whether %s is open", path, exc_info=True)
        return None
    if error in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION):
        return True
    return None


def boot_time() -> float | None:
    """When this machine last started, in epoch seconds, or None if unknown.

    The sharpest answer available to the question the coordinator of any of
    these supervisors actually has: a claim written before the last boot names a
    PID that cannot possibly still be its process, whatever is holding that
    number now. GetTickCount64 is in the kernel this already depends on and
    needs nothing installed.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        return time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:  # noqa: BLE001 - an unknown boot time is not a failure
        return None


def process_image(pid: int) -> str | None:
    """The executable name of a live process, or None if there is no such process.

    `tasklist` rather than anything richer because this has to work on a machine
    where only the standard library is installed - psutil arrives with the
    detector's extras and a recording-only laptop does not have it.
    """
    if os.name != "nt":  # pragma: no cover - not the deployment platform
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return "python"
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        # Cannot tell. Not the same as "nothing is there", and the caller must
        # not read it as such.
        return ""
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith('"'):
            return line.split('","')[0].strip('"')
    return None  # "INFO: No tasks are running which match the specified criteria."


def running_recorder(
    pid_path: str | Path,
    our_pid: int | None = None,
    image_of: Callable[[int], str | None] | None = None,
    booted: Callable[[], float | None] | None = None,
) -> int | None:
    """The pid of a recorder that really is running, or None if the claim is free.

    "A process with that number exists" is a different claim from "the recorder
    is running", and the difference is where this has gone wrong. A claim file
    survives `taskkill /F`, and it survives a reboot, and Windows hands the same
    numbers out again - so a stale file plus an unrelated process wearing the
    recycled number reads as a healthy recorder to anything that only asks
    whether the PID is alive.
    """
    # Resolved here rather than as default arguments, so that these two - the
    # only things in this file that ask the operating system anything - can be
    # replaced by a caller or a test without the module having to be reloaded.
    image_of = process_image if image_of is None else image_of
    booted = boot_time if booted is None else booted

    pid = read_pid(pid_path)
    if pid is None or pid <= 0:
        return None
    our_pid = os.getpid() if our_pid is None else our_pid
    if pid == our_pid:
        return None  # our own claim, written a moment ago by whoever started us

    identity = read_identity(pid_path)
    machine_started = booted()
    if identity and identity.written_at and machine_started:
        if identity.written_at < machine_started:
            logger.info(
                "%s names pid %s but was written before this machine last "
                "started, so it is left over from an earlier boot",
                pid_path,
                pid,
            )
            return None

    image = image_of(pid)
    if image is None:
        logger.info("%s names pid %s, which is not running", pid_path, pid)
        return None
    if image == "":
        # The process list could not be read. Nothing is proven either way, and
        # the safe reading is that the recorder is up: refusing to start costs
        # this one process, starting a second one costs the archive.
        return pid
    expected = Path(identity.executable).name if identity and identity.executable else ""
    if expected:
        if image.lower() != expected.lower():
            logger.info(
                "%s names pid %s, but that is %s and the recorder was %s - "
                "the number has been given to something else",
                pid_path,
                pid,
                image,
                expected,
            )
            return None
    elif image.lower() not in RECORDER_IMAGES:
        logger.info(
            "%s names pid %s, which is %s and cannot be a recorder", pid_path, pid, image
        )
        return None
    return pid


def claim_recorder(
    pid_path: str | Path,
    identity: RecorderIdentity | None = None,
    image_of: Callable[[int], str | None] | None = None,
    booted: Callable[[], float | None] | None = None,
) -> int | None:
    """Take the claim, or say which recorder already has it.

    Returns None once the claim is ours, or the pid of the recorder that holds
    it. Created exclusively, so two recorders starting at the same instant
    cannot both believe they won: the loser finds the winner's number in the
    file, sees a live process behind it, and defers.

    A claim that cannot be written at all - a read-only folder, a full disk - is
    a warning and nothing more. Recording without a claim is worse than
    recording with one and far better than not recording.
    """
    pid_path = Path(pid_path)
    identity = identity or RecorderIdentity.for_this_process()
    for _ in range(3):
        try:
            pid_path.parent.mkdir(parents=True, exist_ok=True)
            handle = os.open(str(pid_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = running_recorder(
                pid_path, our_pid=identity.pid, image_of=image_of, booted=booted
            )
            if holder is not None:
                return holder
            # Nobody is behind it. Clear it and go round again rather than
            # overwriting in place, so that a recorder which starts in the gap
            # is found alive on the next turn instead of being trampled.
            try:
                identity_path(pid_path).unlink(missing_ok=True)
                pid_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("the stale claim in %s could not be cleared", pid_path)
                return None
            continue
        except OSError:
            logger.warning("could not write %s; recording anyway", pid_path, exc_info=True)
            return None

        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(str(identity.pid))
        try:
            identity_path(pid_path).write_text(
                json.dumps(identity.as_dict(), indent=2), encoding="utf-8"
            )
        except OSError:
            # The bare number is what every existing reader needs; the companion
            # only makes the check sharper.
            logger.warning("could not write %s", identity_path(pid_path), exc_info=True)
        return None
    return running_recorder(pid_path, our_pid=identity.pid, image_of=image_of, booted=booted)


def release_recorder(pid_path: str | Path, pid: int | None = None) -> None:
    """Drop the claim, but only while it still names us.

    Something else may have taken it over in the meantime - the console starting
    its own recorder, the logon task starting one after this exited - and
    deleting that claim would let the next supervisor start a second recorder
    beside a live one.
    """
    pid = os.getpid() if pid is None else pid
    if read_pid(pid_path) != pid:
        return
    for path in (identity_path(pid_path), Path(pid_path)):
        try:
            path.unlink(missing_ok=True)
        except OSError:  # noqa: PERF203 - shutdown must always complete
            logger.warning("could not remove %s", path, exc_info=True)


class RecordingService:
    """Owns the recorders, the index and the retention pass."""

    def __init__(
        self,
        settings: Settings,
        spawn: Callable | None = None,
        retention_interval: float = 60.0,
        settle_seconds: float = 5.0,
        endpoint_path: str | Path | None = None,
        settings_path: str | Path | None = None,
        source_check_interval: float = SOURCE_CHECK_SECONDS,
        source_settle_seconds: float = SOURCE_SETTLE_SECONDS,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings
        # A clock that cannot be set by hand, for the one decision here that
        # must not be moved by an operator correcting the date: how long a
        # catalogue that will not open is left alone before it is tried again.
        self._monotonic = monotonic or time.monotonic
        # Where the operator's choices are written down, so that saving the
        # Settings tab can reach a process the console cannot restart. None
        # means "these settings and no others", which is what every caller that
        # hands in a Settings object directly wants.
        self.settings_path = Path(settings_path) if settings_path else None
        self._settings_stamp = self._settings_timestamp()
        # The path, not the answer. The answer changes underneath this process:
        # see SOURCE_CHECK_SECONDS.
        self._endpoint_path = Path(endpoint_path or DEFAULT_ENDPOINT_PATH)
        self._endpoint = self._live_endpoint()
        self.source_check_interval = source_check_interval
        self.source_settle_seconds = source_settle_seconds
        self._last_source_check: float | None = None
        # The answer waiting to be believed, when it is not the one in use.
        self._pending_endpoint: object = _NO_PENDING
        self._pending_since: float | None = None
        self._switch_due: float | None = None
        self._source_switches = 0
        # What has already been said about each stream's source, so the line is
        # written when the answer changes rather than on every rebuild. Set
        # before _build_recorders, which is what says it.
        self._announced_local: dict[str, bool] = {}
        # How long a file must sit untouched before it counts as finished. The
        # same window guards both discovery and orphan adoption; adoption used
        # to have none, which made it the weaker of the two paths.
        self.settle_seconds = settle_seconds
        self._spawn_kwargs = {"spawn": spawn} if spawn else {}
        self.root = Path(settings.storage.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = SegmentIndex(self.root / "segments.db")
        # What the catalogue has done lately. See INDEX_FAILURES_BEFORE_REOPEN:
        # sqlite failures out of the two stages that use it, reopens that have
        # not worked, and - when it has been given up on - the sentence saying
        # so, which is what reaches status() and from there the console.
        self._index_failures = 0
        self._index_reopens = 0
        self._index_broken: str | None = None
        self._index_gave_up_at: float | None = None
        self._said_index_broken = False
        self._clock = ClockWatch(self.root / CLOCK_FILENAME)
        self._clock_verdict = None
        self._last_clock_look = 0.0
        self._retention_declined: str | None = None
        self._declined_said = 0
        self._clock_jumps = 0
        # Opened on demand rather than here; see _event_store.
        self.events_path = self.root / EVENTS_FILENAME
        # Where what this process knows is published; see write_status.
        self.status_path = self.root / STATUS_FILENAME
        self._events = None
        self._events_failures = 0
        try:
            self._last_segment_at: dict[str, float] = {}
            self._started_at: dict[str, float] = {}
            self._build_recorders()
            self._seen: set[str] = {s.path for s in self.index.all()}
            self._last_warning: str | None = None
            # Retention runs on its own slower cadence; see _apply_retention.
            self.retention_interval = retention_interval
            self._last_retention = 0.0
            self._stuck_deletions = 0
            self._stall_restarts = 0
            self._empty_segments = 0
            self._empty_seen: set[str] = set()
            self._stage_failures: dict[str, int] = {}
            self._adopt_orphans()
        except Exception:
            # Nothing will hold a reference to this half-built service, so the
            # connection would leak. On Windows a lingering handle can make an
            # immediate retry fail with "database is locked".
            self.index.close()
            raise

    def _build_recorders(self) -> None:
        """One recorder per enabled stream, and a supervisor over them.

        Called again whenever the set of streams or the folder they write into
        changes, so it must leave no trace of the recorders it replaced: the
        stall clocks are keyed by stream name and a name that has just been
        pointed somewhere else must start its grace period again, not inherit
        the previous recorder's.
        """
        self.recorders = [
            SegmentRecorder(
                stream=stream.name,
                source_url=self._source_for(stream),
                output_dir=self.root / stream.name,
                segment_seconds=self.settings.storage.segment_seconds,
                **self._spawn_kwargs,
            )
            for stream in self.settings.camera.streams
            if stream.enabled
        ]
        self.supervisor = Supervisor(
            [Managed(name=r.stream, service=r) for r in self.recorders]
        )
        self._last_segment_at = {}
        self._started_at = {}
        # The last thing said on behalf of each stream, so an ffmpeg that fails
        # the same way every five seconds is reported once. Cleared with the
        # recorders, because a fresh recorder's first complaint is news again.
        self._last_ffmpeg_line: dict[str, str] = {}
        # Not cleared with them: which side of the link a stream is read from is
        # news when it changes, and a rebuild for some other reason has not
        # changed it.
        self._announce_sources()

    def _settings_timestamp(self) -> int | None:
        """When the settings file was last written, or None if there is no file."""
        if self.settings_path is None:
            return None
        try:
            return self.settings_path.stat().st_mtime_ns
        except OSError:
            return None

    def _event_store(self):
        """The movement events, if a detector has ever written any.

        Opened lazily, and only when the file already exists. Opening it in the
        constructor would be simpler, and would create events.db on every
        machine - including the ones where detection was never turned on, where
        an empty database beside the recordings is a thing an operator has to
        ask about and a thing a backup has to carry. The recorder is not the
        detector, and it should not leave the detector's fingerprints.

        Checked on every retention pass rather than once, because detection can
        be ticked on in the Settings tab one afternoon while this service has
        been running since March: the store appears underneath a process that
        has already decided there was none, and the events written from then on
        must still be reclaimed with the footage they point at.

        `EventStore` itself is imported at the top of this file. It used not to
        be, on the grounds that `vmd.detect` dragged the detector's whole stack -
        cv2, numpy, eventually the weights - behind it. That has stopped being
        true: `vmd/detect/__init__.py` resolves its names on first use, so
        `from vmd.detect.events import EventStore` costs sqlite3 and a
        dataclass. Only the import moved. Opening the store on first use, and
        only when the file is already there, is a separate rule and it stands.
        """
        if self._events is not None:
            return self._events
        if not self.events_path.exists():
            return None
        try:
            self._events = EventStore(self.events_path)
        except Exception:  # noqa: BLE001 - retention frees the disk with or without this
            self._events_failures += 1
            # Loud the first few times, then rare. This is retried every
            # retention pass for as long as the fault lasts, and a fault that
            # lasts for days must stay visible without burying the log.
            if self._events_failures <= 3 or self._events_failures % 100 == 0:
                logger.exception(
                    "the movement events could not be opened (%d times); "
                    "footage will be reclaimed without them",
                    self._events_failures,
                )
            self._events = None
        return self._events

    def _source_for(self, stream) -> str:
        """Prefer the local streaming server over the camera.

        The console already holds one connection to the camera and re-serves it
        on this machine. Recording from there means the stream crosses the radio
        link once instead of twice - which on a five megabit link is the
        difference between recording and losing the live picture as well.

        If the streaming server is not running, the camera is used directly:
        recording something is more important than recording it cheaply.

        Says nothing: whether the answer is worth a line depends on whether it
        has changed, which is `_announce_sources`.
        """
        return local_source(self._endpoint, stream.name) or stream.url

    # -------------------------------------------- keeping that answer current

    def _live_endpoint(self) -> dict | None:
        """What the streaming server is, right now, or None if there is not one.

        A stale file is worse than none - it would point ffmpeg at a loopback
        port nothing is listening on - so the file is only believed while
        something answers on the port it names.
        """
        endpoint = read_endpoint(self._endpoint_path)
        if endpoint and is_live(endpoint):
            return endpoint
        return None

    def _sources_from(self, endpoint) -> dict[str, str]:
        """Where each enabled stream would be read from, given that endpoint."""
        return {
            stream.name: local_source(endpoint, stream.name) or stream.url
            for stream in self.settings.camera.streams
            if stream.enabled
        }

    def _sources_now(self) -> dict[str, str]:
        return {recorder.stream: recorder.source_url for recorder in self.recorders}

    def _recheck_source(self, now: float) -> None:
        """Ask again where the streams should be read from, and move if it changed.

        Three things have to hold at once, and each of them is a way this can go
        wrong rather than a nicety:

        * **Notice at all.** The console starts minutes or hours after this
          process, and a go2rtc that restarts can come back on a different port
          (`free_port`), so the answer this process started with expires.
        * **Do not flap.** Moving means killing ffmpeg and starting it again. A
          go2rtc that is failing to start would otherwise cut the recording
          every couple of seconds, which is worse than the doubled link it is
          being moved away from.
        * **Move away as readily as towards.** If go2rtc dies while the console
          is gone, ffmpeg is pointed at a dead loopback port and the stream
          simply stops. Recording from the wrong place beats not recording.
        """
        if self._last_source_check is not None:
            elapsed = now - self._last_source_check
            if 0 <= elapsed < self.source_check_interval:
                return
        self._last_source_check = now

        fresh = self._live_endpoint()
        wanted = self._sources_from(fresh)
        if wanted == self._sources_now():
            # Already reading from there. Keep the endpoint itself current all
            # the same: the ports may be the same object with a new process
            # behind it, and a later rebuild must not re-derive an old answer.
            self._endpoint = fresh
            self._forget_pending_source()
            return

        if self._pending_endpoint is _NO_PENDING or (
            self._sources_from(self._pending_endpoint) != wanted
        ):
            # A different answer from the one that was settling: start again.
            self._pending_endpoint = fresh
            self._pending_since = now
            # How long the boundary may be waited for before the move happens
            # anyway. Without it a stream that never closes a segment - one that
            # is failing, or one whose ffmpeg is held back - would keep the link
            # doubled for ever while politely waiting for a boundary that is
            # never coming.
            self._switch_due = (
                now
                + max(self.source_settle_seconds, 0.0)
                + 2.0 * self.settings.storage.segment_seconds
            )
            return

        waited = now - (self._pending_since or now)
        if waited < self.source_settle_seconds:
            return
        if not self._at_a_segment_boundary(now) and now < (self._switch_due or now):
            return
        self._move_sources(fresh, now)

    def _forget_pending_source(self) -> None:
        self._pending_endpoint = _NO_PENDING
        self._pending_since = None
        self._switch_due = None

    def _at_a_segment_boundary(self, now: float) -> bool:
        """Has every recorder only just opened the file it is writing?

        Read from the names, which is where the truth is: ffmpeg stamps each
        file with the moment it opened it, and on Windows the modification time
        of a file something still holds is not written back until the handle is
        closed - so mtime cannot answer this and the filename can.

        A stream with no files yet, or one whose ffmpeg is not running, has
        nothing open to lose and never holds the move up.
        """
        for recorder in self.recorders:
            if not recorder.running:
                continue
            try:
                starts = segment_starts(recorder.output_dir)
            except OSError:
                continue
            if not starts:
                continue
            if now - starts[-1] > BOUNDARY_GRACE_SECONDS:
                return False
        return True

    def _move_sources(self, endpoint: dict | None, now: float) -> None:
        self._endpoint = endpoint
        self._forget_pending_source()
        self._source_switches += 1
        self._rebuild_recorders("where the streams are read from changed", now)

    def _announce_sources(self) -> None:
        """Say which side of the radio link each stream is being read from.

        Said when it changes, in this process's own output, because that output
        is what reaches the Logs tab. "Recording" that is silently costing
        double the link is precisely the kind of fault this machine has been
        bitten by: everything reports healthy, the picture stutters, and every
        part of the system blames the link.

        No URL is ever put in these lines. The camera's address carries its
        password.
        """
        for recorder in self.recorders:
            local = bool(local_source(self._endpoint, recorder.stream))
            if self._announced_local.get(recorder.stream) == local:
                continue
            self._announced_local[recorder.stream] = local
            if local:
                logger.info(
                    "%s is being recorded from the streaming server on this "
                    "machine, so the radio link carries it once",
                    recorder.stream,
                )
            elif self._endpoint is not None:
                logger.warning(
                    "%s is being recorded straight from the camera even though "
                    "the streaming server is running - it is not serving a "
                    "stream by that name, so the radio link is carrying %s "
                    "twice",
                    recorder.stream,
                    recorder.stream,
                )
            else:
                logger.info(
                    "%s is being recorded straight from the camera; there is no "
                    "streaming server on this machine to read it from",
                    recorder.stream,
                )

    def link_doubled(self) -> list[str]:
        """Streams being pulled from the camera while go2rtc holds it as well."""
        if self._endpoint is None:
            return []
        return [
            recorder.stream
            for recorder in self.recorders
            if not local_source(self._endpoint, recorder.stream)
        ]

    def run_once(self, now: float | None = None) -> None:
        """One pass: keep recorders alive, index finished segments, apply retention.

        Each stage is isolated from the others. They used to run in sequence with
        no guard, which made a full disk self-locking: the index write failed,
        the exception ended the pass before retention could run, so nothing was
        deleted, so the disk stayed full - forever, and reported healthy.
        Retention is the stage that frees the disk, so it must run even when
        everything before it has failed.
        """
        now = time.time() if now is None else now
        self._stage("settings", self._reload_settings, now)
        self._stage("the streaming server", self._recheck_source, now)
        for recorder in self.recorders:
            self._started_at.setdefault(recorder.stream, now)
        self._stage("supervisor", self.supervisor.tick)
        self._stage("ffmpeg's own words", self._repeat_what_ffmpeg_said)
        # Before anything reads a timestamp or writes a row: what the clock has
        # done changes what indexing has to do, and what retention is allowed to
        # do. See ClockWatch.
        self._stage("the clock", self._watch_the_clock, now)
        self._stage("empty segments", self._notice_empty_segments)
        self._stage("indexing", self._index_new_segments, now)
        self._stage("stall check", self._restart_stalled, now)
        self._stage("retention", self._apply_retention, now)

    def _repeat_what_ffmpeg_said(self) -> None:
        """Put ffmpeg's stderr where the operator can read it.

        This process's own output is pumped into the console's Logs tab, which
        on that laptop is the only place anything can be read at all. ffmpeg's
        went to a file beside the recordings and stayed there: when the camera
        turned out to be sending audio MP4 cannot carry, the two lines that said
        so exactly - "Could not find tag for codec pcm_mulaw" and "Could not
        write header" - sat in recordings\\thermal.ffmpeg.log for a whole day
        while the console reported that it was recording.

        The same line is not repeated: an ffmpeg restarted every five seconds
        writes the same complaint every five seconds, and five hundred lines of
        one sentence is the same as no log at all.
        """
        for recorder in self.recorders:
            for line in recorder.new_log_lines():
                if self._last_ffmpeg_line.get(recorder.stream) == line:
                    continue
                self._last_ffmpeg_line[recorder.stream] = line
                logger.error("%s: ffmpeg: %s", recorder.stream, line)

    def _notice_empty_segments(self) -> None:
        """Say when files are being written that contain nothing.

        Nothing was indexing these - `find_closed_segments` skips a zero-byte
        file, and so do the final pass, the orphan sweep and the console's disk
        reading - which is right, and left the machine in the worst state a
        fault can reach: 24 files appeared in the recording folder in four
        minutes, every one of them empty, and nothing anywhere said a word. The
        console read the folder, found no footage, and reported "NOT recording -
        nothing has ever been written", which is true and does not say that the
        recorder is trying every five seconds and failing.

        A file of zero bytes that has a newer file beside it is the signal, and
        it is not ambiguous: ffmpeg opened it, wrote no header, and moved on.
        The file ffmpeg currently holds is never counted - on Windows its size
        in the directory entry stays zero until the handle is closed, so the
        newest file being empty is the ordinary state of a healthy recorder.
        """
        for recorder in self.recorders:
            try:
                files = sorted(
                    (path.stat().st_mtime, path)
                    for path in recorder.output_dir.glob("*.mp4")
                )
            except OSError:
                continue
            for _mtime, path in files[:-1]:
                key = str(path)
                if key in self._empty_seen:
                    continue
                try:
                    if path.stat().st_size != 0:
                        continue
                except OSError:
                    continue
                self._empty_seen.add(key)
                self._empty_segments += 1
                # Loud the first few times, then rare: this is one line per
                # broken segment and the Logs tab holds five hundred.
                if self._empty_segments <= 3 or self._empty_segments % 100 == 0:
                    logger.error(
                        "%s: %s was opened and nothing was ever written to it "
                        "(%d such files so far), so it is not footage and is not "
                        "indexed. ffmpeg is exiting before it records anything - "
                        "its own words are in the lines above",
                        recorder.stream,
                        path.name,
                        self._empty_segments,
                    )

    def _stage(self, name: str, work, *args) -> None:
        try:
            work(*args)
        except Exception as exc:  # noqa: BLE001 - one broken stage must not skip the rest
            self._stage_failures[name] = self._stage_failures.get(name, 0) + 1
            count = self._stage_failures[name]
            # Loud the first few times, then rare: a fault that lasts for days
            # must stay visible in the log without burying everything else.
            if count <= 3 or count % 100 == 0:
                logger.exception("%s failed (%d times); continuing", name, count)
            if isinstance(exc, sqlite3.Error) and name in INDEX_STAGES:
                # Only sqlite's own errors. A directory that cannot be listed
                # and a settings file that will not parse both surface here as
                # well, and neither of them is answered by a new connection.
                self._index_failures += 1
                if self._index_failures >= INDEX_FAILURES_BEFORE_REOPEN:
                    self._reopen_index()

    # ------------------------------------------------- keeping the catalogue

    def _index_worked(self) -> None:
        """Called where the index has just demonstrably answered.

        This is what makes the failure count consecutive. A connection that
        reads and writes is not a connection that needs replacing, whatever it
        did an hour ago, and a catalogue that was given up on and has since
        started working is not broken any more - the drive came back.
        """
        self._index_failures = 0
        if self._index_broken is not None:
            logger.warning("the segment catalogue is answering again")
        self._index_broken = None
        self._index_gave_up_at = None
        self._said_index_broken = False
        self._index_reopens = 0

    def _reopen_index(self) -> None:
        """Throw the connection away and open the catalogue again.

        The database file is almost never the casualty - the connection is. So
        the new one is opened and proven with a read *before* the old one is let
        go, and what has already been indexed is re-derived from the rows that
        are actually there rather than carried across in memory. That is what
        makes this safe to do at any moment: nothing is lost, because the rows
        are the record; and nothing is counted twice, because `SegmentIndex.add`
        is keyed on the path and corrects a row rather than adding a second one.

        A reopen that does not work is counted, and after INDEX_REOPEN_LIMIT of
        them this stops trying at the loop's cadence and says, in status() and
        once in the log, that the catalogue is unusable. It is tried again after
        INDEX_REOPEN_QUIET_SECONDS, because nobody visits this machine and a
        drive that comes back at three in the morning has to be picked up on its
        own.
        """
        now = self._monotonic()
        if self._index_broken is not None:
            waited = now - (self._index_gave_up_at if self._index_gave_up_at is not None else now)
            if 0 <= waited < INDEX_REOPEN_QUIET_SECONDS:
                return
        self._index_failures = 0
        try:
            fresh = SegmentIndex(self.root / "segments.db")
            indexed = {segment.path for segment in fresh.all()}
        except Exception as exc:  # noqa: BLE001 - the reason belongs to the operator
            self._index_reopens += 1
            if self._index_reopens >= INDEX_REOPEN_LIMIT:
                self._give_up_on_the_index(exc, now)
            else:
                logger.warning(
                    "the segment catalogue could not be reopened (attempt %d): %s",
                    self._index_reopens,
                    exc,
                )
            return

        stale, self.index = self.index, fresh
        self._seen = indexed
        logger.warning(
            "the segment catalogue stopped answering and has been opened again; "
            "%d segments are in it, and recording never stopped",
            len(indexed),
        )
        self._index_worked()
        try:
            stale.close()
        except Exception:  # noqa: BLE001 - a dead connection may refuse to close
            logger.debug("the old catalogue connection would not close", exc_info=True)

    def _give_up_on_the_index(self, exc: Exception, now: float) -> None:
        """Stop reopening, and say what the operator has actually got.

        Said once rather than every pass, for the reason everything else here is
        said once: the Logs tab holds five hundred lines and a sentence repeated
        every five seconds destroys it. status() carries it for as long as it
        lasts, which is the channel that does not scroll.
        """
        self._index_gave_up_at = now
        self._index_broken = (
            f"the recordings catalogue could not be opened on "
            f"{self._index_reopens} attempts ({exc}). Recording is still running "
            f"and footage is still being written, but nothing is being indexed, "
            f"nothing is being deleted, and the disk will fill"
        )
        if not self._said_index_broken:
            self._said_index_broken = True
            logger.error("%s", self._index_broken)

    def run_forever(self, interval: float = 5.0) -> None:
        """Run until interrupted. A failed pass must never end the process.

        This runs unattended for months. Any exception escaping run_once would stop
        recording permanently, since nothing outside this process restarts it, so a
        failed pass is logged and the loop continues. The sleep happens on the failure
        path too: without it a persistent fault, such as a full disk, would become a
        tight busy loop.

        The report is published after every pass, including a pass that failed -
        a pass that keeps failing is exactly the state worth publishing - and
        once before the first one, so that anything opening beside a recorder
        that has been running since March does not have to wait out an interval
        before it can name the streams.
        """
        try:
            self.write_status(interval)
            while True:
                try:
                    self.run_once()
                except Exception:  # noqa: BLE001 - a bad pass must not end the service
                    logger.exception("recording pass failed; continuing")
                self.write_status(interval)
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # ------------------------------------------------------- saying it out loud

    def write_status(self, interval: float = 5.0) -> None:
        """Publish `status()` where something else can read it. Never raises.

        This process is separate from the console on purpose and outlives it, so
        everything it works out about itself - which stream is held back, which
        one is crossing the radio link twice, why retention refused to run - was
        state that reached nobody. On the machine's ordinary boot path it is
        worse than that: the logon task starts this process with its output
        going to a log file under bin\\, so logging reaches nobody either and
        this file is the only channel there is.

        `written_at` and `interval` go in because a reader has to be able to tell
        stale from fresh and cannot know how often this was told to write. See
        `status_fresh`.

        A failure is logged and swallowed. A full disk should make a reader say
        "unknown", which is true; it is not a reason to stop recording.
        """
        try:
            payload = dict(self.status())
        except Exception:  # noqa: BLE001 - the report must not be able to stop the pass
            logger.exception("what to publish could not be worked out")
            return
        payload["written_at"] = time.time()
        payload["interval"] = float(interval)
        # Which process this report is from. The claim file holds the same
        # number, so a reader can tell a report by the recorder it adopted from
        # one left by a different recorder against the same folder.
        payload["pid"] = os.getpid()
        try:
            _write_json_atomically(payload, self.status_path)
        except Exception:  # noqa: BLE001 - publishing must never be able to stop recording
            logger.warning("could not publish %s", self.status_path, exc_info=True)

    def clear_status(self) -> None:
        """Take the report down on the way out.

        A recorder that stopped cleanly and left its last report behind would
        have anything reading it believing a dead process's words until the
        staleness window ran out - and that report says the streams were fine,
        because they were, right up until it stopped.
        """
        try:
            self.status_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("could not remove %s", self.status_path, exc_info=True)

    def stop(self) -> None:
        self.supervisor.stop_all()
        # stop_all() blocks until each ffmpeg has exited, so the segment it was writing
        # is now closed and valid. Index it before shutting down, or it stays on disk
        # while being invisible to the budget.
        try:
            self._index_new_segments(time.time())
            self._index_final_segments()
        except Exception:  # noqa: BLE001 - shutdown must always complete
            logger.exception("final indexing pass failed")
        self._close_stores()
        self.clear_status()

    def status(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        stalled = set(self.stalled_streams(now))
        # Asked for, not assumed. This is published every pass now, so a
        # catalogue that has stopped answering must produce a report saying so
        # rather than an exception that takes the report with it - and it must
        # not produce a report saying the archive is empty, which is the same
        # lie in the other direction. `None` is "not known"; 0 is "none".
        try:
            segments = self.index.all()
        except Exception:  # noqa: BLE001 - the catalogue is the thing being reported on
            logger.debug("the catalogue could not be read for status", exc_info=True)
            segments = None
        used = sum(s.size_bytes for s in segments) if segments is not None else None
        oldest = segments[0].start if segments else None
        streams = [
            {
                "name": r.stream,
                "running": r.running,
                "stalled": r.stream in stalled,
                # An ffmpeg that exits before it records anything is stopped
                # rather than started every five seconds for ever, and a stream
                # in that state is broken rather than merely down: nothing will
                # start it until whatever is wrong with it changes.
                "held_back": r.held_back,
                "restarts": self.supervisor.restarts.get(r.stream, 0),
                "exit_code": r.exit_code,
                # Which side of the radio link this stream is coming from.
                # "local" or "camera", and the address with the password taken
                # out of it - the detector's two names for the same fact, out of
                # `detection.json`, because the console reads both files and puts
                # one sentence on screen.
                "source": "local" if local_source(self._endpoint, r.stream) else "camera",
                "source_url": without_credentials(r.source_url),
            }
            for r in self.recorders
        ]
        return {
            "streams": streams,
            # A stream that never starts successfully keeps `restarts` at zero, so
            # health must be derived from `running`, never from the restart count.
            # `all([])` is True, so a service with no streams at all would otherwise
            # report itself healthy while recording nothing. The CLI refuses to start
            # in that state, but status() is about to become a web API and must be
            # trustworthy on its own.
            "healthy": (
                bool(streams)
                and all(
                    s["running"] and not s["stalled"] and not s["held_back"]
                    for s in streams
                )
                and not self._stuck_deletions
                and not self._empty_segments
                # Recording without a catalogue is recording that nothing can
                # find, on a disk nothing will ever reclaim.
                and self._index_broken is None
                and segments is not None
            ),
            "segments": len(segments) if segments is not None else None,
            "used_bytes": used,
            "budget_bytes": self.settings.storage.budget_bytes,
            "oldest": oldest,
            "warning": self._last_warning,
            "stuck_deletions": self._stuck_deletions,
            "stall_restarts": self._stall_restarts,
            "empty_segments": self._empty_segments,
            "restarts": dict(self.supervisor.restarts),
            # Streams being read straight from the camera rather than through
            # the local streaming server, counted the way the detector counts
            # them so that one sentence can be written about both processes.
            # Not a fault on its own: a stream with no local server is read from
            # the camera on purpose. It says the link is paying for it.
            "on_camera": sum(1 for s in streams if s["source"] == "camera"),
            # The sharper subset, and the one with no counterpart in the
            # detector: streams pulled from the camera while go2rtc is holding
            # that same camera as well. Every name here is a full-rate copy
            # crossing a >15 km, ~5 Mb/s link for no reason at all. Empty is the
            # healthy answer.
            "link_doubled": self.link_doubled(),
            "source_changes": self._source_switches,
            # Why retention did not delete everything it was asked to, if it
            # did not. None is the ordinary answer.
            "retention_declined": self._retention_declined,
            "clock_jumps": self._clock_jumps,
            # Why there is no catalogue, if there is none. None is the ordinary
            # answer; a sentence here means footage is being written and
            # nothing is indexing it or deleting anything.
            "index_broken": self._index_broken,
            "index_reopens": self._index_reopens,
            # How often each stage of the pass has failed since this process
            # started. Nothing read these before, which is why a stage failing
            # every pass for a week was a whisper. See _stage.
            "stage_failures": dict(self._stage_failures),
        }

    def _index_new_segments(self, now: float) -> None:
        for recorder in self.recorders:
            # Read once per directory, not once per file: it is only needed to
            # answer "where does this segment's coverage stop", and the answer
            # for every file in the directory comes out of the same listing.
            starts = segment_starts(recorder.output_dir)
            for path in find_closed_segments(
                recorder.output_dir, now=now, settle_seconds=self.settle_seconds, seen=self._seen
            ):
                start = parse_segment_start(path.name)
                if start is None:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                # The observed close time, not the nominal duration. A recorder that
                # died mid-segment wrote a short file, and recording that honestly is
                # what makes a dropout visible in the coverage timeline instead of
                # being papered over.
                end = self._end_of(start, stat.st_mtime, next_segment_start(starts, start))
                # Noted before the row is written, not after. This is the clock
                # the stall check reads, and what it is asking is whether ffmpeg
                # is producing files - which it can be doing perfectly while the
                # catalogue is refusing to record the fact. Updating it after
                # the insert meant a dead index made every stream look stalled,
                # so ffmpeg was killed and restarted every two segment lengths:
                # a fault in the catalogue putting holes in the footage.
                self._last_segment_at[recorder.stream] = now
                self.index.add(
                    stream=recorder.stream,
                    path=str(path),
                    start=start,
                    end=end,
                    size_bytes=stat.st_size,
                )
                self._seen.add(str(path))
                # A row written is the sharpest proof there is that the
                # catalogue works. See _index_worked.
                self._index_worked()

    # ------------------------------------------------------ following the tab

    def _reload_settings(self, now: float) -> None:
        """Notice the operator saving the Settings tab, without being restarted.

        This process is deliberately separate from the console and deliberately
        outlives the window, which is what makes it unreachable: the next
        console adopts this same process with the configuration it started with,
        so "close it and open it again" changes nothing. That left the storage
        budget, the retention policy, the segment length and the recording
        folder unfixable by an operator who has no terminal.

        The file's timestamp is the trigger rather than the clock. The ordinary
        pass then costs one stat() and nothing else, which is what it has to
        cost on a loop that runs every five seconds for months. Exiting and
        letting somebody restart this was the other candidate and is not
        available: the thing that would restart it is the console, and the
        console is allowed to be closed.
        """
        if self.settings_path is None:
            return
        stamp = self._settings_timestamp()
        if stamp is None:
            # No file to read. `save_settings` writes a temporary file and
            # renames it over this one, and os.replace never leaves the
            # destination absent, so this is not the moment of a save - it is a
            # file that has been moved, deleted, or put on a disk that went
            # away. Whatever the reason, the settings in hand are the last ones
            # the operator chose, and recording on those beats recording on the
            # defaults or not recording at all.
            return
        if stamp == self._settings_stamp:
            return
        # Remembered before the load and whether or not the load works, so that
        # a file which cannot be read is complained about once per version of
        # itself rather than once every five seconds for as long as it is broken.
        self._settings_stamp = stamp
        try:
            fresh = load_settings(self.settings_path)
        except SettingsError as exc:
            logger.error(
                "the settings could not be read, so recording continues exactly "
                "as it is on the last settings that worked: %s",
                exc,
            )
            return
        self._apply_settings(fresh, now)

    def _apply_settings(self, fresh: Settings, now: float) -> None:
        """Take the new settings, restarting only as much as has to restart.

        Most of them are live the moment `self.settings` is replaced: the
        retention pass reads the budget, the age rule and the warning threshold
        out of it every time it runs. The three that are not are the ones that
        were baked into something already running - the folder each ffmpeg
        writes into, the length of the segments it cuts, and which streams have
        an ffmpeg at all.

        The camera's address is deliberately not one of them. Recording pulls
        from the local streaming server keyed by stream name, so a corrected
        address reaches the disk through go2rtc without this process moving.
        """
        previous = self.settings
        self.settings = fresh

        old_root = Path(previous.storage.root).resolve()
        new_root = Path(fresh.storage.root)
        if new_root.resolve() != old_root:
            self._move_archive(new_root, now)
            return

        renamed = self._enabled_names(fresh) != self._enabled_names(previous)
        relengthened = fresh.storage.segment_seconds != previous.storage.segment_seconds
        if renamed or relengthened:
            self._rebuild_recorders(
                "the streams being recorded changed"
                if renamed
                else f"the segment length changed to {fresh.storage.segment_seconds}s",
                now,
            )
            return
        logger.info("settings reloaded; the storage rules now in force are the saved ones")

    @staticmethod
    def _enabled_names(settings: Settings) -> list[str]:
        return [s.name for s in settings.camera.streams if s.enabled]

    def _rebuild_recorders(self, reason: str, now: float) -> None:
        """Stop every recorder, keep what it wrote, and start the new set.

        Stopping first is not optional. Each ffmpeg has a file open; the file is
        only finished once its process is gone, and it is only findable
        afterwards if it is indexed before this object forgets about it.
        """
        logger.info("%s; restarting the recorders", reason)
        self.supervisor.stop_all()
        try:
            self._index_new_segments(now)
            self._index_final_segments()
        except Exception:  # noqa: BLE001 - a lost index row must not stop recording
            logger.exception("indexing before the restart failed")
        self._build_recorders()

    def _move_archive(self, new_root: Path, now: float) -> None:
        """Point everything at a new recording folder, losing nothing on the way.

        The order is the whole of it. Every recorder is stopped so the segment
        it had open is closed and complete; those segments are indexed into the
        *old* folder's catalogue, which is the only one that knows where they
        are; only then is that catalogue closed and a new one opened beside the
        new folder.

        Retention is never left holding one folder's catalogue and the other's
        rules. `self.index` and `self.settings.storage` are swapped together and
        the retention clock is reset, so the next pass reads the new folder's
        contents and measures them against the new folder's budget. Nothing in
        the old folder is deleted - the operator moved the archive, they did not
        ask for the old one to be thrown away - and nothing there is managed
        from here any more, which is said out loud because it is the sort of
        thing an operator finds out months later otherwise.
        """
        logger.warning(
            "the recording folder changed from %s to %s. Everything already "
            "recorded stays in the old folder, with its own index, and is no "
            "longer deleted or counted by this service",
            self.root,
            new_root,
        )
        self.supervisor.stop_all()
        try:
            self._index_new_segments(now)
            self._index_final_segments()
        except Exception:  # noqa: BLE001 - the move must complete either way
            logger.exception("the last segments in %s could not be indexed", self.root)
        self._close_stores()
        # Before the root moves. A report left in the folder that was left goes
        # on being read as this recorder's current state by anything pointed at
        # it, and it says the streams were fine.
        self.clear_status()

        self.root = Path(new_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = SegmentIndex(self.root / "segments.db")
        # A fresh catalogue in a fresh folder. Whatever the last one had done
        # is not this one's record: carrying the failure count across would
        # have the first blip here counted as the fourth.
        self._index_failures = 0
        self._index_reopens = 0
        self._index_broken = None
        self._index_gave_up_at = None
        self._said_index_broken = False
        # The clock witness belongs to the archive it protects, so a folder that
        # has never been managed from here starts its own record rather than
        # inheriting the last one's.
        self._clock = ClockWatch(self.root / CLOCK_FILENAME)
        self._clock_verdict = None
        self._last_clock_look = 0.0
        self._retention_declined = None
        self._declined_said = 0
        self.events_path = self.root / EVENTS_FILENAME
        self.status_path = self.root / STATUS_FILENAME
        self._events = None
        self._seen = {s.path for s in self.index.all()}
        # This folder's totals have never been measured, and waiting out the
        # retention interval before measuring them would leave the budget
        # unenforced on a folder that may already be full.
        self._last_retention = 0.0
        self._last_warning = None
        self._stuck_deletions = 0
        self._build_recorders()
        self._adopt_orphans()

    def _close_stores(self) -> None:
        if self._events is not None:
            try:
                self._events.close()
            except Exception:  # noqa: BLE001 - closing must not fail a close
                logger.exception("the movement events would not close")
            self._events = None
        try:
            self.index.close()
        except Exception:  # noqa: BLE001 - a dead connection must not fail a close
            logger.debug("the catalogue connection would not close", exc_info=True)

    def _index_final_segments(self) -> None:
        """Index the file each recorder still had open, now that it has none.

        `_index_new_segments` cannot do this and must not try. While recording
        runs, the newest file in a directory is the one ffmpeg is writing, so
        find_closed_segments always leaves it alone and waits for the settle
        window - which is right, because indexing a live recording would expose
        it to retention. Both of those guards are also why the last segment of
        every run used to be left behind: it is by definition the newest file
        and it was touched a moment ago, so it failed both tests, and stop()
        claimed to have indexed it while doing nothing of the kind. It stayed on
        disk invisible to the storage budget and missing from the Playback
        timeline until some later run happened to write a newer file beside it -
        and for a stream that is never recorded again, for ever.

        Once stop_all() has returned, the question the guards exist to answer is
        settled: `running` is False only when the process is confirmed dead, so
        nothing in that directory is open and every file in it is finished. A
        recorder that survived the kill keeps `running` True and is skipped, for
        exactly the reason the guards existed.
        """
        for recorder in self.recorders:
            if recorder.running:
                logger.warning(
                    "%s did not stop, so its directory is left unindexed until it does",
                    recorder.stream,
                )
                continue
            try:
                paths = sorted(recorder.output_dir.glob("*.mp4"))
            except OSError:
                continue
            starts = segment_starts(recorder.output_dir)
            for path in paths:
                if str(path) in self._seen:
                    continue
                start = parse_segment_start(path.name)
                if start is None:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size == 0:
                    continue  # ffmpeg opened it and wrote nothing; there is no footage
                self.index.add(
                    stream=recorder.stream,
                    path=str(path),
                    start=start,
                    end=self._end_of(
                        start, stat.st_mtime, next_segment_start(starts, start)
                    ),
                    size_bytes=stat.st_size,
                    commit=False,
                )
                self._seen.add(str(path))
                logger.info("indexed the final segment %s", path.name)
        self.index.commit()

    @staticmethod
    def _end_of(start: float, mtime: float, following: float | None) -> float:
        """When this segment stops covering time.

        Its close time as the filesystem recorded it, except that it may never
        run past where the next segment begins. The two ends come from the same
        clock but not at the same resolution: `start` is read out of the
        filename, which carries whole seconds, while the close time carries the
        fraction of a second as well. ffmpeg closes one file and opens the next
        in the same instant, so every consecutive pair claimed the fraction
        twice - measured over a real run at a mean of 0.463 s of double coverage
        per boundary, on all twenty of twenty pairs.

        Nothing was visibly harmed by it, because seeking takes the first
        segment that matches. But the index is the thing every later question is
        answered from, and a gap detector, a total duration or a coverage figure
        all inherit an error that accumulates once per segment for months.

        Clamped rather than truncating both ends to whole seconds. Truncation
        would turn the overlap into a phantom one-second gap whenever a file
        happened to be closed just before a second boundary and its successor
        opened just after it, and a coverage timeline that invents gaps is worse
        than one that invents overlaps - a gap is the thing an operator is meant
        to act on.

        A segment with no successor keeps its measured close time, which is the
        truth about it: that is the last segment of a run, and the segment whose
        successor was never written because the recorder died. The file ffmpeg
        currently has open is never indexed at all, but its name is still read,
        because it is what says where its predecessor stopped.
        """
        end = max(mtime, start)
        if following is not None and following >= start:
            end = min(end, following)
        return end

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

    def _restart_stalled(self, now: float) -> None:
        """Stop any stream that is alive but producing nothing, so it gets restarted.

        The supervisor only restarts a recorder whose process has exited. A recorder
        blocked on a dead RTSP socket reports itself as running indefinitely, so
        without this it would never recover on its own - detection alone would leave
        the stream dead until somebody looked at a dashboard.
        """
        limit = 2 * self.settings.storage.segment_seconds
        for stream in self.stalled_streams(now):
            recorder = next((r for r in self.recorders if r.stream == stream), None)
            if recorder is None:
                continue
            logger.warning(
                "%s is alive but has produced no segment for over %.0fs; restarting it",
                stream,
                limit,
            )
            recorder.stop()
            # Give the restarted recorder a fresh grace period, or it would be judged
            # stalled again before it has had time to write anything.
            self._started_at[stream] = now
            self._last_segment_at.pop(stream, None)
            self._stall_restarts += 1

    def _adopt_orphans(self) -> None:
        """Index segments already on disk that no current recorder is responsible for.

        Renaming or disabling a stream leaves its recordings behind. Without this they
        occupy the storage budget forever while being invisible to retention.

        Directories belonging to a currently configured recorder are deliberately
        skipped. Those are handled by _index_new_segments, which uses
        find_closed_segments and therefore never touches the file ffmpeg still has
        open. Sweeping them here would index the in-progress segment and expose a live
        recording to retention.

        Every file in an unowned directory is considered, the newest included.
        Skipping the newest one is right for a running stream - the next file
        written beside it proves it closed, and _index_new_segments picks it up
        then - and wrong here, because in a directory nobody records into any
        more no newer file will ever be written. That last segment used to hold
        the drive while being invisible to the disk budget, to retention and to
        the Playback timeline, for ever. Renaming or disabling a stream is the
        ordinary way to reach that state, and the console restarts the recorder
        precisely when the stream set changes, so it is reached on purpose.

        What replaces "skip the newest" is the question it was standing in for:
        does anything still have the file open. See `held_open`.
        """
        owned = {recorder.stream for recorder in self.recorders}
        for directory in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if directory.name in owned:
                continue
            candidates = []
            for path in directory.glob("*.mp4"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size == 0:
                    continue
                candidates.append((stat.st_mtime, path, stat))
            if not candidates:
                continue
            candidates.sort()
            starts = segment_starts(directory)
            for mtime, path, stat in candidates:
                if self._still_being_written(path, mtime):
                    continue
                if str(path) in self._seen:
                    continue
                start = parse_segment_start(path.name)
                if start is None:
                    continue
                self.index.add(
                    stream=directory.name,
                    path=str(path),
                    start=start,
                    end=self._end_of(start, mtime, next_segment_start(starts, start)),
                    size_bytes=stat.st_size,
                    commit=False,
                )
                self._seen.add(str(path))
                logger.info("adopted orphaned segment %s", path.name)
        self.index.commit()

    def _still_being_written(self, path: Path, mtime: float) -> bool:
        """Whether this file may still be growing, erring towards yes.

        A segment indexed while it is being written carries a truncated
        duration, and is offered to retention before it is finished - both worse
        than one indexed a minute late, so anything unproven is left alone.

        Asked of the handles first, which is a direct answer, and of the clock
        only when there are no handles to ask about. A file left alone here is
        not lost: the next time this service starts it is asked about again.
        """
        held = held_open(path)
        if held is not None:
            return held
        return time.time() - mtime < max(self.settle_seconds, 0.0)

    def _apply_retention(self, now: float) -> None:
        # Retention reads the entire index, which is expensive once the catalogue is
        # large. Its input only changes when a segment closes, so running it on the
        # 5-second loop cadence would be pure waste.
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
        # The whole catalogue has just been read. See _index_worked.
        self._index_worked()
        plan = plan_retention(
            segments,
            now=now,
            budget_bytes=storage.budget_bytes,
            budget_enabled=storage.budget_enabled,
            retention_days=storage.retention_days,
            warn_at_fraction=storage.warn_at_fraction,
            bytes_per_second=self._write_rate(segments),
            clock_reason=self._clock_verdict.reason if self._clock_verdict else "",
        )
        # Declining to delete has to be as visible as deleting would have been.
        # Silence here reads exactly like a rule that ran and found nothing to
        # do, and this one runs every minute for months, so it is said the first
        # few times and rarely after that - the Logs tab holds five hundred
        # lines and a sentence repeated every minute destroys it.
        self._retention_declined = plan.declined
        if plan.declined:
            self._declined_said += 1
            if self._declined_said <= 3 or self._declined_said % 100 == 0:
                logger.error("%s", plan.declined)
        else:
            self._declined_said = 0
        # `warning` is the operator-facing line, and there is no point having
        # one channel say the disk is filling while another, unread, says the
        # reason nothing is being reclaimed from it.
        self._last_warning = plan.warning or plan.declined
        if plan.warning:
            logger.warning(plan.warning)
        # The events go with the footage they point at, or the movement list
        # ends up offering to play files that were reclaimed months ago.
        removed = apply_plan(plan, self.index, events=self._event_store())
        for segment in removed:
            self._seen.discard(segment.path)
        if removed:
            logger.info("retention removed %d segments", len(removed))

        # A file that cannot be deleted is retried forever. Counting only what was
        # removed would report a healthy number every pass while the budget is never
        # actually met, so the shortfall is tracked and surfaced in status().
        self._stuck_deletions = len(plan.delete) - len(removed)
        if self._stuck_deletions:
            logger.warning(
                "%d segment(s) could not be deleted; storage budget cannot be met",
                self._stuck_deletions,
            )

    def _watch_the_clock(self, now: float) -> None:
        """Hold the wall clock against one that cannot be set by hand.

        Run before indexing and before retention, because both of them act
        irreversibly on timestamps: retention deletes footage by age, and the
        index records what a file is from the moment it was written. On the same
        cadence as retention, and not the five-second one, because it writes a
        witness to disk - and because two passes agreeing about the time has to
        mean a couple of minutes, not ten seconds, if it is to outlast somebody
        mistyping a date and correcting it.
        """
        elapsed = now - self._last_clock_look
        if self._last_clock_look and 0 <= elapsed < self.retention_interval:
            return
        self._last_clock_look = now
        verdict = self._clock.observe(now)
        self._clock_verdict = verdict
        # Enough to have put ffmpeg back over names it has already used. A
        # smaller step cannot: consecutive names are a segment apart.
        if verdict.jumped < -max(60.0, float(self.settings.storage.segment_seconds)):
            self._recheck_what_is_on_disk(verdict)

    def _recheck_what_is_on_disk(self, verdict) -> None:
        """After the clock moved backwards, stop believing the catalogue.

        Two runs can no longer be given the same filename - each carries its own
        run number; see `split_run`. Within one run they still can: ffmpeg
        builds each name as it opens the file, from a clock that has just been
        set back, and it truncates whatever is already there. Nothing would
        notice, because a path already indexed is skipped by every later pass by
        design - that is what makes the ordinary pass cheap.

        So the memo of what has been indexed is dropped. Every closed file in
        every directory is offered to the index again, once, and `SegmentIndex.add`
        corrects any row that no longer describes its file. It is a directory
        walk per stream, which is why it is done when the clock has moved rather
        than on the five-second pass.

        The count of empty segments goes with it. `_notice_empty_segments` sorts
        by modification time and exempts only the newest file; a clock that
        moved backwards can put the file ffmpeg is holding somewhere other than
        last, where its zero size reads as a broken segment - one permanent
        false error, and an unhealthy status for the life of the process.
        """
        self._clock_jumps += 1
        logger.error(
            "the clock has gone backwards by %.1f days. Recordings written "
            "either side of that carry the same names, so what is on disk is "
            "being checked against the catalogue again, and anything that no "
            "longer matches is corrected",
            -verdict.jumped / 86400.0,
        )
        self._seen = set()
        self._empty_seen = set()
        self._empty_segments = 0

    @staticmethod
    def _write_rate(segments) -> float:
        """Bytes per second, measured from what has actually been recorded.

        This averages over the entire retained history, so a long outage makes the
        figure read low. It feeds only the operator-facing estimate of when footage
        will be deleted, never a deletion decision, so an optimistic estimate after a
        link outage is a display inaccuracy rather than a data-loss risk.
        """
        if len(segments) < 2:
            return 0.0
        span = segments[-1].end - segments[0].start
        if span <= 0:
            return 0.0
        return sum(s.size_bytes for s in segments) / span


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vmd-record", description="VMD recording service")
    parser.add_argument("--settings", default="settings.json", help="path to settings.json")
    parser.add_argument(
        "--streaming",
        default=None,
        help="where the console wrote the streaming server's ports "
        "(default: streaming.json beside the settings)",
    )
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between passes")
    parser.add_argument(
        "--pid-file",
        default=None,
        help="where to record which process is recording "
        "(default: recorder.pid beside the settings)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    endpoint_path = Path(args.streaming) if args.streaming else Path(args.settings).parent / "streaming.json"
    try:
        settings = load_settings(args.settings)
    except SettingsError as exc:
        # A broken settings file must fail with a readable message, not a traceback.
        # Nothing restarts this process, so an unhandled error here means the machine
        # records nothing until somebody notices.
        logger.error("%s", exc)
        return 1
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
    # Claimed before anything is opened, and by the process doing the recording
    # rather than by whoever started it. Two recorders on one folder is the
    # accident this prevents: they would fight over the same segment files and
    # write the same SQLite index from two processes, and the console would
    # report both of them as healthy.
    pid_path = (
        Path(args.pid_file) if args.pid_file else Path(args.settings).parent / PID_FILENAME
    )
    holder = claim_recorder(pid_path, RecorderIdentity.for_this_process(args.settings))
    if holder is not None:
        # Not an error. Something started a second recorder - the console, the
        # logon task, an operator - and the right answer is to leave the one
        # that is already recording alone and say which one it is.
        message = (
            f"a recorder is already running (pid {holder}); "
            f"leaving it alone rather than recording the same folder twice"
        )
        logger.info("%s", message)
        print(message)
        return ALREADY_RECORDING_EXIT

    # The path, not only the loaded settings: this process outlives the console
    # window, so re-reading this file is the only way the Settings tab can ever
    # reach it.
    try:
        service = RecordingService(
            settings, endpoint_path=endpoint_path, settings_path=args.settings
        )
    except Exception:
        release_recorder(pid_path)
        raise

    try:
        if args.once:
            try:
                service.run_once()
                print(service.status())
            finally:
                service.stop()
            return 0
        service.run_forever(interval=args.interval)
        return 0
    finally:
        # A recorder killed with taskkill /F never reaches this, which is why
        # the claim also has to be recognisable as stale from the outside.
        release_recorder(pid_path)


if __name__ == "__main__":
    raise SystemExit(main())
