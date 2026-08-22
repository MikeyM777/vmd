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

import json
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

# Written beside the copied files in previous\<version>\, recording which of
# the backed-up names actually existed before the update. restore uses it to
# tell "this name should be put back" apart from "this name should never have
# existed" - the second case has nothing to copy, only something to remove.
KEPT_MANIFEST = ".kept.json"


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

    Every name asked about is recorded in KEPT_MANIFEST, even the ones that do
    not exist yet - a name absent from the install today but about to be
    created by this update. Without that record, restore cannot tell "this was
    never here" from "the backup happens to have nothing to copy", and a
    rollback would leave behind whatever the update introduced.
    """
    root = Path(root)
    kept = root / PREVIOUS / (str(version) if version is not None else "unknown")
    if kept.exists():
        shutil.rmtree(kept)
    kept.mkdir(parents=True)
    existed = {}
    for name in names:
        source = root / name
        present = source.exists()
        existed[name] = present
        if not present:
            continue
        if source.is_dir():
            shutil.copytree(source, kept / name)
        else:
            shutil.copy2(source, kept / name)
    (kept / KEPT_MANIFEST).write_text(json.dumps(existed, sort_keys=True), encoding="utf-8")
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
            _prune_removed(source, target)
        else:
            replace_file(source, target)
        copied.append(name)
    return copied


def _prune_removed(source: Path, target: Path) -> None:
    """Delete from target whatever source no longer has, after a merge.

    copy_in merges the new tree onto the old one with dirs_exist_ok=True
    instead of replacing the directory outright, because emptying the live
    folder before the new one has fully landed would leave a console that
    cannot even run its own broken half if the copy dies partway through.
    Copy first, prune second - the live tree is only ever missing files for
    the moment it takes to delete them, never for the moment it takes to
    write everything new.

    The cost of merging instead of replacing is that a file deleted in the
    new version is never removed by the merge on its own - it stays on disk,
    and Python imports it exactly as happily as it imports a current one.
    That is a console quietly running part of one release and part of
    another, with nothing in the log to say so. This walks the merged
    directory afterward, removes any file the new tree does not claim at the
    same relative path, and then removes any directory that pruning left
    empty - never touching a directory that still holds something.
    """
    # Deepest paths first, so a directory is only checked for emptiness after
    # everything that used to be inside it has already been dealt with.
    for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        counterpart = source / path.relative_to(target)
        if path.is_dir():
            if not any(path.iterdir()):
                path.rmdir()
        elif not counterpart.is_file():
            path.unlink()


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
    aside = _unused_aside(target)
    target.rename(aside)
    shutil.copy2(source, target)


def _unused_aside(target: Path) -> Path:
    """A name next to target that nothing currently claims.

    This used to be a single fixed name, deleted if it existed before the
    rename. That delete could itself fail - the leftover from a previous run
    can be held open by an antivirus scanner or by a process that has not
    finished exiting yet - and an unhandled PermissionError there would crash
    an update at the exact moment it looked safe to proceed. Counting up to a
    name nothing owns removes the delete from the critical path entirely:
    nothing ever has to be freed before the rename can happen.
    """
    candidate = target.with_name(f"{target.name}.old-replaced")
    counter = 1
    while candidate.exists():
        candidate = target.with_name(f"{target.name}.old-replaced.{counter}")
        counter += 1
    return candidate


def restore(kept: Path | str, root: Path | str) -> list[str]:
    """Put a kept copy back over the install. Returns what was restored.

    A KEPT_MANIFEST beside the copied files, if the backup wrote one, also
    tells restore which names existed before the update at all - anything
    recorded as absent is removed rather than left in place, so a rollback
    undoes an addition as completely as it undoes a change. A previous\\
    folder written before this manifest existed has none, and restore falls
    back to putting back only what it was actually given, exactly as it
    always did.
    """
    kept = Path(kept)
    root = Path(root)
    existed = _read_kept_manifest(kept)
    restored = []
    for entry in sorted(kept.iterdir()):
        if entry.name == KEPT_MANIFEST:
            continue
        target = root / entry.name
        if entry.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(entry, target)
        else:
            replace_file(entry, target)
        restored.append(entry.name)
    for name, was_present in existed.items():
        if was_present:
            continue
        target = root / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            restored.append(name)
        elif target.exists():
            target.unlink()
            restored.append(name)
    return restored


def _read_kept_manifest(kept: Path) -> dict:
    """What back_up recorded about which names existed, or {} if it did not."""
    manifest = kept / KEPT_MANIFEST
    if not manifest.is_file():
        return {}
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
