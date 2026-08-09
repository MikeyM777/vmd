"""The Update button: pull the latest code without reinstalling anything.

Two commands, in order: `git pull --ff-only`, then `uv sync` so any new
dependency is present. Both run in a background thread, because a pull over a
slow link takes longer than any browser will wait, and the console must keep
answering while it happens.

`--ff-only` is the whole safety story. If someone has edited files on this
machine the pull refuses rather than merging or discarding their work, and the
refusal is shown verbatim. An updater that can silently throw away local changes
on a machine nobody backs up is not worth having.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

TIMEOUT_SECONDS = 600


@dataclass
class UpdateState:
    """What the Update button is doing, in the words shown to the operator."""

    running: bool = False
    step: str = ""
    ok: bool | None = None
    message: str = ""
    output: list[str] = field(default_factory=list)
    finished_at: float | None = None

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "step": self.step,
            "ok": self.ok,
            "message": self.message,
            "output": self.output[-200:],
            "finished_at": self.finished_at,
        }


class Updater:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.state = UpdateState()
        self._lock = threading.Lock()

    # ------------------------------------------------------------- describing

    def version(self) -> dict:
        """What this copy is, so the operator can tell whether it changed."""
        if not (self.root / ".git").exists():
            return {
                "known": False,
                "reason": "this copy was downloaded as a ZIP, so it cannot pull updates",
            }
        if shutil.which("git") is None:
            return {"known": False, "reason": "git is not installed on this machine"}
        described = self._run(["git", "log", "-1", "--format=%h %cd", "--date=format:%d %b %Y %H:%M"])
        if described.returncode != 0:
            return {"known": False, "reason": described.stderr.strip() or "git could not read this copy"}
        return {"known": True, "version": described.stdout.strip(), "can_update": True}

    # --------------------------------------------------------------- updating

    def start(self) -> tuple[bool, str]:
        """Begin an update. Returns (started, why not)."""
        with self._lock:
            if self.state.running:
                return False, "an update is already running"
            info = self.version()
            if not info.get("can_update"):
                return False, info.get("reason", "this copy cannot update itself")
            self.state = UpdateState(running=True, step="pulling changes")
        threading.Thread(target=self._work, daemon=True).start()
        return True, ""

    def _work(self) -> None:
        try:
            pull = self._run(["git", "pull", "--ff-only"])
            self._record("git pull --ff-only", pull)
            if pull.returncode != 0:
                self._finish(False, self._pull_failure(pull))
                return

            already = "Already up to date" in pull.stdout or "Already up-to-date" in pull.stdout

            # Only when there is something to install. uv needs a pyproject to
            # act on, and reporting a dependency failure for a copy that has no
            # dependencies would send the operator to reinstall for nothing.
            if (self.root / "pyproject.toml").is_file() and shutil.which("uv"):
                self._set_step("installing any new dependencies")
                sync = self._run(["uv", "sync", "--extra", "detect"])
                self._record("uv sync --extra detect", sync)
                if sync.returncode != 0:
                    self._finish(
                        False,
                        "The new code was pulled but its dependencies could not be installed. "
                        "Run install.bat once to finish.",
                    )
                    return

            if already:
                self._finish(True, "Already up to date. Nothing changed.")
            else:
                self._finish(
                    True,
                    "Updated. Close this window and start VMD.exe again to run the new version.",
                )
        except Exception as exc:  # noqa: BLE001 - the console must survive its own updater
            self._finish(False, f"The update stopped unexpectedly: {exc}")

    def _pull_failure(self, result: subprocess.CompletedProcess) -> str:
        text = (result.stderr + result.stdout).lower()
        if "would be overwritten" in text or "local changes" in text:
            return (
                "This copy has local edits, so the update was refused rather than "
                "discarding them. Nothing was changed."
            )
        if "not possible to fast-forward" in text or "diverging" in text:
            return (
                "This copy has commits that are not in the shared version, so it cannot "
                "fast-forward. Nothing was changed."
            )
        if "could not resolve host" in text or "unable to access" in text:
            return "Could not reach GitHub. Check the internet connection on this machine."
        return "The update was refused. The output below says why. Nothing was changed."

    # ---------------------------------------------------------------- plumbing

    def _run(self, command: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            cwd=str(self.root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )

    def _record(self, label: str, result: subprocess.CompletedProcess) -> None:
        with self._lock:
            self.state.output.append(f"$ {label}")
            for stream in (result.stdout, result.stderr):
                self.state.output.extend(line for line in stream.splitlines() if line.strip())

    def _set_step(self, step: str) -> None:
        with self._lock:
            self.state.step = step

    def _finish(self, ok: bool, message: str) -> None:
        with self._lock:
            self.state.running = False
            self.state.ok = ok
            self.state.step = ""
            self.state.message = message
            self.state.finished_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            payload = self.state.as_dict()
        payload["current"] = self.version()
        return payload
