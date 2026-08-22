"""Starting the updater, and the three things it does to the machine.

The console calls `start`. Everything below it exists so that `apply.run` can be
tested without killing a process, running uv or starting an interpreter.

Stdlib only, like the rest of this package: this module is imported by
`vmd/update/main.py`, which the bundled interpreter runs with no virtual
environment at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from vmd.update.apply import LOGS, STATUS
from vmd.update.stick import WHEELS

# Half an hour. Not a guess about how long a sync takes but a bound on how long
# a wedged one is allowed to hold the machine: this runs detached, with the
# console already killed, so a subprocess that never returns is a machine with
# no console and nobody to tell.
TIMEOUT_SECONDS = 1800

TEMP_COPY = "vmd-update"


def uv_exe(root: Path) -> Path:
    """The uv that travels with this project, and on the offline machine the
    only one there is."""
    return Path(root) / "bin" / "uv.exe"


def sync_command(root: Path, stick: Path) -> list[str]:
    """Install the libraries this update needs, from the stick.

    `--offline` and `--no-index` together, and both are load-bearing: the first
    stops uv consulting the network at all, the second stops it treating PyPI as
    a source it merely cannot reach right now. What is left is the wheels on the
    stick and whatever is already in uv's own cache on this machine.
    """
    return [
        str(uv_exe(root)),
        "sync",
        "--offline",
        "--frozen",
        "--no-index",
        "--find-links",
        str(Path(stick) / WHEELS),
        "--extra",
        "detect",
    ]


def stop_command(root: Path, spare: int) -> list[str]:
    """Stop the console and the recorder, by the script that already knows how.

    `Stop-ProjectProcesses` has been doing this for install.bat since the day a
    uv sync deleted a .venv that python.exe was running out of, so it is called
    rather than reimplemented.

    `spare` is the updater's own process id, and leaving it out is fatal: the
    updater is `bin\\python\\...\\python.exe`, which is exactly what that
    function matches - "a process named python running from under bin\\python".
    Without being told to spare it, the first thing an update does is taskkill
    itself, halfway through, with the console already gone and nothing left
    running to start it again. It was measured, not guessed: a process started
    that way killed itself here on the first attempt.
    """
    return [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        f". '{Path(root) / 'scripts' / '_common.ps1'}'; "
        f"Stop-ProjectProcesses '{Path(root)}' {spare} | Out-Null",
    ]


def selftest_command(root: Path, settings: Path) -> list[str]:
    """Run the new version's own smoke test, against this machine's settings.

    Through uv rather than the bundled interpreter, because this is the one
    check that has to see what the console will see: the environment in .venv,
    with the libraries the sync just put there.
    """
    return [
        str(uv_exe(root)),
        "run",
        "--offline",
        "--frozen",
        "--no-sync",
        "python",
        "-m",
        "vmd.selftest",
        "--settings",
        str(settings),
    ]


def temp_folder() -> Path:
    """Where this console puts the copy of itself that will do the updating.

    Named after the process that asks, because one install can run several
    consoles - one per camera folder - under one account, sharing one TEMP.
    A single fixed name would mean the second console to press Update deletes
    the code the first one is running out of, which is precisely the accident
    the copy is here to prevent.
    """
    return Path(os.environ.get("TEMP", ".")) / f"{TEMP_COPY}-{os.getpid()}"


def temp_copy_of(root: Path, into: Path) -> Path:
    """Copy the `vmd` package out of the install, and answer where it went.

    The updater is part of the thing being updated. Running it out of the folder
    it is rewriting is asking Python to import a module out of a file that is
    being replaced under it - which works until the day it does not, on the
    machine where nobody can tell what went wrong.

    Nothing ever deletes these copies: the process that makes one is killed by
    the update it starts. So this always finds whatever the last update left,
    and it replaces rather than merges - a copy holding some files from this
    version and some from the last is an updater nobody can reason about. Any
    file that cannot be deleted is overwritten instead, because a leftover that
    is locked is a worse reason to refuse an update than it is a risk.
    """
    into = Path(into)
    shutil.rmtree(into, ignore_errors=True)
    into.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(root) / "vmd", into / "vmd", dirs_exist_ok=True)
    return into


def spawn_orphan(command: list[str], cwd: Path, environment: dict) -> None:
    """Start a process that nothing can find its way down to, and return at once.

    Detached is not enough, and this is the second half of the same fatal bug
    `stop_command` describes. The console stops the recorder with
    `taskkill /F /T`, and /T means "and everything below it": Windows keeps the
    id of the process that started each process, and a tree kill walks it.
    DETACHED_PROCESS gives up a console window, not a place in that tree - a
    detached child of the console is killed with the console, which was
    measured here rather than assumed.

    So the updater is started through `cmd /c start`, which starts it and exits.
    By the time the console is stopped - after the note, after every file on the
    stick has been read back and checksummed - the process that started the
    updater has been gone for a long time, and there is no tree left leading to
    it. `scripts/build_exe.ps1` knows the same thing from the other side, and
    says so where it kills a console: "a grandchild whose parent had already
    exited, which Windows no longer relates to anyone".
    """
    creation = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen(  # noqa: S603 - our own interpreter, our own module
        # The empty title is start's, not ours: given a quoted first argument -
        # which any path with a space in it becomes - start reads it as the
        # title of a window and then has no program left to run.
        [os.environ.get("COMSPEC", "cmd.exe"), "/c", "start", "", "/B", *command],
        cwd=str(cwd),
        env=environment,
        creationflags=creation,
        close_fds=True,
    )


def project_python(root: Path) -> Path | None:
    """The interpreter inside bin\\python\\, which no update replaces."""
    folder = Path(root) / "bin" / "python"
    if not folder.is_dir():
        return None
    found = sorted(folder.glob("*/python.exe"))
    return found[0] if found else None


def start(root: Path, stick: Path, settings: Path) -> tuple[bool, str]:
    """Start the updater as its own process and return at once.

    Out of a copy of this tree, and out of reach of the tree kill that stops
    the console - `temp_copy_of` and `spawn_orphan` each say why - because this
    process is one of the things the updater is about to stop.

    What comes back says only whether the process was started, not whether the
    update worked - nothing here waits for that, and nothing here could. The
    answer is written to the status file by the updater itself, which is the
    only way it could be: by the time there is an answer, this console has been
    killed.
    """
    root = Path(root)
    status = root / LOGS / STATUS
    status.parent.mkdir(parents=True, exist_ok=True)
    # The panel watches this file for `finished`. Last update's copy of it would
    # answer the moment this one begins.
    status.unlink(missing_ok=True)

    python = project_python(root)
    if python is None:
        return False, "This copy has no interpreter in bin\\python\\, so it cannot update itself."

    try:
        where = temp_copy_of(root, temp_folder())
        spawn_orphan(
            [
                str(python),
                "-m",
                "vmd.update.main",
                "--root",
                str(root),
                "--stick",
                str(stick),
                "--settings",
                str(settings),
            ],
            cwd=where,
            environment=dict(os.environ, PYTHONPATH=str(where)),
        )
    except OSError as failure:
        # A copy that could not be made - a leftover from the last update that
        # something still holds open, a full disk - or Windows refusing to start
        # the process at all. Raised into the button that was pressed, either
        # would close the panel over a traceback; said out loud it is a sentence
        # the operator can read down the telephone. Nothing on the machine has
        # been touched at this point, so there is nothing to undo.
        return False, f"The updater could not be started: {failure}"
    return True, ""
