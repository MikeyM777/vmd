"""The note this machine leaves on the stick about itself.

The laptop that fills the stick has to know what the offline machine already
has, or it packs every wheel in the lock every time - which for this project is
torch, and torch is over 2 GB. It cannot ask: there is no network between them
and there never will be. So the machine writes it down, on the stick, in the
one place that travels between the two.

Written EARLY in an update - before anything is replaced - so that even an
update that fails teaches the laptop what this machine has. The failed trip is
already wasted; it must not also be uninformative.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from vmd.update.version import read_version

MACHINES = "machines"


def installed_libraries(root: Path | str) -> dict[str, str]:
    """Every library in this copy's .venv, as name -> version.

    Read off the `.dist-info` folders rather than by asking pip or uv, because
    this runs on a machine in the middle of an update, sometimes with no working
    environment at all. A directory listing cannot fail the way a subprocess
    can.

    Names are lowercased and their separators normalised, which is what makes
    them comparable with the names in uv.lock: PySide6_Essentials and
    pyside6-essentials are the same library, and treating them as two is a 90 MB
    wheel copied for nothing.
    """
    site = Path(root) / ".venv" / "Lib" / "site-packages"
    found: dict[str, str] = {}
    if not site.is_dir():
        return found
    for info in site.glob("*.dist-info"):
        stem = info.name[: -len(".dist-info")]
        if "-" not in stem:
            continue
        name, _, version = stem.rpartition("-")
        found[normalise(name)] = version
    return found


def normalise(name: str) -> str:
    """PEP 503: the one spelling of a package name that both sides agree on."""
    return re.sub(r"[-_.]+", "-", name).lower()


def note(root: Path | str, machine: str, when: str) -> dict:
    return {
        "machine": machine,
        "version": read_version(root),
        "libraries": installed_libraries(root),
        "written": when,
    }


def write_note(root: Path | str, stick: Path | str, machine: str, when: str) -> Path:
    """Write this machine's note onto the stick and return where it went.

    One file per machine, named after the machine, so that one stick can serve
    several sites without either of them packing for the other.
    """
    folder = Path(stick) / MACHINES
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{machine}.json"
    path.write_text(json.dumps(note(root, machine, when), indent=1), encoding="utf-8")
    return path
