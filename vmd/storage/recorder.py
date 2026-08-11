"""One ffmpeg process per stream, writing timestamped segments."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from vmd.storage.discovery import SEGMENT_FORMAT

RTSP_SCHEMES = ("rtsp://", "rtsps://")

# How many ffmpegs that never got as far as running may be started for one
# stream inside this window before the recorder stops starting it.
#
# From the deployment laptop: 24 files of zero bytes, one every five seconds -
# the supervision interval, not the segment length - each one an ffmpeg that
# exited before it wrote a header. Restarting something two dozen times while it
# fails identically every time is not supervision, and it buries the one line
# that says why under a fresh copy of itself every pass.
#
# Bounded by a window rather than latched, so that giving up is never permanent:
# a camera whose firmware is changed, or a folder that becomes writable again,
# comes back on its own.
RESTART_WINDOW_SECONDS = 120.0
RESTART_LIMIT = 5

# The longest a single ffmpeg's stderr may contribute to the log in one pass.
# ffmpeg does not normally write a great deal at `-loglevel error`, but a stream
# that is failing on every frame can, and the Logs tab holds five hundred lines.
LOG_TAIL_BYTES = 8192
LOG_TAIL_LINES = 10

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    """Where bin\\ lives: beside the executable when frozen, else the package parent."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def find_tool(name: str, project_root: Path | None = None) -> str:
    """A command-line tool: the copy in bin\\ first, then whatever is on PATH.

    INSTALL.md tells whoever prepares the offline machine to copy ffmpeg.exe
    into `C:\\VMD\\bin\\`, exactly as go2rtc lives there - and nothing looked
    for it, because the recorder ran the bare name and let PATH resolve it. On
    the deployment laptop, following the instructions as written meant recording
    never started at all, with no message saying why.

    The bare name is returned when there is no bundled copy, which is how it
    resolves on a development machine with ffmpeg installed system-wide, and
    which keeps a missing tool failing the way it always did: at spawn, with the
    tool's own name in the error.
    """
    root = project_root or _project_root()
    for candidate in (root / "bin" / f"{name}.exe", root / "bin" / name):
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    return found or name


def find_ffmpeg(project_root: Path | None = None) -> str:
    return find_tool("ffmpeg", project_root)


def _default_spawn(command: list[str], log_path: Path | None = None):
    # TZ=UTC so ffmpeg's -strftime filenames are UTC and therefore monotonic. With local
    # time, the autumn daylight-saving transition repeats an hour and ffmpeg overwrites
    # the segments it already wrote for that hour.
    environment = {**os.environ, "TZ": "UTC"}
    # stderr goes to a file, never to an unread pipe: an unread pipe fills its OS buffer
    # and blocks ffmpeg forever, leaving a hung process that still reports as running.
    if log_path is None:
        stderr = subprocess.DEVNULL
    else:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate rather than append: this is restarted every time the link drops,
        # and an unbounded log on an unattended box eventually fills the disk and
        # stops recording. One run's stderr is what matters when diagnosing.
        stderr = open(log_path, "wb")
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=stderr,
        env=environment,
    )
    if hasattr(stderr, "close"):
        stderr.close()  # the child holds its own duplicate of the handle
    return process


