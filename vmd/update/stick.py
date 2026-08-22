"""Which drive is the update stick, and what it is offering.

The drives are handed in rather than discovered, everywhere except at the one
edge that has to ask Windows. That is what makes this testable at all: a test
can hand it two folders in tmp_path and get back the sentence the operator
would have seen.
"""

from __future__ import annotations

import ctypes
import json
import os
import string
from dataclasses import dataclass
from pathlib import Path

from vmd.update.version import read_version

UPDATE_JSON = "update.json"
MANIFEST_JSON = "manifest.json"
FILES = "files"
WHEELS = "wheels"

DRIVE_REMOVABLE = 2
# A USB stick does not always report as "removable". USB3 sticks, larger sticks,
# SSD-in-an-enclosure and many card readers enumerate as FIXED, and a panel that
# only trusted "removable" told the operator "No update stick found" with the
# stick plugged in - the one machine where there is no other way to update.
DRIVE_FIXED = 3


@dataclass
class StickState:
    """What the Update panel draws, and nothing else.

    `kind` is one of: none, many, damaged, older, same, ready. The panel
    switches on it; `message` is what it prints; `stick` and `version` are only
    meaningful when `kind` is "ready".
    """

    kind: str
    message: str
    stick: Path | None = None
    version: int | None = None


def removable_drives() -> list[Path]:
    """Every drive letter that could be an update stick.

    The only function in this package that asks the operating system anything,
    so it is kept small: everything above it takes a list of folders and can be
    tested with folders.

    Both REMOVABLE and FIXED drives are returned, because Windows tags plenty of
    real USB sticks as FIXED and a "removable only" rule made the air-gapped
    panel blind to them. The system drive is left out - it is never a stick, and
    it is the one FIXED drive we do not want to scan - and `read_update` does the
    rest of the filtering: a drive without update.json and manifest.json is not a
    stick, so an ordinary data drive is ignored whatever its type.
    """
    found: list[Path] = []
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    except AttributeError:
        return found
    system_drive = os.environ.get("SystemDrive", "C:").rstrip("\\").upper()
    for letter in string.ascii_uppercase:
        if f"{letter}:" == system_drive:
            continue
        root = f"{letter}:\\"
        try:
            if kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) in (DRIVE_REMOVABLE, DRIVE_FIXED):
                found.append(Path(root))
        except OSError:
            continue
    return found


def read_update(drive: Path) -> dict | None:
    """The stick's own description of itself, or None if this is not a stick.

    None means "there is nothing here to call a stick" - no update.json, or no
    manifest.json beside it. `{}` means the opposite: update.json exists (so
    this drive IS a VMD stick) but could not be read as JSON, which `look`
    turns into "damaged" rather than "none" - the drive is not missing, it is
    broken, and those get different messages.
    """
    path = Path(drive) / UPDATE_JSON
    if not path.is_file() or not (Path(drive) / MANIFEST_JSON).is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def look(root: Path | str, drives) -> StickState:
    """What to say about the drives in front of us."""
    sticks = [(Path(drive), read_update(drive)) for drive in drives]
    sticks = [(drive, update) for drive, update in sticks if update is not None]

    if not sticks:
        return StickState("none", "No update stick found.")
    if len(sticks) > 1:
        names = [str(drive) for drive, _ in sticks]
        named = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
        count = len(names)
        unplug = "one" if count == 2 else "ones"
        return StickState(
            "many",
            f"There are {count} update sticks plugged in - {named}. "
            f"Unplug the {unplug} you do not want and press Look again.",
        )

    stick, update = sticks[0]
    theirs = update.get("version")
    if not isinstance(theirs, int):
        return StickState(
            "damaged",
            f"The stick in {stick} does not say which version it carries, "
            f"so it cannot be used. Build it again on the laptop.",
        )

    mine = read_version(root)
    if mine is None:
        return StickState(
            "ready",
            f"The stick has VMD {theirs}. This system's own version is unknown, "
            f"so it can be updated but not compared.",
            stick,
            theirs,
        )
    if theirs == mine:
        return StickState(
            "same", f"The stick has VMD {theirs} - the same version this system runs."
        )
    if theirs < mine:
        return StickState(
            "older",
            f"The stick has VMD {theirs} and this system runs VMD {mine}, so there "
            f"is nothing to install. Going back to an older version is the other "
            f"button.",
        )
    return StickState(
        "ready", f"The stick has VMD {theirs}. This system runs VMD {mine}.", stick, theirs
    )
