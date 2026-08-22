"""What the detached updater process runs.

Thin on purpose: it wires the real stop, sync and selftest into `apply.run`,
and starts the console again afterwards. Everything it decides is decided in
`apply.py`, where it can be tested.

Stdlib only, and more literally than anywhere else in this package: this is the
module `bin\\python\\...\\python.exe` is pointed at, with no virtual environment
on the path and, by the time it matters, with .venv being replaced underneath
it.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from vmd.update.apply import run
from vmd.update.runner import (
    TIMEOUT_SECONDS,
    selftest_command,
    stop_command,
    sync_command,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a VMD update from a stick.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--stick", required=True)
    parser.add_argument("--settings", required=True)
    args = parser.parse_args(argv)

    root = Path(args.root)
    stick = Path(args.stick)

    def stop() -> None:
        """Stop the console and the recorder, sparing this process.

        Sparing this process is not a nicety: this is a python running out of
        bin\\python\\, which is one of the things being stopped. `stop_command`
        says what that costs when it is forgotten.

        Nothing is done with what comes back. It reports what refused to die,
        and the next thing that happens either way is the copy - which fails
        loudly, and is undone, if something is still holding a file open. A
        second judgement here would only be a second place to get it wrong.
        """
        subprocess.run(
            stop_command(root, os.getpid()),
            capture_output=True,
            timeout=300,
            check=False,
        )

    def sync(where: Path) -> tuple[bool, str]:
        result = subprocess.run(
            sync_command(root, where),
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        # The last few lines only. uv's failures end with the sentence that
        # matters, this goes into a status file the console re-reads every
        # second, and the whole of a failed resolution is thousands of lines.
        return result.returncode == 0, (result.stderr or result.stdout).strip()[-400:]

    def selftest() -> tuple[bool, str]:
        result = subprocess.run(
            selftest_command(root, Path(args.settings)),
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()[-400:]

    def start_console() -> None:
        """Put the console back up, whichever version won.

        An operator left staring at a desktop with no console is the worst
        outcome of an update, and it is the one outcome that has nothing to do
        with whether the update worked. So this is not conditional on the
        report, and a failure to start is swallowed: there is nobody left to
        tell, and the status file already says what happened to the update.

        It appears on the operator's desktop rather than nowhere: this process
        was started by the console, which runs in the logged-on session, and
        detaching a process gives up its console window - not its window
        station.
        """
        exe = root / "VMD.exe"
        starter = str(exe) if exe.is_file() else str(root / "VMD.bat")
        try:
            subprocess.Popen(  # noqa: S603 - the console this update was for
                [starter, "--settings", args.settings], cwd=str(root), close_fds=True
            )
        except OSError:
            pass

    report = run(
        root,
        stick,
        machine=os.environ.get("COMPUTERNAME", "unknown"),
        when=datetime.now().isoformat(timespec="seconds"),
        stop=stop,
        sync=sync,
        selftest=selftest,
    )

    start_console()

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
