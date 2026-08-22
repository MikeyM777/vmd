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

  note          what this machine has, written to the stick first of all, so a
                failed update still teaches the laptop something
  verify        a stick that half arrived is refused before anything is touched
  stop          nothing is replaced under a running program
  keep          the old program is copied aside before the first byte is written
  copy          the new program, by whitelist, never the machine's own things
  libraries     only when the lock changed, and only from the stick
  selftest      the new version has to prove it runs
  start / undo  and if it does not, the old one goes back

Throughout the dangerous half of that list there is a marker file,
bin\\logs\\update-in-progress.json, which exists for exactly as long as the
install is not known to be whole. A power cut leaves it behind, and that is how
the NEXT start of the console knows this copy was interrupted rather than
finished. It is cleared on every path that ends with a tree somebody can trust -
and deliberately left up on the one path that does not, a rollback that could
not be carried out.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from vmd.update import manifest as manifest_module
from vmd.update.note import write_note
from vmd.update.stick import FILES, MANIFEST_JSON, UPDATE_JSON
from vmd.update.version import read_version

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

# Where the console looks to find out what is happening. bin\ is never copied
# over by an update, so the log of an update survives the update it describes.
LOGS = Path("bin") / "logs"
LOG = "update.log"
STATUS = "update-status.json"
MARKER = "update-in-progress.json"

# Where the report goes when bin\logs cannot be written to at all - because it
# is a file rather than a folder, or the disk it is on is full. The console
# will not find it there, but the console is not the only reader: somebody is
# going to stand at this machine afterwards and ask what happened, and %TEMP%
# is an answer where silence is not one.
ELSEWHERE = "vmd-update"

