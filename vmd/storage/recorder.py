"""One ffmpeg process per stream, writing timestamped segments."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Callable

from vmd.storage.discovery import SEGMENT_FORMAT

RTSP_SCHEMES = ("rtsp://", "rtsps://")

logger = logging.getLogger(__name__)


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
        ffmpeg: str = "ffmpeg",
        spawn: Callable[..., object] = _default_spawn,
    ) -> None:
        self.stream = stream
        self.source_url = source_url
        self.output_dir = Path(output_dir)
        self.segment_seconds = segment_seconds
        self.ffmpeg = ffmpeg
        self._spawn = spawn
        self._process = None
        self._exit_code: int | None = None

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
            "-c", "copy",
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

    def start(self) -> None:
        if self.running:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
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
            return True
        self._exit_code = code
        return False

    @property
    def exit_code(self) -> int | None:
        """Exit status of the last ffmpeg run, or None if it never exited."""
        return self._exit_code
