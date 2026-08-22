"""The updater itself: everything that is dangerous, in one place.

Run as its own process, by the interpreter in bin\\python\\, out of a COPY of
this tree in the temporary folder - see `vmd/update/runner.py`. It replaces the
files the console is made of, so it cannot be a thread inside the console, and
it cannot be run from the folder it is rewriting.

Stdlib only. It runs at the moment the environment is being replaced, so an
import of anything from .venv would be an updater that stops working exactly
when it is needed.

The order below is the whole design, and every step of it is there because of
what it prevents:

  verify        a stick that half arrived is refused before anything is touched
  note          what this machine has, written to the stick early, so a failed
                update still teaches the laptop something
  stop          nothing is replaced under a running program
  keep          the old program is copied aside before the first byte is written
  copy          the new program, by whitelist, never the machine's own things
  libraries     only when the lock changed, and only from the stick
  selftest      the new version has to prove it runs
  start / undo  and if it does not, the old one goes back
"""

from __future__ import annotations

import shutil
from pathlib import Path

# What an update is allowed to replace. A list of names rather than a rule,
# because a rule ("everything except...") is one refactor away from copying the
# .venv over itself.
COPY_IN = ("vmd", "scripts", "docs", "VERSION", "pyproject.toml", "uv.lock", "VMD.exe")
COPY_SUFFIXES = (".bat",)

# What is never touched, said out loud so it can be tested and read. Some of it
# is this site's own - its camera, its passwords, its footage - and some of it
# is simply not part of an update.
KEEP_OUT = (
    "settings.json",
    "go2rtc.json",
    "streaming.json",
    "detection.json",
    "cameras",
    "recordings",
    "footage",
    "clips",
    "bin",
    ".venv",
    "Ultralytics",
    "previous",
)

PREVIOUS = "previous"


def what_to_copy(files: Path | str) -> list[str]:
    """The names in the update that this machine will take."""
    files = Path(files)
    taken = []
    for entry in sorted(files.iterdir()):
        if entry.name in KEEP_OUT:
            continue
        if entry.name in COPY_IN or entry.suffix.lower() in COPY_SUFFIXES:
            taken.append(entry.name)
    return taken


def back_up(root: Path | str, version: int | None, names) -> Path:
    """Copy what is about to be overwritten into previous\\<version>\\.

    Only the names being replaced. A backup that also held settings.json would
    be a rollback that puts an old camera password back, which is a fault
    nobody would think to look for.
    """
    root = Path(root)
    kept = root / PREVIOUS / (str(version) if version is not None else "unknown")
    if kept.exists():
        shutil.rmtree(kept)
    kept.mkdir(parents=True)
    for name in names:
        source = root / name
        if not source.exists():
            continue
        if source.is_dir():
            shutil.copytree(source, kept / name)
        else:
            shutil.copy2(source, kept / name)
    return kept


def copy_in(files: Path | str, root: Path | str) -> list[str]:
    """Put the update in place. Returns what was copied."""
    files = Path(files)
    root = Path(root)
    copied = []
    for name in what_to_copy(files):
        source = files / name
        target = root / name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            replace_file(source, target)
        copied.append(name)
    return copied


def replace_file(source: Path, target: Path) -> None:
    """Copy one file over another, even when Windows will not have it replaced.

    VMD.exe is the reason. Windows refuses to overwrite an executable that is
    still open - which it can be moments after the console closed - but it will
    let one be RENAMED: the holder keeps the file it opened, under its new
    name. The leftover is deleted on the next run, when whatever held it has
    gone. `scripts/install.ps1` does the same thing to bin\\uv.exe, for the same
    reason and after the same bug.
    """
    for stale in target.parent.glob(f"{target.name}.old-*"):
        try:
            stale.unlink()
        except OSError:
            pass
    try:
        shutil.copy2(source, target)
        return
    except OSError:
        pass
    aside = target.with_name(f"{target.name}.old-replaced")
    if aside.exists():
        aside.unlink(missing_ok=True)
    target.rename(aside)
    shutil.copy2(source, target)


def restore(kept: Path | str, root: Path | str) -> list[str]:
    """Put a kept copy back over the install. Returns what was restored."""
    kept = Path(kept)
    root = Path(root)
    restored = []
    for entry in sorted(kept.iterdir()):
        target = root / entry.name
        if entry.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(entry, target)
        else:
            replace_file(entry, target)
        restored.append(entry.name)
    return restored
