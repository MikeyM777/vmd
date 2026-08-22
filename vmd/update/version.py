"""Which version this copy is, and how to say it.

One whole number in a file called VERSION in the project root, bumped when a
change is worth shipping. It travels inside the update, so after an update the
copy's own VERSION file IS its version - there is no second place for it to be
recorded and no way for two places to disagree.

Not a date and not three numbers: the question this has to answer, over a
telephone, to somebody standing at a laptop with no internet, is "is the stick
newer than the machine". 8 > 7 answers it.
"""

from __future__ import annotations

from pathlib import Path

VERSION_FILE = "VERSION"


def read_version(root: Path | str) -> int | None:
    """The version of the copy in `root`, or None when it cannot be read.

    None rather than 0. A folder with no VERSION file is one that predates this
    or one that is half-copied, and calling that 0 would make every stick in
    the world look newer than it - which is exactly the comparison that must
    not be made on a guess.
    """
    path = Path(root) / VERSION_FILE
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def describe(root: Path | str) -> str:
    """What to print on a window title or a form: "VMD 7"."""
    version = read_version(root)
    return f"VMD {version}" if version is not None else "VMD (version unknown)"
