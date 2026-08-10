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

# How long the recorder tree gets to disappear after taskkill has been told to
# end it. It is already a forced kill, so this is only the time the kernel needs.
TREE_STOP_SECONDS = 10.0


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
        kill_tree=None,
    ) -> None:
        self.settings_path = Path(settings_path)
        self.pid_path = Path(pid_path) if pid_path else self.settings_path.parent / "recorder.pid"
        self._spawn = spawn or _default_spawn
        self._kill_tree = kill_tree or _taskkill_tree
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
        """Stop a recorder this object started, and everything it started.

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

        An adopted recorder is left alone: it belongs to a window that is gone,
        and stopping it here would stop recording because someone closed a
        second window.
        """
        if self._process is None and self._adopted_pid is not None:
            self._adopted_pid = None
            return
        process = self._process
        if process is None:
            return
        pid = getattr(process, "pid", None)
        if process.poll() is None and pid is not None and self._kill_tree(pid):
            try:
                process.wait(timeout=TREE_STOP_SECONDS)
            except subprocess.TimeoutExpired:
                logger.warning("the recorder outlived taskkill; forcing it")
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
    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=_creation_flags(),
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
