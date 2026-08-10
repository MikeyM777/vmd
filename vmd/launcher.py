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

    if not (root / "vmd" / "desktop").is_dir():
        return hold(
            f"\n  This does not look like the VMD folder:\n    {root}\n\n"
            "  Keep VMD.exe in the folder it was installed into."
        )

    uv = shutil.which("uv")
    if uv is None:
        return hold(
            "\n  uv is not installed, so the console cannot start.\n"
            "  Double-click install.bat once; it sets everything up."
        )

    command = [uv, "run", "python", "-m", "vmd.desktop", *args]
    try:
        # Run in the project directory so settings.json, recordings and bin\
        # all resolve the way every other part of the system expects.
        completed = subprocess.run(command, cwd=str(root), check=False)
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        return hold(f"\n  Could not start the console: {exc}")

    if completed.returncode != 0:
        # The console prints its own diagnosis; this only keeps the window up
        # long enough to read it when it was started from Explorer.
        return hold("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
