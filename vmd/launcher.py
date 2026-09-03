"""What VMD.exe actually is: a launcher, not a copy of the program.

An executable that bundles the code freezes it. Pulling an update would change
every file in the project and the exe would go on serving the version it was
built from, which is exactly the trap the Update button exists to avoid. So the
exe carries nothing: it finds the project it sits in and runs that.

The cost is that the environment built by install.bat must be present. It always
is - the recorder and the detector need it too.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def project_root() -> Path:
    """The folder the exe sits in, which is the project folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


# How long the "does this uv actually run" check may take. It is `uv --version`
# against a local executable - milliseconds - so this is only here so that a uv
# which has wedged costs the operator ten seconds rather than a console that
# never opens.
UV_PROBE_SECONDS = 10.0


def uv_candidates(root: Path) -> list[str]:
    """Where uv might be, best first.

    The installer puts uv.exe in bin\\ and adds bin\\ to the user PATH, but the
    environment-change broadcast does not always reach Explorer before the
    operator double-clicks VMD.exe - and an exe started from Explorer inherits
    Explorer's environment. The launcher then said "uv is not installed" on a
    machine where it plainly was, with no terminal to find that out from.
    VMD.bat has never had the problem because it prepends bin\\ itself.

    Looked for by full path rather than by asking PATH again, because the point
    is to not depend on PATH at all.
    """
    found = [
        str(candidate)
        for candidate in (root / "bin" / "uv.exe", root / "bin" / "uv")
        if candidate.is_file()
    ]
    on_path = shutil.which("uv")
    if on_path and on_path not in found:
        found.append(on_path)
    return found


def uv_runs(path: str) -> bool:
    """Whether this file is a uv that works, rather than a file called uv.

    `is_file()` is true of a half-downloaded uv.exe, and a half-downloaded
    uv.exe is the ordinary way to get one: the installer fetches it over the
    same radio link this laptop keeps losing. Windows then refuses it with
    "%1 is not a valid Win32 application", which reached the operator as a
    number, on a machine that may have had a working uv on PATH the whole time.
    """
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            timeout=UV_PROBE_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def working_uv(root: Path) -> tuple[str | None, str]:
    """The first uv that answers, and - when none does - why not.

    The two failures are told apart because they send the operator to
    different places: nothing to find at all is an install that never ran,
    while a uv that is there and will not start is an install that ran and
    brought down a broken file.
    """
    candidates = uv_candidates(root)
    for candidate in candidates:
        if uv_runs(candidate):
            return candidate, ""
    if candidates:
        return None, (
            f"\n  uv is installed here but will not run on this machine:\n"
            f"    {candidates[0]}\n\n"
            "  The file is usually a download that did not finish.\n"
            "  Double-click install.bat once; it fetches a fresh copy."
        )
    return None, (
        "\n  uv is not installed, so the console cannot start.\n"
        "  Double-click install.bat once; it sets everything up."
    )


def find_uv(root: Path) -> str | None:
    """The uv to start the console with, or None if there is not a working one."""
    return working_uv(root)[0]


def child_environment(root: Path) -> dict[str, str]:
    """The console's environment, with bin\\ in front of PATH.

    A second belt, not a replacement. The launcher starts the console, which
    starts the recorder, which runs ffmpeg - and the installer puts ffmpeg.exe
    in bin\\ beside go2rtc and uv. `vmd.storage.recorder.find_tool` already
    looks there, so recording works without this; putting bin\\ on the child's
    PATH means everything else resolves the same way whether it goes through
    that lookup or a bare PATH one, which is one line for one fewer thing that
    can differ.
    """
    environment = dict(os.environ)
    bin_dir = root / "bin"
    if bin_dir.is_dir():
        environment["PATH"] = str(bin_dir) + os.pathsep + environment.get("PATH", "")
    return environment


def hold(message: str) -> int:
    print(message)
    try:
        input("\n  Press Enter to close this window. ")
    except (EOFError, KeyboardInterrupt):
        pass
    return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = project_root()

    # Whether a watchdog is holding this launcher (scripts\run_console.ps1). Every
    # failure below normally ends at "Press Enter to close" so a person at the
    # machine can read it - but under the watchdog nobody is there, and a blocked
    # `input()` would freeze the reopen loop on the first failure, which is the
    # black screen the watchdog exists to prevent. So under supervision each of
    # these prints its diagnosis and returns a non-zero code, and the watchdog
    # reopens (with a widening backoff for a failure that repeats on start). A
    # clean exit still returns 0, which is how the watchdog knows to stay closed.
    supervised = os.environ.get("VMD_SUPERVISED") == "1"

    def bail(message: str) -> int:
        if supervised:
            if message:
                print(message)
            return 1
        return hold(message)

    if not (root / "vmd" / "desktop").is_dir():
        return bail(
            f"\n  This does not look like the VMD folder:\n    {root}\n\n"
            "  Keep VMD.exe in the folder it was installed into."
        )

    uv, why_not = working_uv(root)
    if uv is None:
        return bail(why_not)

    # --no-sync --frozen --offline, because starting the console must not be a
    # network operation. `uv run` on its own re-checks the lock file and syncs,
    # so any drift - a pulled commit, a touched pyproject.toml - sends it to
    # PyPI. On this laptop there is no network at all, so that is a hang or a
    # refusal at the one moment a non-technical operator cannot recover from.
    # install.bat and the Update button are where dependencies are allowed to
    # change; this is not.
    command = [
        uv, "run", "--offline", "--frozen", "--no-sync",
        "python", "-m", "vmd.desktop", *args,
    ]
    try:
        # Run in the project directory so settings.json, recordings and bin\
        # all resolve the way every other part of the system expects.
        completed = subprocess.run(
            command, cwd=str(root), check=False, env=child_environment(root)
        )
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        return bail(f"\n  Could not start the console: {exc}")

    if completed.returncode != 0:
        # The console prints its own diagnosis. Unsupervised this only keeps the
        # window up long enough to read it; under the watchdog the code is handed
        # straight back so the reopen loop can act on it. `bail("")` does both -
        # but the crash code itself is returned so a widening backoff can tell a
        # console that ran and fell over from one that dies the instant it opens.
        if supervised:
            return completed.returncode
        return hold("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