class SegmentRecorder:
    """Records one stream to disk as fixed-length segments, without re-encoding."""

    def __init__(
        self,
        stream: str,
        source_url: str,
        output_dir: str | Path,
        segment_seconds: int = 300,
        ffmpeg: str | None = None,
        spawn: Callable[..., object] = _default_spawn,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.stream = stream
        self.source_url = source_url
        self.output_dir = Path(output_dir)
        self.segment_seconds = segment_seconds
        # Resolved rather than assumed: the offline install puts ffmpeg.exe in
        # bin\ beside go2rtc, and PATH alone never looked there. See find_tool.
        self.ffmpeg = ffmpeg or find_ffmpeg()
        self._spawn = spawn
        self._process = None
        self._exit_code: int | None = None
        self._clock = clock or time.monotonic
        # When each ffmpeg that never got as far as being seen alive was
        # started; see `held_back`.
        self._stillbirths: list[float] = []
        # Whether the ffmpeg now held has ever been seen running. An ffmpeg that
        # was already gone the first time it was asked never started at all -
        # which is a different fault from a stream that recorded for an hour and
        # dropped, and only one of the two is worth retrying every five seconds.
        self._seen_running = False
        self._said_held_back = False
        # How much of ffmpeg's stderr has already been passed on; see
        # `new_log_lines`.
        self._log_offset = 0

    def build_command(self) -> list[str]:
        command = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if self.source_url.lower().startswith(RTSP_SCHEMES):
            # Do not add -stimeout here: it was renamed and then removed in modern
            # ffmpeg builds, and an unknown option makes ffmpeg exit immediately.
            command += ["-rtsp_transport", "tcp"]
        else:
            # -re paces reading at the input's own frame rate. RTSP already arrives in
            # real time, so it does not need this. A local file (or a looped test
            # source) would otherwise be read as fast as disk/CPU allow, so an entire
            # multi-segment recording finishes within the same wall-clock second and
            # every segment gets the same -strftime filename, silently overwriting the
            # previous one.
            command += ["-re"]
        command += [
            "-i", self.source_url,
            # The video, named rather than assumed, and nothing else.
            #
            # `-c copy` copied whatever the source offered, and what this camera
            # offers is pcm_mulaw audio. MP4 cannot carry it: ffmpeg writes
            # "Could not find tag for codec pcm_mulaw", refuses the header, and
            # exits before the first frame. Every five seconds, for a whole day,
            # leaving 24 files of zero bytes and a console that said "recording".
            #
            # Nobody has ever listened to that audio. The video panes pass
            # --no-audio to libVLC - "never listened to: one less decode, one
            # less failure" - and this is the same position, held one process
            # along, where it costs the archive rather than a decode.
            #
            # Explicit about the stream it wants as well as the ones it does
            # not, so the next thing a camera offers that MP4 cannot hold is a
            # message about a stream that was asked for rather than a silent
            # loop: `-map 0:v:0` names the first video stream, and a source with
            # no video at all fails with "matches no streams" instead of
            # recording an empty container.
            "-map", "0:v:0",
            "-c:v", "copy",
            "-an",
            "-f", "segment",
            "-segment_time", str(self.segment_seconds),
            "-segment_format", "mp4",
            "-reset_timestamps", "1",
            "-strftime", "1",
            str(self.output_dir / f"{SEGMENT_FORMAT}.mp4"),
        ]
        return command

    @property
    def log_path(self) -> Path:
        """Where ffmpeg's stderr is appended. Beside the segment directory, not in it."""
        return self.output_dir.parent / f"{self.stream}.ffmpeg.log"

    def new_log_lines(self) -> list[str]:
        """Whatever ffmpeg has written to its log since this was last asked.

        Its stderr goes to a file rather than to a pipe, and must go on doing
        so: a pipe nobody reads fills its buffer and blocks ffmpeg for ever,
        leaving a wedged process that still reports as running. But that file
        reached nobody. The one explanation of a total failure of recording -
        "Could not write header (incorrect codec parameters ?)" - sat on the
        laptop for a whole day while the console said "recording", because
        nothing ever read it back.

        The file is truncated by every start, so a size smaller than what has
        already been read means a fresh run rather than an impossible rewind.
        Bounded at both ends: only the tail of a large file is read, and only
        the last few lines of that are returned, because this ends up in a
        500-line ring the operator reads on a laptop.
        """
        try:
            size = self.log_path.stat().st_size
        except OSError:
            return []
        if size < self._log_offset:
            self._log_offset = 0  # truncated: this is a new run's stderr
        if size <= self._log_offset:
            return []
        start = max(self._log_offset, size - LOG_TAIL_BYTES)
        try:
            with open(self.log_path, "rb") as handle:
                handle.seek(start)
                data = handle.read(size - start)
        except OSError:
            return []
        self._log_offset = size
        lines = [
            line.strip()
            for line in data.decode("utf-8", "replace").splitlines()
            if line.strip()
        ]
        return lines[-LOG_TAIL_LINES:]

    @property
    def held_back(self) -> bool:
        """Has ffmpeg failed to start so often, so recently, that starting it
        again is no longer worth doing? See RESTART_LIMIT.

        Only runs that were never seen alive count. A stream that recorded for
        an hour and then dropped is the ordinary life of a radio link and must
        be restarted every time; one that is dead every time it is looked at has
        something wrong with it that another attempt will not fix.
        """
        cutoff = self._clock() - RESTART_WINDOW_SECONDS
        self._stillbirths = [at for at in self._stillbirths if at >= cutoff]
        return len(self._stillbirths) >= RESTART_LIMIT

    def start(self) -> None:
        if self.running:
            return
        if self.held_back:
            if not self._said_held_back:
                self._said_held_back = True
                logger.error(
                    "ffmpeg for %s has exited before it recorded anything %d "
                    "times in the last %.0f minutes, so it is not being started "
                    "again until that has quietened down. It said: %s",
                    self.stream,
                    len(self._stillbirths),
                    RESTART_WINDOW_SECONDS / 60.0,
                    " | ".join(self.new_log_lines()) or "nothing",
                )
            return
        self._said_held_back = False
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._seen_running = False
        self._started_at = self._clock()
        self._process = self._spawn(self.build_command(), self.log_path)

    def stop(self) -> None:
        """Stop ffmpeg, escalating to kill if it ignores terminate.

        The process reference is only cleared once the process is confirmed dead. A
        recorder that forgot a live process would let the supervisor start a second
        ffmpeg writing into the same directory.
        """
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            logger.warning("ffmpeg for %s ignored terminate; killing it", self.stream)
            try:
                self._process.kill()
                self._process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                logger.error(
                    "ffmpeg for %s survived kill; not clearing the handle so that "
                    "running stays True and no second recorder is started",
                    self.stream,
                )
                return
        self._exit_code = self._process.poll()
        self._process = None

    @property
    def running(self) -> bool:
        if self._process is None:
            return False
        code = self._process.poll()
        if code is None:
            self._seen_running = True
            return True
        self._exit_code = code
        if not self._seen_running:
            # It was already gone the first time anyone looked, so it never
            # recorded a frame. Counted once - `_seen_running` is only reset by
            # the next start - and it is what `held_back` is measured on.
            self._seen_running = True
            self._stillbirths.append(self._clock())
        return False

    @property
    def exit_code(self) -> int | None:
        """Exit status of the last ffmpeg run, or None if it never exited."""
        return self._exit_code
