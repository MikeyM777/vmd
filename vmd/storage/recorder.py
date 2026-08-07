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
