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
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from vmd.update.apply import MARKER, PREVIOUS, Progress, _named, restore, run
from vmd.update.runner import (
    TIMEOUT_SECONDS,
    selftest_command,
    stop_command,
    sync_command,
)
from vmd.update.version import read_version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a VMD update from a stick.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--stick")
    # Going back does not read a stick at all: what it puts back is already on
    # this machine, in previous\<version>\, kept there by the update that
    # replaced it. So --stick stops being required and one of the two has to be
    # given.
    parser.add_argument("--rollback", type=int)
    parser.add_argument("--settings", required=True)
    args = parser.parse_args(argv)
    if args.rollback is None and not args.stick:
        parser.error("one of --stick or --rollback is required")

    root = Path(args.root)

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

    def sync_from_cache() -> tuple[bool, str]:
        """Going back needs the OLD libraries, and there is no stick for those.

        uv's own cache on this machine is where they are: they were installed
        here once, by the update that is being undone or by the install before
        it. So no --no-index and no --find-links - there is nothing to point
        either at - and `--offline` is what keeps a machine with a cleared
        cache from sitting on a network that is not there. When the cache has
        been cleared this fails, and the message says so rather than pretending
        the rollback was clean.
        """
        result = subprocess.run(
            [str(root / "bin" / "uv.exe"), "sync", "--offline", "--frozen", "--extra", "detect"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
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

    when = datetime.now().isoformat(timespec="seconds")

    if args.rollback is not None:
        return go_back(root, args.rollback, when, stop, sync_from_cache, start_console)

    report = run(
        root,
        Path(args.stick),
        machine=os.environ.get("COMPUTERNAME", "unknown"),
        when=when,
        stop=stop,
        sync=sync,
        selftest=selftest,
    )

    start_console()

    return 0 if report.ok else 1


def go_back(root: Path, version: int, when: str, stop, sync, start_console) -> int:
    """Put `previous\\<version>` back over this install.

    The three things that touch the machine are handed in, as they are for
    `apply.run` and for the same reason: what is left here is the order they
    happen in, which is the part worth testing.

    Every way out writes a finished status, including the ways nobody planned
    for - `stop` included, which raises when the stopper does not return inside
    five minutes. The console that pressed Go back is watching that file, and a
    rollback that ends without writing it is a panel waiting on a process that
    is not running, on the one operation where the operator most needs to be
    told what state the machine was left in.

    And it keeps the same marker discipline an update keeps, for the same
    reason: this rewrites the same tree. Cut off in the middle it leaves an
    install that is part one version and part another, and without the marker
    the panel reads that as a machine with nothing wrong.
    """
    progress = Progress(root)
    progress.begin(when)
    progress.report.moved_from = read_version(root)
    progress.report.moved_to = version

    kept = root / PREVIOUS / str(version)
    if not kept.is_dir():
        # Before the console is stopped, so nothing has been done: the console
        # that asked is still up, and it is the thing that reads this answer.
        progress.finish(False, f"There is no kept copy of {_named(version)} on this machine.")
        return 1

    marker = progress.folder / MARKER
    # Whether the install has been opened up. It answers two questions with one
    # flag: whether the marker stays up, and which of the two failures this was
    # - a rollback stopped before it touched anything is not the same news as
    # one stopped halfway through, and telling an operator their install is
    # half one version and half another when it is not would send somebody to
    # the site for nothing.
    opened_up = False
    try:
        # Inside the try, so a marker that cannot be written is reported like
        # any other failure rather than ending this process in silence. It
        # records which copy was being put back, which is what lets the panel
        # offer the same button again after a cut.
        marker.write_text(
            json.dumps({"started": when, "to": version, "kept": version, "rollback": True}),
            encoding="utf-8",
        )
        progress.say("putting the previous version back")
        stop()

        opened_up = True
        restore(kept, root)
        # Whole again, and entirely the kept version: whatever happens below
        # this line, no part of the other one is left in the tree.
        opened_up = False

        progress.say("installing that version's libraries")
        installed, said = sync()
        progress.finish(
            installed,
            f"{_named(version)} is back. The console will start again by itself."
            if installed
            else f"{_named(version)}'s files are back, but its libraries could not be "
            f"installed from this machine's cache ({said}). Bring a stick with "
            f"{_named(version)} on it.",
        )
        return 0 if installed else 1
    except Exception as failure:
        # Deliberately everything: a stopper that never returned, a file held
        # open by something that did not die, a full disk. Whichever it was,
        # the console is watching a status file and there is nobody else to
        # tell.
        said = f"{type(failure).__name__}: {failure}"
        progress.say("something went wrong", said)
        if not opened_up:
            progress.finish(
                False,
                f"Going back to {_named(version)} stopped before anything was "
                f"replaced ({said}). Nothing was changed.",
            )
            return 1
        progress.finish(
            False,
            f"Going back to {_named(version)} stopped part of the way through ({said}). "
            f"This copy is now part {_named(progress.report.moved_from)} and part "
            f"{_named(version)} and has to be installed again from a stick.",
        )
        return 1
    finally:
        if not opened_up:
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                # A marker that will not delete costs one false "an update was
                # interrupted" at the next start. Raising here would replace
                # the return value on its way out and throw away the only
                # report of what happened.
                pass
        # Whatever happened, and last: an operator left staring at a desktop
        # with no console is the worst outcome there is, and it is the one that
        # has nothing to do with whether the rollback worked.
        start_console()


if __name__ == "__main__":
    sys.exit(main())