# How many lines of what the steps said are kept. A sync that goes wrong can
# say a great deal, and the console re-reads the whole status file every second
# while it waits.
OUTPUT_LINES = 200

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

    A junction or symlink placed inside target reads as an ordinary
    directory to naive iteration, and following it walks whatever it points
    at - which can be anywhere on the machine, including somebody's data
    outside the install entirely. Nothing in this codebase creates such a
    link, but "nothing creates it" is not "it cannot be there", so this never
    descends into one: see _prune_directory and _confined below.
    """
    _prune_directory(source, target, target)


def _prune_directory(source_root: Path, target_root: Path, directory: Path) -> None:
    """Prune one directory, already known to be a real directory, in place.

    Walked by hand rather than with rglob, because rglob has no way to say
    "list what is in here without following that": it would step through a
    junction exactly as it steps through a real subdirectory, and every path
    found beneath it would relative_to() as if it were an ordinary part of
    target_root. That is precisely how a junction at vmd\\escape pointing
    outside the install let a real file outside the whitelisted tree get
    deleted with no confirmation - the walk never knew it had left target_root
    at all. Recursing by hand means every directory is looked at, and decided
    about, before anything beneath it is ever touched.
    """
    for entry in sorted(directory.iterdir()):
        if entry.is_symlink() or entry.is_junction():
            # A link is never something copy_in itself could have produced -
            # shutil.copytree and shutil.copy2 always write real files and
            # real directories, never a link - so any link found here came
            # from outside this update entirely, the same way the reported
            # junction did. Deciding whether it is "carried" by the new
            # version and following it to find out would mean walking
            # whatever it points at, which is the fault being fixed. So a
            # link is never adjudicated, never walked into, and never
            # deleted here, regardless of whether the new version has a
            # same-named entry.
            continue
        if not _confined(entry, target_root):
            # Belt beneath the check above, not instead of it: a path can
            # still resolve outside target_root through a reparse point
            # higher up in the tree, or through some other reparse kind that
            # is_symlink() and is_junction() do not both catch. Nothing below
            # is deleted unless it demonstrably still lives inside the
            # directory this call was asked to prune.
            continue
        if entry.is_dir():
            _prune_directory(source_root, target_root, entry)
            if not any(entry.iterdir()):
                entry.rmdir()
        else:
            counterpart = source_root / entry.relative_to(target_root)
            if not counterpart.is_file():
                entry.unlink()


def _confined(path: Path, root: Path) -> bool:
    """Whether path, once every link along it is resolved, is still under root.

    resolve() follows symlinks and junctions to where they actually point,
    the same resolution Windows itself would use to open the file - so this
    is the one check that cannot be fooled by a kind of reparse point nobody
    has thought to name yet.
    """
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


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


@dataclass
class Report:
    """What happened, in the words the console shows and the log keeps."""

    ok: bool = False
    message: str = ""
    step: str = ""
    moved_from: int | None = None
    moved_to: int | None = None
    output: list[str] = field(default_factory=list)


class Progress:
    """The log and the status file, which are how the console watches this.

    The console cannot watch this process any other way: it is a separate
    program, started detached, that will still be running when the console has
    been killed. So every step is written down as it happens, and the panel
    reads the file.

    The Report and the status file are never assembled separately - the file is
    always written out of the Report, by write_status, and the Report holds
    exactly what the file holds, trimmed in one place only. The Report matters
    to almost nobody: it is returned into a process that is about to exit. The
    file is what is read.

    Nothing here raises. Every one of these methods is called before, during and
    after the install has been opened up, in a process nobody is watching, and a
    log folder that turns out to be a file must not be the thing that ends an
    update in silence.
    """

    def __init__(self, root: Path) -> None:
        self.report = Report()
        self.folder = _log_folder(root)
        # A whole status straight away, because probing the folder creates the
        # file: a panel that read it in between would get nought bytes, which
        # is neither valid JSON nor an absent file.
        self.write_status(finished=False)

    def begin(self, when: str) -> None:
        """Start a new run in the log, on its own line, with a rule above it.

        Two updates in one visit used to run into one another in the same file
        with nothing to say where one ended, so the second read as more of the
        first.
        """
        self._append(f"\n----- update started {when} -----")

    def say(self, step: str, line: str = "") -> None:
        self.report.step = step
        if line:
            self.report.output.append(line)
            # Trimmed here and nowhere else, so that what the Report keeps and
            # what the file holds are the same list rather than two lists that
            # are meant to agree.
            del self.report.output[:-OUTPUT_LINES]
        self._append(f"{step}{': ' + line if line else ''}")
        self.write_status(finished=False)

    def _append(self, line: str) -> None:
        """One line into the log, with the time it happened, or nothing at all.

        Timestamped because the questions asked afterwards are "how long did it
        sit there" and "was this before or after the power went", and a list of
        steps with no clock answers neither.
        """
        try:
            with open(self.folder / LOG, "a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now().isoformat(timespec='seconds')}  {line}\n")
        except OSError:
            # The log is the courtesy; the status file is the answer. Losing
            # the first is not a reason to lose the second as well.
            pass

    def write_status(self, finished: bool) -> None:
        payload = {
            "step": self.report.step,
            "ok": self.report.ok if finished else None,
            "message": self.report.message,
            "from": self.report.moved_from,
            "to": self.report.moved_to,
            "output": self.report.output,
            "finished": finished,
        }
        try:
            (self.folder / STATUS).write_text(json.dumps(payload, indent=1), encoding="utf-8")
        except OSError:
            pass

    def finish(self, ok: bool, message: str) -> Report:
        self.report.ok = ok
        self.report.message = message
        self.report.step = ""
        self.write_status(finished=True)
        return self.report


def _log_folder(root: Path | str) -> Path:
    """The first folder that will take this run's status file.

    bin\\logs\\ normally. But it is asked for by name on a machine that is about
    to be rewritten, and it can be a file, or on a disk that is full, or hold a
    directory called update-status.json - and this used to be a bare mkdir
    outside every try in run, so any of those ended the updater before it had
    written a word, with the console already waiting on a status file that
    runner.start had just deleted. So it is probed by actually opening the file
    that matters, and when that cannot be done anywhere under root the report
    goes to %TEMP% instead. The console will not look there, but an update that
    left a record somewhere is recoverable by a person and one that left none
    is not.
    """
    elsewhere = Path(tempfile.gettempdir()) / ELSEWHERE
    for folder in (Path(root) / LOGS, elsewhere):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            # Append, not write: this must not truncate a status file, and it
            # must fail the same way a real write would.
            with open(folder / STATUS, "a", encoding="utf-8"):
                pass
            return folder
        except OSError:
            continue
    return elsewhere


def _named(version: int | None) -> str:
    """The name of a version already in hand: VMD 8, or what describe() says.

    describe() reads VERSION off the disk, and by the time these sentences are
    written the disk holds whichever version won - so the number has to come
    from what was read before anything was replaced. What this prevents is
    "VMD 8 did not start, so VMD None was put back", on the machine most likely
    to be updating: the one whose VERSION file cannot be read.
    """
    return f"VMD {version}" if version is not None else "VMD (version unknown)"


def run(root, stick, machine: str, when: str, stop, sync, selftest) -> Report:
    """Apply the update on the stick to the copy in `root`.

    `stop`, `sync` and `selftest` are handed in rather than called directly, and
    that is what makes this testable: the real ones kill processes, run uv and
    start a second interpreter, and none of those belong in a test that is about
    whether the right files end up in the right place. `vmd/update/runner.py`
    supplies the real three.

    Every way out of this function goes through Progress.finish, including the
    ways nobody planned for. An exception leaving here would end the detached
    process with the status file still saying finished: false, and the console
    would sit waiting on a program that is not running any more.

    The work is in _apply. This is the outer guard around it, because the part
    of an update before the marker goes up was the part left unprotected: the
    stick read, the shape checks, the verification. Those are as able to raise
    as anything below them and they run at the moment the console has already
    thrown away the previous status file and started watching for a new one.
    """
    progress = Progress(root)
    progress.begin(when)
    try:
        return _apply(progress, Path(root), Path(stick), machine, when, stop, sync, selftest)
    except Exception as failure:
        # Only the prologue can reach here: everything from the marker onward
        # has its own handler, which returns rather than raises. So nothing on
        # this machine has been replaced at the point this runs.
        said = f"{type(failure).__name__}: {failure}"
        progress.say("something went wrong", said)
        return progress.finish(
            False, f"The update stopped before anything was replaced ({said}). Nothing was changed."
        )


def _apply(
    progress: Progress, root: Path, stick: Path, machine: str, when: str, stop, sync, selftest
) -> Report:
    """The order itself. See run, which is the guard around this."""
    progress.report.moved_from = read_version(root)
    files = stick / FILES

    # First of all, before the stick has even been read, because the commonest
    # way for an update to fail is for it to be refused - a stick that was
    # written badly, or that carries a version this machine already has. Those
    # trips are wasted either way; writing the note first is what stops them
    # being wasted twice, by at least telling the laptop what to pack next
    # time. Nothing on this machine is touched by writing a file to the stick.
    progress.say("writing this machine's note onto the stick")
    try:
        write_note(root, stick, machine=machine, when=when)
    except Exception as failure:
        # Anything at all, because the note is a courtesy to the laptop that
        # packs the next stick. It must never be the reason an update this
        # machine could have had did not happen.
        progress.say("writing this machine's note onto the stick", str(failure))

    try:
        update = json.loads((stick / UPDATE_JSON).read_text(encoding="utf-8"))
        listed = json.loads((stick / MANIFEST_JSON).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as failure:
        return progress.finish(False, f"The stick could not be read: {failure}")
    if not isinstance(update, dict) or not isinstance(listed, dict):
        # Valid JSON that is not an object - a list, a bare number. Everything
        # below asks these two for keys, and asking a list for a key is an
        # AttributeError out of a process nobody is watching.
        return progress.finish(
            False,
            "The stick could not be read: update.json and manifest.json are not "
            "in the shape a stick's are. Build it again on the laptop.",
        )
    if not isinstance(update.get("version"), int):
        # stick.look refuses this too, but run is the layer that does the
        # damage and cannot assume it was reached through the panel. The cost
        # of trusting it is an operator told "Updated to VMD None".
        return progress.finish(
            False,
            "The stick does not say which version it carries, so it cannot be "
            "used. Build it again on the laptop.",
        )
    progress.report.moved_to = update["version"]

    progress.say("checking the stick")
    problems = manifest_module.verify(files, listed)
    if problems:
        for line in problems:
            progress.say("checking the stick", line)
        rest = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
        return progress.finish(
            False, f"The stick is damaged: {problems[0]}{rest}. Nothing was changed."
        )

    # The marker goes up before the first dangerous step and comes down only
    # when this copy is known to be whole again - which is not the same as
    # "when this function returns". It starts down, because nothing has been
    # written yet; it goes up the moment there is a backup to put back; and it
    # comes down again only where the tree has been proved consistent, whether
    # that is by the new version passing or by the old one going back.
    marker = progress.folder / MARKER
    leave_marker = False
    kept = None
    try:
        # Inside the try, so that a marker that cannot be written is reported
        # like any other failure instead of ending the process silently.
        marker.write_text(json.dumps({"started": when, "to": update["version"]}), encoding="utf-8")
        progress.say("stopping the console")
        stop()

        names = what_to_copy(files)
        progress.say("keeping the version that is here now")
        kept = back_up(root, progress.report.moved_from, names)
        # From here the install is being rewritten. The marker now stays up
        # through anything this function does not catch - a KeyboardInterrupt
        # or a SystemExit, which except Exception rightly does not take, but
        # whose half-written tree is exactly what the marker is for.
        leave_marker = True

        progress.say("copying the new version in")
        copy_in(files, root)

        progress.say("installing any new libraries")
        installed, said = sync(stick)
        if not installed:
            progress.say("installing any new libraries", said)
            if not _put_back(progress, kept, root):
                return progress.finish(False, _stranded(progress, kept, said))
            leave_marker = False
            return progress.finish(
                False,
                f"The libraries this update needs could not be installed ({said}). "
                f"{_named(progress.report.moved_from)} was put back. Nothing was lost.",
            )

        progress.say("checking that the new version runs")
        works, said = selftest()
        if not works:
            progress.say("checking that the new version runs", said)
            if not _put_back(progress, kept, root):
                return progress.finish(False, _stranded(progress, kept, said))
            leave_marker = False
            return progress.finish(
                False,
                f"{_named(progress.report.moved_to)} did not start, so "
                f"{_named(progress.report.moved_from)} was put back. Nothing was lost.",
            )

        leave_marker = False
        return progress.finish(
            True,
            f"Updated to {_named(progress.report.moved_to)}. "
            f"The console will start again by itself.",
        )
    except Exception as failure:
        # Deliberately everything. What is being caught is not a known fault
        # but an unknown one - a full disk, a permission nobody expected, a
        # bug in the lines above - happening with the install already open. The
        # alternative is a traceback into a detached process's stderr, which
        # nobody is reading, and a console still waiting.
        said = f"{type(failure).__name__}: {failure}"
        progress.say("something went wrong", said)
        if kept is None:
            # Nothing had been written yet: the backup is taken before the
            # first byte of the new version goes in, so no backup means no
            # change to undo.
            leave_marker = False
            return progress.finish(
                False, f"The update stopped before anything was replaced ({said}). "
                f"Nothing was changed."
            )
        if not _put_back(progress, kept, root):
            return progress.finish(False, _stranded(progress, kept, said))
        leave_marker = False
        return progress.finish(
            False,
            f"The update failed ({said}), so {_named(progress.report.moved_from)} was "
            f"put back. Nothing was lost.",
        )
    finally:
        if not leave_marker:
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                # An exception raised in a finally clause replaces the return
                # value that was on its way out, which would throw away the
                # only report of what happened over a leftover file. A marker
                # that will not delete costs one false "an update was
                # interrupted" on the next start; losing the report costs the
                # console its answer.
                pass


def _put_back(progress: Progress, kept: Path, root: Path) -> bool:
    """Undo the copy. False if the undoing itself could not be done.

    The one failure with no good answer: a file the rollback has to overwrite
    is held open by something that did not die when the console was stopped. It
    is caught rather than raised so that the operator is told, in the status
    file, what state the machine is actually in - a rollback that fails
    silently is a machine that boots into half of two versions.
    """
    progress.say("putting the previous version back")
    try:
        restore(kept, root)
    except Exception as failure:
        progress.say("putting the previous version back", f"{type(failure).__name__}: {failure}")
        return False
    return True


def _stranded(progress: Progress, kept: Path, said: str) -> str:
    """What to tell somebody standing at a machine that is now half updated."""
    return (
        f"The update failed ({said}) and putting {_named(progress.report.moved_from)} "
        f"back failed as well, so this copy is half updated and must not be "
        f"started. The version that was here is kept in {kept}. Restart the "
        f"machine and try the update again, or copy that folder back by hand."
    )
