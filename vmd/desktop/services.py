"""The processes the window looks after, and the state it reports about them.

Recording does not belong to the window. It is a separate process so that a
crash in the video pane, or the operator closing the window, cannot stop the
disk filling - which was the first requirement this system was given.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from vmd.settings import Settings
from vmd.streaming.endpoint import is_live, read_endpoint
from vmd.streaming.go2rtc import Go2rtcService
from vmd.supervisor import Managed, Supervisor

logger = logging.getLogger(__name__)


class RecorderProcess:
    """`python -m vmd.record_main`, shaped to fit the supervisor's protocol.

    A PID file makes the process findable across window lifetimes. Recording is
    meant to outlive the window, which means the next window must be able to
    tell "already recording" from "not recording" - otherwise it starts a second
    recorder on the same directory, and two of them fight over the same files
    and the same index.
    """

    def __init__(
        self,
        settings_path: str | Path,
        pid_path: str | Path | None = None,
        spawn=None,
    ) -> None:
        self.settings_path = Path(settings_path)
        self.pid_path = Path(pid_path) if pid_path else self.settings_path.parent / "recorder.pid"
        self._spawn = spawn or _default_spawn
        self._process: subprocess.Popen | None = None
        self._adopted_pid: int | None = None

    @property
    def running(self) -> bool:
        if self._process is not None:
            return self._process.poll() is None
        if self._adopted_pid is not None:
            return _pid_alive(self._adopted_pid)
        return False

    def start(self) -> None:
        if self.running:
            return

        adopted = self._read_pid()
        if adopted is not None and _pid_alive(adopted):
            logger.info("a recorder is already running (pid %s); adopting it", adopted)
            self._adopted_pid = adopted
            return
        self._adopted_pid = None

        command = [
            sys.executable,
            "-m",
            "vmd.record_main",
            "--settings",
            str(self.settings_path),
        ]
        try:
            self._process = self._spawn(command)
        except OSError:
            logger.exception("could not start the recorder")
            self._process = None
            return
        self._write_pid()
        logger.info("recorder started")

    def _read_pid(self) -> int | None:
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _write_pid(self) -> None:
        pid = getattr(self._process, "pid", None)
        if pid is None:
            return
        try:
            self.pid_path.parent.mkdir(parents=True, exist_ok=True)
            self.pid_path.write_text(str(pid), encoding="utf-8")
        except OSError:
            logger.warning("could not write %s", self.pid_path, exc_info=True)

    def stop(self) -> None:
        """Stop a recorder this object started.

        An adopted one is left alone: it belongs to a window that is gone, and
        killing it here would stop recording because someone closed a second
        window.
        """
        if self._process is None and self._adopted_pid is not None:
            self._adopted_pid = None
            return
        process = self._process
        if process is None:
            return
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
                    # recorder on the same directory would fight the first.
                    logger.error("the recorder did not stop; leaving it tracked")
                    return
        self._process = None


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


def _default_spawn(command: list[str]) -> subprocess.Popen:
    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


class ConsoleServices:
    """Everything the window starts and watches."""

    def __init__(
        self,
        settings: Settings,
        settings_path: str | Path,
        streaming: Go2rtcService | None,
        recorder: RecorderProcess,
    ) -> None:
        self.settings = settings
        self.settings_path = Path(settings_path)
        self.streaming = streaming
        self.recorder = recorder
        self.adopted_streaming = False

        managed = [Managed(name="recorder", service=recorder)]
        if streaming is not None:
            managed.insert(0, Managed(name="streaming", service=streaming))
        self.supervisor = Supervisor(managed)

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
                self.streaming.api_port = int(endpoint.get("api_port", self.streaming.api_port))
                self.streaming.rtsp_port = int(endpoint.get("rtsp_port", self.streaming.rtsp_port))
                self.adopted_streaming = True
            else:
                self.adopted_streaming = False
                self.streaming.start()
        self.recorder.start()

    def tick(self) -> list[str]:
        """Restart whatever has died. Called on a timer by the window."""
        return self.supervisor.tick()

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
        return {
            "recording": self.recorder.running,
            "streaming": streaming_state,
            "restarts": dict(self.supervisor.restarts),
        }
