"""The processes the window looks after, and the state it reports about them.

Recording does not belong to the window. It is a separate process so that a
crash in the video pane, or the operator closing the window, cannot stop the
disk filling - which was the first requirement this system was given.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from vmd.background import BackgroundValue
from vmd.desktop.disk import DiskWatcher
from vmd.settings import Settings
from vmd.streaming.endpoint import is_live, read_endpoint
from vmd.streaming.go2rtc import Go2rtcService
from vmd.supervisor import Managed, Supervisor

logger = logging.getLogger(__name__)

# How long the recorder tree gets to disappear after taskkill has been told to
# end it. It is already a forced kill, so this is only the time the kernel needs.
TREE_STOP_SECONDS = 10.0

# How long stop() will wait for a child's output reader to notice the pipe has
# ended. Short, because stop() runs on the GUI thread while the window closes,
# and the reader is a daemon thread that costs nothing if it is abandoned.
READER_STOP_SECONDS = 2.0

# How long a forced stop waits for an adopted child to disappear. Short, because
# it runs on the GUI thread while the operator waits for a Save to finish, and
# because taskkill /F has already returned by the time it is checked at all.
ADOPTED_STOP_SECONDS = 2.0

# How often "is that adopted child still there?" is asked again. Asking costs a
# `tasklist`, which is about 150 ms, and it used to be paid on the GUI thread
# every time the status line was drawn - which is every heartbeat, for every
# adopted child. One heartbeat's worth of staleness is the most the answer can
# be behind, which for a question whose answer changes at most once is nothing.
LIVENESS_SECONDS = 2.0

# And how old that answer may get before the console stops implying it knows.
# Several heartbeats: a `tasklist` that has not come back in this long is a
# machine in trouble, and reporting a child as running on the strength of a
# check from a quarter of a minute ago is the console inventing health.
LIVENESS_UNANSWERED_SECONDS = 15.0

# How far the start time recorded in a PID file may differ from the one the
# operating system reports before the process is treated as a stranger. A second
# is generous: both numbers come from the same call, and the only thing between
# them is a float going through JSON.
PID_START_TOLERANCE_SECONDS = 1.0

# The longest single line kept from a child. A child in a restart loop, or an
# ffmpeg dumping a binary probe, can write a great deal without a newline, and
# the ring buffer's capacity is no defence against one line that is a megabyte.
CHILD_LINE_LIMIT = 2000

# How much is taken off a child's pipe at a time. Small enough that a line is
# logged while it still matters, large enough not to syscall per byte.
CHILD_READ_CHUNK = 8192

# Where the detector publishes what each stream is doing, beside events.db in
# the recording root. The name is repeated here rather than imported for the
# same reason `detection_enabled` repeats its rule: importing `vmd.detect_main`
# would pull cv2, numpy and eventually the classifier's weights into the
# window's process, which must open on a laptop where none of that is installed.
DETECTION_STATUS_FILENAME = "detection.json"

# How old that file may be before the console stops believing it. The detector
# rewrites it every `interval` seconds - five by default - so thirty seconds is
# six missed writes: long enough that a laptop busy encoding four streams, or a
# write that lost a rename race with a reader, does not make the console cry
# wolf, and short enough that a wedged detector is reported within a handful of
# heartbeats rather than minutes. A detector told to report less often is given
# four of its own intervals instead, so raising --interval cannot silently turn
# this into "permanently stale".
DETECTION_STATUS_STALE_SECONDS = 30.0
DETECTION_STATUS_STALE_INTERVALS = 4

# How a child that will not stay up is recognised: more than this many restarts
# inside this window and the console stops calling it running. Two minutes is
# long enough that a single restart plus a slow start does not trip it, and
# short enough that the operator hears about it while it matters.
#
# One rule for both children, not two copies of it. The recorder is the more
# important of the two and had none at all: the status line said "recording" or
# "NOT recording" and never "restarted twenty times in the last two minutes",
# which is the state a recorder pointed at an unwritable folder is actually in.
FLAP_WINDOW = 120.0
FLAP_LIMIT = 3

# The names detection was written with, kept pointing at the shared rule.
DETECTION_FLAP_WINDOW = FLAP_WINDOW
DETECTION_FLAP_LIMIT = FLAP_LIMIT


def detection_enabled(settings: Settings) -> bool:
    """Has anyone actually asked for detection?

    The same rule as `vmd.detect_main.detected_streams`, spelled out again
    rather than imported: importing the detector package here would pull cv2,
    numpy and eventually the classifier's weights into the window's process,
    which must open on a laptop where none of that is installed.
    """
    if not settings.detection.enabled:
        return False
    return any(stream.enabled and stream.detect for stream in settings.camera.streams)


def recordable(settings: Settings) -> bool:
    """Is there anything for the recorder to record?

    The same shape as `detection_enabled`, and for the same reason.
    `vmd.record_main` prints "no enabled streams; nothing to record" and exits 1
    when no stream is ticked with an address, so a supervisor holding it
    respawns that exit every two seconds for the life of the console: measured
    on an unconfigured machine, eleven spawns in thirty seconds, which is about
    forty-three thousand processes overnight while the operator sleeps before
    setting the camera up in the morning.

    An address as well as the tick, because a stream ticked to record with an
    empty address is not something to record either - that is the ordinary state
    of a machine part way through being configured.
    """
    return any(stream.enabled and stream.url for stream in settings.camera.streams)


# --------------------------------------------------------- what is "material"
#
# Each child reads settings.json once, when it starts, and holds what it read
# for as long as it lives. So a saved setting reaches a running child only by
# restarting it - and restarting a child costs a gap in what it was doing, which
# for the recorder is a gap in the footage. Both halves of that matter: a save
# the operator made must take effect, and a save that changed nothing that child
# reads must cost nothing at all. Clicking Save twice must not cost two
# recording gaps.
#
# So each child has a fingerprint of exactly the settings it reads. Same
# fingerprint, no restart. Anything not listed here is either read by the
# console itself (the radio, the camera's PTZ address, the link ceiling) or read
# live from somewhere other than settings.json.


def streaming_fingerprint(settings: Settings) -> tuple:
    """What go2rtc's configuration is made of - see `build_config`.

    The credentials and, per stream, its name, address, tick and reader. The
    camera's `host` is deliberately absent: it is where the PTZ and the radio
    services are pointed, and it appears nowhere in go2rtc's config, which
    addresses each stream by its own URL. Restarting the video for it would cost
    the picture, and through go2rtc the recorder's source, for nothing.
    """
    return (
        settings.camera.username,
        settings.camera.password,
        tuple(
            (stream.name, stream.url, stream.enabled, stream.reader)
            for stream in settings.camera.streams
        ),
    )


def recorder_fingerprint(settings: Settings) -> tuple:
    """What `vmd.record_main` reads at startup and never re-reads.

    The folder, the segment length, every retention rule, and which streams it
    is to record. The retention rules are in here even though changing one costs
    a recording gap: the operator who lowers the budget has explicitly asked for
    less footage to be kept, and a budget that quietly does not apply until the
    laptop is rebooted is a disk that fills.
    """
    storage = settings.storage
    return (
        str(storage.root),
        storage.segment_seconds,
        storage.budget_gb,
        storage.budget_enabled,
        storage.retention_days,
        storage.warn_at_fraction,
        tuple(
            (stream.name, stream.url, stream.enabled)
            for stream in settings.camera.streams
        ),
    )


def detector_fingerprint(settings: Settings) -> tuple:
    """What `vmd.detect_main` reads at startup and never re-reads.

    The master switches, and per stream everything that shapes what it reports:
    whether it is watched at all, how touchy, whether it is the heat camera,
    whether to name what moved, the sky line and the ignore mask. The recording
    root as well, because events.db lives in it.
    """
    detection = settings.detection
    return (
        str(settings.storage.root),
        detection.enabled,
        detection.classify,
        detection.min_travel_px,
        tuple(
            (
                stream.name,
                stream.url,
                stream.enabled,
                stream.detect,
                stream.sensitivity,
                stream.thermal,
                stream.classify,
                stream.horizon_y,
                tuple(region.as_tuple() for region in stream.ignore_regions),
            )
            for stream in settings.camera.streams
        ),
    )


def read_child_output(stream, emit: Callable[[str], None]) -> None:
    """Read a child's pipe to the end, handing whole lines to `emit`.

    Bytes rather than text, and decoded here on purpose. A child writes things
    that are not valid UTF-8 more often than not: ffmpeg puts filenames in the
    machine's own code page, and any chunk can end half way through a multi-byte
    character. Decoding with `replace` turns both into a readable line instead of
    a UnicodeDecodeError that ends the reader and takes the Logs tab silent for
    the rest of the run.

    Chunked rather than `for line in stream`, because a line-oriented reader
    accumulates whatever the child writes until a newline arrives - and a child
    that never sends one has the console holding all of it. `CHILD_LINE_LIMIT`
    caps that, so the memory this can hold is bounded by the limit and not by
    the child, which is what makes the ring buffer's capacity mean anything.

    A child that dies mid-line still said something: whatever is left when the
    pipe ends is emitted rather than dropped, because a half-written line is
    usually the most interesting thing the child ever said.
    """
    # read1 where it exists (a buffered pipe, a BytesIO) so a partial chunk is
    # returned as soon as it arrives; read() on a buffered stream would block
    # until the whole chunk was filled, which for a quiet child is for ever.
    read = getattr(stream, "read1", None) or stream.read
    pending = bytearray()
    # True while the tail of an over-long line is being thrown away: it has
    # already been reported as truncated, and the rest is not worth holding.
    discarding = False

    while True:
        try:
            data = read(CHILD_READ_CHUNK)
        except (OSError, ValueError):
            # The pipe was closed under us - the ordinary end of a child that
            # was killed. Not a failure worth a traceback.
            return
        if not data:
            break
        pending.extend(data)

        while True:
            end = pending.find(b"\n")
            if end < 0:
                break
            line = bytes(pending[:end])
            del pending[: end + 1]
            if discarding:
                discarding = False
                continue
            _emit_line(line, emit)

        if len(pending) > CHILD_LINE_LIMIT:
            _emit_line(bytes(pending[:CHILD_LINE_LIMIT]), emit, truncated=True)
            pending.clear()
            discarding = True

    if pending and not discarding:
        _emit_line(bytes(pending), emit)


def _emit_line(raw: bytes, emit: Callable[[str], None], truncated: bool = False) -> None:
    text = raw.decode("utf-8", "replace").rstrip()
    if len(text) > CHILD_LINE_LIMIT:
        text = text[:CHILD_LINE_LIMIT]
        truncated = True
    if truncated:
        text += " ... (line truncated)"
    if not text.strip():
        return
    emit(text)


def child_log_level(text: str) -> int:
    """How loudly to repeat one line from a child.

    The children log with `%(levelname)s` in the format, so the word is in the
    line and can be honoured - which is what makes the Logs tab's "warnings and
    errors" button work on the children as well as on the console itself. The
    two extra words are go2rtc's: "401 Unauthorized" is the line this whole
    change exists for, and it is not tagged as an error by anything.
    """
    upper = text.upper()
    if "CRITICAL" in upper or "ERROR" in upper or "TRACEBACK" in upper:
        return logging.ERROR
    if "WARN" in upper or "UNAUTHORIZED" in upper or "401" in upper:
        return logging.WARNING
    return logging.INFO


class ChildProcess:
    """One `python -m <module>` child, shaped to fit the supervisor's protocol.

    A PID file makes the process findable across window lifetimes. These
    children are meant to outlive the window, which means the next window must
    be able to tell "already running" from "not running" - otherwise it starts a
    second one on the same directory, and two of them fight over the same files
    and the same database.

    Subclasses say which module they run and what their PID file is called. The
    two must never coincide: a shared PID file would have each child adopt the
    other and neither would ever be started.
    """

    module = ""
    pid_filename = ""
    label = ""

    def __init__(
        self,
        settings_path: str | Path,
        pid_path: str | Path | None = None,
        spawn=None,
        kill_tree=None,
        alive=None,
    ) -> None:
        self.settings_path = Path(settings_path)
        self.pid_path = (
            Path(pid_path) if pid_path else self.settings_path.parent / self.pid_filename
        )
        self._spawn = spawn or _default_spawn
        self._kill_tree = kill_tree or _taskkill_tree
        self._alive = alive or _pid_alive
        self._process: subprocess.Popen | None = None
        self._adopted_pid: int | None = None
        # Whether that adopted PID is still alive, kept off the GUI thread. See
        # `_watch_adopted`; None whenever nothing has been adopted.
        self._adopted_alive: BackgroundValue[bool] | None = None
        self._output_thread: threading.Thread | None = None
        # Its own name in the Logs tab, exactly as go2rtc has one, so a line
        # from the recorder is distinguishable from a line about the recorder.
        self._child_logger = logging.getLogger(self.label or "child")

    @property
    def output_thread(self) -> threading.Thread | None:
        """The reader for this child's pipe, for anyone who has to wait on it."""
        return self._output_thread

    @property
    def running(self) -> bool:
        """Whether this child is up. Never waits for the operating system.

        A child this console spawned is a poll() away. An adopted one is a PID
        and nothing else, and on Windows the only way to ask about a PID is to
        shell out to `tasklist` - about 150 ms, paid on the GUI thread, on every
        heartbeat, for every adopted child. So that question is asked on a
        thread of its own and this answers from what it last said.

        An unanswerable question is deliberately read as "still there". The two
        ways of being wrong are not symmetrical: believing a live recorder is
        gone starts a second one on the same directory and the same index, which
        is the collision adoption exists to prevent.
        """
        if self._process is not None:
            return self._process.poll() is None
        if self._adopted_pid is None:
            return False
        watch = self._adopted_alive
        if watch is None:  # pragma: no cover - adoption always sets one up
            return self._alive(self._adopted_pid)
        reading = watch.get()
        return True if reading.value is None else bool(reading.value)

    def liveness_age(self) -> float | None:
        """How long ago it was last confirmed that an adopted child is there.

        None when there is nothing adopted to ask about - which is the ordinary
        case, and is not the same as "nobody has checked".
        """
        watch = self._adopted_alive
        if self._process is not None or self._adopted_pid is None or watch is None:
            return None
        return watch.get().age

    def _watch_adopted(self, pid: int) -> None:
        """Start asking, off-thread, whether that PID is still there.

        Seeded true: the caller has just checked it synchronously, once, which
        is a reading and not a guess.
        """
        self._forget_adopted()
        self._adopted_alive = BackgroundValue(
            read=lambda: self._alive(pid),
            stale_after=LIVENESS_SECONDS,
            name=f"whether the adopted {self.label} is still running",
            seed=True,
        )

    def _forget_adopted(self) -> None:
        watch, self._adopted_alive = self._adopted_alive, None
        if watch is not None:
            watch.close()

    def start(self) -> None:
        if self.running:
            return

        adopted, recorded_start = self._read_pid()
        if adopted is not None and self._alive(adopted) and self._is_ours(adopted, recorded_start):
            logger.info("a %s is already running (pid %s); adopting it", self.label, adopted)
            self._adopted_pid = adopted
            self._watch_adopted(adopted)
            self._announce_adoption(adopted)
            return
        self._adopted_pid = None
        self._forget_adopted()

        command = [
            sys.executable,
            # Unbuffered. Python block-buffers stdout when it is a pipe, so
            # without this the operator watches an empty Logs tab while the
            # child fills eight kilobytes - which for a recorder saying one line
            # a minute is most of an hour.
            "-u",
            "-m",
            self.module,
            "--settings",
            str(self.settings_path),
        ]
        try:
            self._process = self._spawn(command)
        except OSError:
            logger.exception("could not start the %s", self.label)
            self._process = None
            return
        self._write_pid()
        self._read_output(self._process)
        logger.info("%s started", self.label)

    def restart(self, why: str) -> bool:
        """Stop this child - adopted or not - and start a fresh one.

        Returns whether it is running afterwards, so that a Save can tell the
        operator the truth rather than reporting settings as live when they are
        not. Every line it writes goes to the Logs tab, which on this machine is
        the only place the operator can read anything: the restart is
        deliberate, and a gap in the footage that nobody explained looks exactly
        like a fault.
        """
        self._child_logger.warning(
            "%s: restarting it because %s. It was still running the settings "
            "that have just been replaced, so there is a short gap while it "
            "comes back.",
            self.label,
            why,
        )
        try:
            self.stop(force=True)
        except Exception:  # noqa: BLE001 - a Save must not throw back into the button
            logger.exception("the %s would not stop", self.label)
        if self.running:
            self._child_logger.error(
                "%s: it would not stop, so the settings you saved are NOT in effect",
                self.label,
            )
            return False
        try:
            self.start()
        except Exception:  # noqa: BLE001 - the file is saved either way
            logger.exception("the %s would not start", self.label)
        if not self.running:
            self._child_logger.error(
                "%s: it did not come back after being restarted. It is NOT "
                "running and the settings you saved are NOT in effect.",
                self.label,
            )
            return False
        self._child_logger.info(
            "%s: running again, with the settings you saved", self.label
        )
        return True

    def _announce_adoption(self, pid: int) -> None:
        """Say that this child's output cannot be shown, rather than showing none.

        An adopted child was started by a console that has since closed, and its
        pipes went with it - there is nothing here to read. Silence in the Logs
        tab would read as "the recorder has nothing to say", which is the
        opposite of the truth and the wrong thing to act on.
        """
        self._child_logger.warning(
            "%s: adopted from an earlier run (pid %s) - it is running and recording, "
            "but its output goes to the console that started it, so no further "
            "output from it can be shown here",
            self.label,
            pid,
        )

    def _read_output(self, process) -> None:
        """Pump this child's pipe into the log, on a daemon thread.

        A thread rather than anything cleverer because the read has to block:
        there is no portable non-blocking read of a pipe on Windows, selectors
        do not take pipe handles, and asyncio would mean an event loop inside a
        Qt application. One thread per child, blocked on a read almost always,
        costs a stack and nothing else.

        Daemon, because these children outlive the window on purpose and their
        readers must not hold the interpreter open behind a console that closed.
        A pipe nobody reads eventually fills and blocks the child, so this is
        not optional once stdout is a pipe.
        """
        stream = getattr(process, "stdout", None)
        if stream is None:
            return

        def pump() -> None:
            try:
                read_child_output(stream, self._log_line)
            except Exception:  # noqa: BLE001 - a reader must never take the console with it
                logger.debug("the %s output reader stopped", self.label, exc_info=True)
            finally:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001 - closing must not fail a close
                    pass

        thread = threading.Thread(target=pump, name=f"{self.label}-log", daemon=True)
        self._output_thread = thread
        thread.start()

    def _log_line(self, text: str) -> None:
        self._child_logger.log(child_log_level(text), "%s: %s", self.label, text)

    def wait_for_output(self, timeout: float = READER_STOP_SECONDS) -> bool:
        """Wait for this child's reader to reach the end of the pipe.

        Bounded always, and its answer is a bool rather than an exception: the
        caller is either a closing window, which cannot afford to wait, or a
        test, which must fail rather than hang.
        """
        thread = self._output_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _is_ours(self, pid: int, recorded_start: float | None) -> bool:
        """Is the process holding that PID the child this file was written for?

        A live PID is not an answer. After a power cut the PID file survives
        holding a dead PID, and Windows hands PIDs out again from a small pool -
        so an unrelated program can be holding it by the time the console
        starts. The console then adopted a stranger, reported "recording", and
        never started a recorder at all: nothing written, status line green.
        That is the worst failure shape this system has, and it lasts until
        somebody notices the disk is not growing.

        Unverifiable is not the same as "not ours", and is deliberately treated
        the old way. A PID file written by an older console holds a bare number
        with nothing to check against, and a process whose start time this
        console may not read is a question that could not be answered. In both
        cases the alternative to adopting is starting a SECOND child on the same
        recording directory and the same index, which is the collision adoption
        exists to prevent. So it adopts, and says the check could not be made -
        and the recording state, which is now a question about the folder rather
        than about a process, catches a ghost within one poll either way.
        """
        if recorded_start is None:
            self._child_logger.warning(
                "%s: adopting pid %s, but which program holds that number could "
                "not be checked - the PID file was written by an older console",
                self.label,
                pid,
            )
            return True
        actual = process_started_at(pid)
        if actual is None:
            self._child_logger.warning(
                "%s: adopting pid %s, but which program holds that number could "
                "not be verified on this machine",
                self.label,
                pid,
            )
            return True
        if abs(actual - recorded_start) <= PID_START_TOLERANCE_SECONDS:
            return True
        self._child_logger.warning(
            "%s: pid %s is now held by a different program, so the %s from the "
            "last run is gone - probably a power cut. Starting a fresh one.",
            self.label,
            pid,
            self.label,
        )
        return False

    @property
    def started_at_path(self) -> Path:
        """Where the console notes when it spawned this child.

        Beside the PID file rather than in it. That file holds a bare integer
        and must go on holding one: `vmd.record_main.read_pid` parses it with
        int(text.strip()) and scripts\recorder_service.ps1 with [int]::TryParse
        over the whole file, and either of them failing to parse reads as "no
        recorder is running" - whose answer is to start a second recorder on the
        same directory and the same index.

        A suffix of its own, because the recorder keeps what it knows about
        itself in `recorder.pid.json`. That is the recorder answering "which
        interpreter am I"; this is the console answering "when did I spawn it",
        and two processes writing one file is the accident both of them exist to
        avoid.
        """
        return Path(str(self.pid_path) + ".started")

    def _read_pid(self) -> tuple[int | None, float | None]:
        """The PID in the claim file, and when the console saw that child start.

        The start time is only believed when it names the same PID. The recorder
        writes its own claim now, so the number in that file can belong to a
        process this console never spawned, and a note about a different PID
        says nothing at all about this one - which is "unverified", not "wrong".
        """
        try:
            pid = int(self.pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None, None
        try:
            payload = json.loads(self.started_at_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return pid, None
        if not isinstance(payload, dict) or payload.get("pid") != pid:
            return pid, None
        started_at = payload.get("started_at")
        if not isinstance(started_at, (int, float)):
            return pid, None
        return pid, float(started_at)

    def _write_pid(self) -> None:
        pid = getattr(self._process, "pid", None)
        if pid is None:
            return
        try:
            self.pid_path.parent.mkdir(parents=True, exist_ok=True)
            self.pid_path.write_text(str(pid), encoding="utf-8")
        except OSError:
            logger.warning("could not write %s", self.pid_path, exc_info=True)
        try:
            self.started_at_path.write_text(
                json.dumps({"pid": pid, "started_at": process_started_at(pid)}),
                encoding="utf-8",
            )
        except OSError:
            # Not fatal: without it adoption falls back to "unverified", which
            # is where this started.
            logger.warning("could not write %s", self.started_at_path, exc_info=True)

    def stop(self, force: bool = False) -> None:
        """Stop a child this object started, and everything it started.

        The whole tree, not just the process we spawned. The recorder starts an
        ffmpeg per stream, so those are our grandchildren: ending only our own
        child leaves them running, still writing segments into the recording
        directory, with nothing supervising them and no handle to stop them by.

        That is worse than it sounds. The recorder's PID file is then stale, and
        correctly so - the recorder really is gone - so the next window starts a
        fresh one, which writes into the same directory and indexes it with the
        same SQLite database that the orphans are still filling. That is the
        exact collision the PID file and its adoption exist to prevent, reached
        from the other side.

        Terminating the recorder politely first was tried and does not work: on
        Windows terminate() is TerminateProcess, so the `finally` in
        run_forever that stops each ffmpeg never runs, and CTRL_BREAK_EVENT
        cannot reach a child spawned with CREATE_NO_WINDOW because that gives it
        a console of its own, while console control events only reach processes
        sharing the sender's console. Little is lost by being blunt: the
        recorder's own shutdown terminates ffmpeg rather than closing the segment
        cleanly anyway, and the only work skipped is a final indexing pass, which
        the next recorder redoes when it adopts the files left on disk.

        An adopted child is left alone unless `force` is given: it belongs to a
        window that is gone, and stopping it here would stop recording - or
        detection - because someone closed a second window.

        `force` is what a settings change uses, and the distinction is the whole
        point. Adoption exists so that recording survives *the window closing*,
        which is a passive event the operator did not intend as a configuration
        change. A Save is the opposite: an explicit instruction whose entire
        purpose is to change how the system runs. A child running settings the
        operator has just replaced is not a child worth protecting, and a brief
        gap is a far smaller harm than a system that silently ignores its
        operator.
        """
        if self._process is None and self._adopted_pid is not None:
            pid = self._adopted_pid
            if not force:
                self._adopted_pid = None
                self._forget_adopted()
                return
            logger.warning(
                "stopping the adopted %s (pid %s), because the settings it was "
                "started with have been replaced",
                self.label,
                pid,
            )
            self._kill_tree(pid)
            # Bounded, and short. taskkill /F has already returned by the time
            # this runs, so in practice the first check is the only one; the
            # loop is here because this decides whether a second child may be
            # started on the same directory, and that must never be guessed.
            deadline = time.monotonic() + ADOPTED_STOP_SECONDS
            while self._alive(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            if self._alive(pid):
                # Deliberately keeps `_adopted_pid`, so `running` stays True and
                # nothing starts a second one on top of it. Two children on one
                # recording directory is the exact collision adoption exists to
                # prevent, and it is worse than a setting that did not apply.
                logger.error(
                    "the adopted %s (pid %s) would not stop, so nothing was "
                    "started in its place and the settings you saved are NOT in effect",
                    self.label,
                    pid,
                )
                return
            self._adopted_pid = None
            self._forget_adopted()
            return
        process = self._process
        if process is None:
            return
        pid = getattr(process, "pid", None)
        if process.poll() is None and pid is not None and self._kill_tree(pid):
            try:
                process.wait(timeout=TREE_STOP_SECONDS)
            except subprocess.TimeoutExpired:
                logger.warning("the %s outlived taskkill; forcing it", self.label)
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
                    # one on the same directory would fight the first.
                    logger.error("the %s did not stop; leaving it tracked", self.label)
                    return
        self._process = None
        # The reader ends by itself when the pipe does. Waited on briefly so
        # that the last thing a dying child said still reaches the Logs tab,
        # and never for longer than that: this runs on the GUI thread while the
        # window closes, and the reader is a daemon that costs nothing if it is
        # left behind.
        self.wait_for_output(READER_STOP_SECONDS)
        self._output_thread = None


class RecorderProcess(ChildProcess):
    """`python -m vmd.record_main`, kept alive across window lifetimes.

    Recording is meant to outlive the window, so the next window must be able to
    tell "already recording" from "not recording" - otherwise it starts a second
    recorder on the same directory, and two of them fight over the same files
    and the same index.
    """

    module = "vmd.record_main"
    pid_filename = "recorder.pid"
    label = "recorder"


class DetectorProcess(ChildProcess):
    """`python -m vmd.detect_main`, supervised exactly like the recorder.

    Separate from the recorder on purpose, and adopted the same way. It writes
    into events.db; two detectors on one file would each append the same
    movement twice, and the operator would read one intruder as two.

    Stopping it stops detection and nothing else. The two processes share
    nothing but the local stream, which is the whole reason detection was built
    as a process rather than a thread of the console.
    """

    module = "vmd.detect_main"
    pid_filename = "detector.pid"
    label = "detector"


def process_started_at(pid: int) -> float | None:
    """When that process was created, in epoch seconds, or None if unknowable.

    This is what tells a recorder that survived the last console from an
    unrelated program that happens to hold the same number. `_pid_alive` only
    asks whether SOMETHING holds that PID, and after a power cut - the event an
    always-on laptop will certainly see - recorder.pid survives on disk holding
    a dead PID while Windows hands that PID out again from a small pool. The
    console then adopted a stranger, reported "recording", and never started a
    recorder at all.

    ctypes rather than a dependency, and rather than shelling out: `wmic` is
    gone from current Windows, PowerShell costs the better part of a second, and
    this runs while the operator is waiting for a window to open.
    PROCESS_QUERY_LIMITED_INFORMATION is enough for a process of the same user
    and is what a service-style child needs.

    None means the question could not be answered - not that the process is
    gone. The caller treats that as "unverified", never as "not ours".
    """
    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_started_at(pid)
    return _proc_started_at(pid)  # pragma: no cover - not the deployment platform


def _windows_started_at(pid: int) -> float | None:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    # 1601-01-01 to 1970-01-01 in 100-nanosecond ticks, which is the unit every
    # Windows FILETIME is counted in.
    EPOCH_TICKS = 116_444_736_000_000_000
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            if not ok:
                return None
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return (ticks - EPOCH_TICKS) / 10_000_000.0
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - an unanswerable question, not a failure
        logger.debug("could not read the start time of pid %s", pid, exc_info=True)
        return None


def _proc_started_at(pid: int) -> float | None:  # pragma: no cover - not Windows
    """The same answer from /proc, for a development machine that is not Windows."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            fields = handle.read().rsplit(") ", 1)[1].split()
        ticks_after_boot = int(fields[19]) / os.sysconf("SC_CLK_TCK")
        with open("/proc/stat", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("btime "):
                    return int(line.split()[1]) + ticks_after_boot
    except (OSError, IndexError, ValueError):
        return None
    return None


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


def _taskkill_tree(pid: int) -> bool:
    """End a process and every process under it. True if the request was accepted.

    Windows offers no way to ask a console process in another console to shut
    down (see stop()), and no way to reach a grandchild by handle. taskkill /T
    walks the tree itself, which is the only readily available way to be sure the
    ffmpeg processes under the recorder go with it.

    A failure is reported rather than raised: this is an improvement on
    terminate(), not a replacement for it, and stop() falls back to it.
    """
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=TREE_STOP_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("taskkill could not be run for pid %s", pid, exc_info=True)
        return False
    if result.returncode != 0:
        logger.warning(
            "taskkill refused pid %s: %s", pid, (result.stderr or result.stdout).strip()
        )
        return False
    return True


def _creation_flags() -> int:
    """No console window: this runs on an unattended machine the operator watches."""
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]


def _default_spawn(command: list[str]) -> subprocess.Popen:
    """Start a child with its mouth open.

    DEVNULL was the defect, and an invisible one: everything still ran, and the
    operator simply never learned why. This machine is offline and has no
    terminal, so the Logs tab is the only place a child can be heard at all.

    stderr is merged into stdout because `logging.basicConfig` writes to stderr
    and `print` writes to stdout, and both are things the children say that the
    operator needs. Two pipes would need two readers and would interleave badly.

    bufsize=0 makes `process.stdout` a raw pipe, whose read returns what has
    arrived rather than waiting to fill a buffer - the difference between a line
    appearing when it is written and appearing eight kilobytes later.
    """
    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        bufsize=0,
        creationflags=_creation_flags(),
    )


def read_detection_status(path: str | Path) -> dict | None:
    """What the detector last published about each stream, if anything.

    Shaped after `read_endpoint`, which is the same idea for the streaming
    server: anything that is not a usable status file - missing, unreadable, not
    JSON, JSON that is not an object, an object written by an older version
    without the key this needs - is None, and the console reports that as
    unknown. None of them may raise. This is called from the status line, on a
    timer, on the one machine the operator is watching.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        return None
    return payload


def detection_status_fresh(status: dict, now: float) -> bool:
    """Is that file recent enough to believe? Stale means unknown, not healthy.

    A detector that wedged an hour ago left a file saying every stream was fine.
    Repeating that is worse than saying nothing, because it is the answer the
    operator would have wanted to hear.

    A file dated in the future is treated the same way. The laptop's clock is
    set by hand and gets set wrong, and a timestamp from next week is a clock
    that moved rather than a detector that is well.
    """
    try:
        written_at = float(status["written_at"])
    except (KeyError, TypeError, ValueError):
        return False
    try:
        interval = float(status.get("interval") or 0.0)
    except (TypeError, ValueError):
        interval = 0.0
    limit = max(
        DETECTION_STATUS_STALE_SECONDS, DETECTION_STATUS_STALE_INTERVALS * interval
    )
    return -limit <= (now - written_at) <= limit


def stream_reason(streams: list[dict], known: bool) -> str:
    """One line naming the streams that are not being watched, and why.

    Never one health flag. Detection continuing on the thermal while the visible
    is unreachable is a normal Tuesday on a 700 m radio link, and a console that
    called that "detection failed" would be teaching its operator to ignore the
    line that one day says something true - which is the same mistake as
    reporting detection nobody asked for as a fault.

    The reasons are the detector's own words, because they were written to be
    read by an operator on a hill rather than by a developer with the source
    open.
    """
    if not known:
        return "detecting - no recent per-stream report, so which streams are watched is unknown"
    if not streams:
        return "detecting - no stream has reported yet"

    watching = [stream["stream"] for stream in streams if stream.get("opened")]
    blind = [stream for stream in streams if not stream.get("opened")]
    if not blind:
        return "detecting on " + ", ".join(watching)

    trouble = "; ".join(
        f"{stream['stream']} NOT detecting - {stream.get('reason') or 'no reason given'}"
        for stream in blind
    )
    if watching:
        return "detecting on " + ", ".join(watching) + "; " + trouble
    return "detecting, but no stream is open: " + trouble


class ConsoleServices:
    """Everything the window starts and watches."""

    def __init__(
        self,
        settings: Settings,
        settings_path: str | Path,
        streaming: Go2rtcService | None,
        recorder: RecorderProcess,
        detector: DetectorProcess | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], float] = time.time,
        disk: DiskWatcher | None = None,
    ) -> None:
        self.settings = settings
        self.settings_path = Path(settings_path)
        self.streaming = streaming
        self.recorder = recorder
        self.detector = detector
        self.adopted_streaming = False
        # What the recordings folder actually looks like. It is here rather than
        # in the window because it answers a question about the children -
        # whether the recorder is recording anything - as well as one about the
        # disk, and because it must be read on a worker rather than on the GUI
        # thread.
        self.disk = disk if disk is not None else DiskWatcher(settings)
        self._clock = clock
        # Two clocks, deliberately. `clock` is monotonic and measures how long
        # ago this console restarted the detector; `now` is wall clock and is
        # the only kind that can be compared with a timestamp written by another
        # process, which is what the detector's status file carries.
        self._now = now
        self.detection_status_path = (
            Path(settings.storage.root) / DETECTION_STATUS_FILENAME
        )

        # Detection nobody asked for is not supervised at all. `vmd.detect_main`
        # prints "nothing to detect" and exits 0 when no stream is ticked, so a
        # supervisor holding it would respawn that exit every two seconds for
        # the life of the console.
        self.detecting = detector is not None and detection_enabled(settings)
        # And a recorder with nothing to record is not supervised either, for
        # exactly the same reason - see `recordable`.
        self.recording = recordable(settings)

        self.supervisor = Supervisor(self._managed(), clock=clock)

        # When each child was restarted, not just how often since the console
        # opened. A child that died twice in March and is up now is healthy; one
        # that has died four times in the last two minutes is not running,
        # whatever the count since boot says.
        self._restarted_at: dict[str, list[float]] = {}

    def _managed(self) -> list[Managed]:
        """The children the supervisor holds, given what is configured now."""
        managed: list[Managed] = []
        if self.streaming is not None:
            managed.append(Managed(name="streaming", service=self.streaming))
        if self.recording:
            managed.append(Managed(name="recorder", service=self.recorder))
        if self.detecting and self.detector is not None:
            managed.append(Managed(name="detector", service=self.detector))
        return managed

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
                # Same as an adopted recorder: its output belongs to whoever
                # started it. Saying so beats a Logs tab where the one line that
                # explains a stuck picture - go2rtc's "401 Unauthorized" - is
                # simply absent, which reads as nothing having gone wrong.
                logging.getLogger("go2rtc").warning(
                    "go2rtc: adopted from an earlier run - it is serving video, but its "
                    "output goes to whatever started it and cannot be shown here"
                )
                self.streaming.api_port = int(endpoint.get("api_port", self.streaming.api_port))
                self.streaming.rtsp_port = int(endpoint.get("rtsp_port", self.streaming.rtsp_port))
                self.adopted_streaming = True
            else:
                self.adopted_streaming = False
                self.streaming.start()
        if self.recording:
            self.recorder.start()
        else:
            logger.info(
                "no stream is ticked to record with an address, so no recorder "
                "is started; enter one in Settings and save"
            )
        if self.detecting and self.detector is not None:
            self.detector.start()

    def tick(self) -> list[str]:
        """Restart whatever has died. Called on a timer by the window."""
        started = self.supervisor.tick()
        for name in started:
            # Every start the supervisor performs is a restart: `start()` above
            # already started each child once.
            self._restarted_at.setdefault(name, []).append(self._clock())
            # Pruned here as well as when read, so a console nobody looks at for
            # months cannot accumulate a list of every restart it ever made.
            self._recent_restarts(name)
        # Not on the caller's thread and not on every call; see DiskWatcher.
        self.disk.poll()
        return started

    def _recent_restarts(self, name: str) -> int:
        """How often that child has been restarted inside the flap window.

        One implementation for the recorder and the detector alike: the rule is
        the same rule, and two copies of it would be two rules within a month.
        """
        cutoff = self._clock() - FLAP_WINDOW
        recent = [at for at in self._restarted_at.get(name, []) if at >= cutoff]
        self._restarted_at[name] = recent
        return len(recent)

    def _forget_restarts(self, name: str) -> None:
        """A child this console deliberately restarted has not been flapping.

        Applying a saved setting stops and starts a child on purpose. Counting
        that as a death would have a second Save read as "restarted twice in the
        last two minutes - NOT recording", which is a lie about a system doing
        exactly what it was told.
        """
        self._restarted_at.pop(name, None)

    def apply(self, settings: Settings) -> list[str]:
        """Take settings the operator has just saved, and make them true.

        Returns the problems, as plain sentences, so that a Save which could not
        be applied says so rather than reporting the new settings as live. An
        empty list means everything the operator changed is now what is running.

        Each child reads settings.json once, at startup, so a saved setting
        reaches a running child only by restarting it - and a restart costs a
        gap in what that child was doing, which for the recorder is a gap in the
        footage. So each child is restarted when, and only when, the settings it
        actually reads have changed; see the fingerprints above for exactly
        which those are on each side of the boundary.

        A child that was adopted from an earlier console is restarted too. See
        `ChildProcess.stop`: adoption exists so recording survives the window
        closing, not so that it survives the operator changing its
        configuration.
        """
        previous = self.settings
        self.settings = settings
        # The recording root can move, and the detector's report moves with it.
        # Left pointing at the old folder, the status line would read a file
        # nobody writes any more and call detection unknown for ever.
        self.detection_status_path = Path(settings.storage.root) / DETECTION_STATUS_FILENAME
        # The folder it watches may have moved too, and a saved folder is a new
        # question rather than one to wait out the poll interval for.
        self.disk.apply(settings)

        problems: list[str] = []

        if self.streaming is not None:
            if streaming_fingerprint(previous) != streaming_fingerprint(settings):
                try:
                    self.streaming.apply(settings)
                except Exception:  # noqa: BLE001 - the file is saved either way
                    logger.exception("the streaming server would not take the new settings")
                    problems.append(
                        "the streaming server would not restart, so the live "
                        "picture is still on the old settings"
                    )
                # Whatever is serving video now, this console restarted it.
                self.adopted_streaming = False
            else:
                # Not a restart, but it must not be left holding the object the
                # operator replaced: `stream_names` and the status line read it.
                self.streaming.settings = settings

        wanted = self.detector is not None and detection_enabled(settings)
        recording = recordable(settings)
        if wanted != self.detecting or recording != self.recording:
            self.detecting = wanted
            self.recording = recording
            # A fresh supervisor rather than a mutated one: its restart counts
            # and back-off belong to a configuration that no longer exists, and
            # there is no supported way to add or remove a service from the one
            # already running.
            self.supervisor = Supervisor(self._managed(), clock=self._clock)
            self._forget_restarts("detector")
            self._forget_restarts("recorder")

        problems.extend(self._apply_to_recorder(previous, settings, recording))
        problems.extend(self._apply_to_detector(previous, settings, wanted))
        return problems

    def _apply_to_recorder(
        self, previous: Settings, settings: Settings, recording: bool
    ) -> list[str]:
        """Start, stop or restart the recorder, whichever the save asks for."""
        try:
            if not recording:
                if self.recorder.running:
                    logger.info(
                        "no stream is ticked to record with an address any more; "
                        "stopping the recorder"
                    )
                    self.recorder.stop(force=True)
                return []
            if not self.recorder.running:
                self.recorder.start()
                if not self.recorder.running:
                    return ["the recorder did not start, so nothing is being recorded"]
                return []
            if recorder_fingerprint(previous) == recorder_fingerprint(settings):
                return []
            if not self.recorder.restart("the recording settings changed"):
                return [
                    "the recorder did not restart, so recording is still using "
                    "the settings you replaced"
                ]
        except Exception:  # noqa: BLE001 - a Save must not throw back into the button
            logger.exception("the recorder would not take the new settings")
            return ["the recorder would not take the new settings"]
        return []

    def _apply_to_detector(
        self, previous: Settings, settings: Settings, wanted: bool
    ) -> list[str]:
        if self.detector is None:
            return []
        try:
            if not wanted:
                self.detector.stop(force=True)
                return []
            if not self.detector.running:
                self.detector.start()
                if not self.detector.running:
                    return ["the detector did not start, so nothing is being watched"]
                return []
            if detector_fingerprint(previous) == detector_fingerprint(settings):
                return []
            if not self.detector.restart("the movement-detection settings changed"):
                return [
                    "the detector did not restart, so movement detection is "
                    "still using the settings you replaced"
                ]
        except Exception:  # noqa: BLE001 - detection is not the picture or the disk
            logger.exception("the detector would not take the new settings")
            return ["the detector would not take the new settings"]
        return []

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
        recording = self.recording_state()
        return {
            # Still a bool, and still the first thing the status line asks -
            # but it is now the honest answer rather than "a process was alive
            # when I looked".
            "recording": recording["running"],
            "recording_state": recording,
            "streaming": streaming_state,
            "detection": self.detection_state(),
            "restarts": dict(self.supervisor.restarts),
            "storage": self.disk.reading,
        }

    def recording_state(self) -> dict:
        """Whether footage is reaching the disk, and if not, why not.

        "Recording" used to mean `self.recorder.running` - that a process
        existed at the instant the console looked. Pointed at a drive that does
        not exist, that read "recording" in 41 of 79 samples over forty seconds
        while twenty recorder processes came and went and nothing was ever
        written. A process is not footage.

        Four things can be wrong, and they are asked in the order in which each
        makes the next one meaningless:

        * nothing is ticked to record, so no recorder is supervised at all;
        * the recorder is being restarted every few seconds, which is not
          recording however alive it is in between - a process that lives two
          seconds writes no usable segment;
        * it is down right now and is about to be restarted;
        * it is up, and nothing is arriving in the folder anyway.
        """
        minutes = FLAP_WINDOW / 60.0
        if not self.recording:
            return {
                "running": False,
                "restarts": 0,
                "reason": "NOT recording - no stream is ticked to record",
            }

        restarts = self._recent_restarts("recorder")
        if restarts > FLAP_LIMIT:
            return {
                "running": False,
                "restarts": restarts,
                "reason": (
                    f"NOT recording - restarted {restarts} times "
                    f"in the last {minutes:.0f} minutes"
                ),
            }
        if not self.recorder.running:
            return {
                "running": False,
                "restarts": restarts,
                "reason": "NOT recording - restarting it",
            }

        # An adopted recorder is a PID and nothing else, and asking about a PID
        # is now done off this thread. A check that has not come back in a
        # quarter of a minute is a machine in trouble, and repeating its last
        # answer as though it were current is the console inventing health.
        age = self.recorder.liveness_age()
        if age is not None and age >= LIVENESS_UNANSWERED_SECONDS:
            return {
                "running": True,
                "restarts": restarts,
                "reason": (
                    f"recording - but whether the recorder is still there has "
                    f"not been answered for {age:.0f} s"
                ),
            }

        reading = self.disk.reading
        if reading is not None and not reading.writing:
            return {
                "running": False,
                "restarts": restarts,
                "reason": f"NOT recording - {reading.write_problem}",
            }
        if restarts:
            return {
                "running": True,
                "restarts": restarts,
                "reason": (
                    f"recording - restarted {restarts} times "
                    f"in the last {minutes:.0f} minutes"
                ),
            }
        return {"running": True, "restarts": 0, "reason": "recording"}

    def detection_state(self) -> dict:
        """What detection is doing, in the three states it can honestly be in.

        Off, running, or not running - and off is not a failure. Detection is
        opt-in per stream, so a console that reported "detection failed" on a
        machine where nobody ticked the box would teach its operator to ignore
        the line that one day says something true.

        The third state is the one this exists for. A detector that cannot stay
        up is restarted by the supervisor for as long as the console is open,
        and without this the status line would read "detecting" between each
        death while nothing watched the perimeter at all. More than a few
        restarts inside a couple of minutes is reported as not running, and it
        overrides `running`: catching the process during the half-second it is
        alive is not detection.

        Underneath all three sits the per-stream state the detector publishes,
        which is a different question from whether the process is up: a detector
        watching the thermal while the visible is unreachable is running, and is
        also not watching everything it was asked to.
        """
        enabled = detection_enabled(self.settings)
        if self.detector is None:
            return {
                "enabled": enabled,
                "running": False,
                "restarts": 0,
                "reason": "not started by this console",
                "streams": [],
                "streams_known": False,
            }
        if not enabled:
            return {
                "enabled": False,
                "running": False,
                "restarts": 0,
                "reason": "off - no stream has detection enabled",
                "streams": [],
                "streams_known": False,
            }

        restarts = self._recent_restarts("detector")
        streams, streams_known = self.detected_stream_states()
        minutes = FLAP_WINDOW / 60.0
        if restarts > FLAP_LIMIT:
            return {
                "enabled": True,
                "running": False,
                "restarts": restarts,
                "reason": (
                    f"NOT running - restarted {restarts} times "
                    f"in the last {minutes:.0f} minutes"
                ),
                "streams": streams,
                "streams_known": streams_known,
            }
        if self.detector.running:
            return {
                "enabled": True,
                "running": True,
                "restarts": restarts,
                "reason": stream_reason(streams, streams_known),
                "streams": streams,
                "streams_known": streams_known,
            }
        return {
            "enabled": True,
            "running": False,
            "restarts": restarts,
            "reason": "NOT running - restarting it",
            "streams": streams,
            "streams_known": streams_known,
        }

    def detected_stream_states(self) -> tuple[list[dict], bool]:
        """What each stream is doing, and whether that is known at all.

        The second half of the answer is the point. A console that could not
        read the detector's report, or read one written before lunch, has to say
        it does not know - anything else is the console inventing health it was
        never told about.
        """
        status = read_detection_status(self.detection_status_path)
        if status is None or not detection_status_fresh(status, self._now()):
            return [], False
        streams = [
            stream
            for stream in status["streams"]
            if isinstance(stream, dict) and stream.get("stream")
        ]
        return streams, True
