"""The half of the updater that moves files about."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from vmd.update import apply as apply_module
from vmd.update.apply import (
    ESSENTIAL,
    KEEP_OUT,
    missing_essentials,
    OUTPUT_LINES,
    Progress,
    Report,
    back_up,
    copy_in,
    replace_file,
    restore,
    run,
    what_to_copy,
)
from vmd.update.manifest import write as write_manifest


def an_install(root: Path) -> Path:
    """A folder shaped like a real one: program files, and the machine's own."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "vmd").mkdir()
    (root / "vmd" / "app.py").write_text("old\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "install.ps1").write_text("old\n", encoding="utf-8")
    (root / "VMD.bat").write_text("old\n", encoding="utf-8")
    (root / "VERSION").write_text("7", encoding="utf-8")

    (root / "settings.json").write_text('{"mine": true}', encoding="utf-8")
    (root / "cameras").mkdir()
    (root / "cameras" / "250").mkdir()
    (root / "cameras" / "250" / "settings.json").write_text("{}", encoding="utf-8")
    (root / "recordings").mkdir()
    (root / "recordings" / "clip.mp4").write_bytes(b"footage")
    (root / "bin").mkdir()
    (root / "bin" / "uv.exe").write_bytes(b"binary")
    (root / ".venv").mkdir()
    (root / ".venv" / "marker").write_text("env", encoding="utf-8")
    return root


def whole(folder: Path) -> Path:
    """Add the names a payload cannot be a whole copy of VMD without.

    `run` refuses a payload missing any of them - see the note on ESSENTIAL -
    so a fixture without them would test that refusal on every case rather than
    the thing each test is actually about.
    """
    for name in ESSENTIAL:
        part = folder / name
        part.parent.mkdir(parents=True, exist_ok=True)
        if not part.exists():
            part.write_text("new\n", encoding="utf-8")
    return folder


def new_files(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "vmd").mkdir()
    (folder / "vmd" / "app.py").write_text("new\n", encoding="utf-8")
    (folder / "VMD.bat").write_text("new\n", encoding="utf-8")
    (folder / "VERSION").write_text("8", encoding="utf-8")
    return whole(folder)


def test_only_the_program_is_copied(tmp_path: Path) -> None:
    files = new_files(tmp_path / "files")
    assert sorted(what_to_copy(files)) == ["VERSION", "VMD.bat", "vmd"]


def test_nothing_belonging_to_the_machine_is_ever_copied_over(tmp_path: Path) -> None:
    """The list that matters. Everything in it is either this site's own - its
    camera, its footage, its passwords - or is bigger than the update and was
    not in it."""
    assert {"settings.json", "cameras", "recordings", "bin", ".venv"} <= set(KEEP_OUT)


def test_copying_replaces_the_program_and_leaves_the_rest_alone(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    files = new_files(tmp_path / "files")

    copy_in(files, root)

    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "new\n"
    assert (root / "VERSION").read_text(encoding="utf-8") == "8"
    assert (root / "settings.json").read_text(encoding="utf-8") == '{"mine": true}'
    assert (root / "cameras" / "250" / "settings.json").is_file()
    assert (root / "recordings" / "clip.mp4").read_bytes() == b"footage"
    assert (root / "bin" / "uv.exe").read_bytes() == b"binary"
    assert (root / ".venv" / "marker").is_file()
    # Untouched by this update because it was not in it.
    assert (root / "scripts" / "install.ps1").read_text(encoding="utf-8") == "old\n"


def test_the_old_program_is_kept_before_anything_is_written(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    files = new_files(tmp_path / "files")

    kept = back_up(root, version=7, names=what_to_copy(files))

    assert kept == root / "previous" / "7"
    assert (kept / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert (kept / "VERSION").read_text(encoding="utf-8") == "7"
    # Not the machine's own things: a backup with a settings file in it is a
    # rollback that can put somebody else's camera password back.
    assert not (kept / "settings.json").exists()


def test_putting_the_old_one_back_undoes_the_copy(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    files = new_files(tmp_path / "files")
    kept = back_up(root, version=7, names=what_to_copy(files))
    copy_in(files, root)

    restore(kept, root)

    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert (root / "VERSION").read_text(encoding="utf-8") == "7"
    assert (root / "settings.json").read_text(encoding="utf-8") == '{"mine": true}'


def test_a_second_backup_of_the_same_version_replaces_the_first(tmp_path: Path) -> None:
    """Two updates in one visit. The second must not fail because the first
    left a folder behind, and must not keep a half-written one."""
    root = an_install(tmp_path / "VMD")
    files = new_files(tmp_path / "files")
    back_up(root, version=7, names=what_to_copy(files))
    (root / "previous" / "7" / "stray.txt").write_text("x", encoding="utf-8")

    kept = back_up(root, version=7, names=what_to_copy(files))

    assert not (kept / "stray.txt").exists()


def test_a_module_deleted_upstream_is_removed_by_the_merge(tmp_path: Path) -> None:
    """copy_in merges rather than replaces, so a module the new version no
    longer has would otherwise stay on disk and importable forever - a
    console quietly running part of one release and part of another."""
    root = an_install(tmp_path / "VMD")
    (root / "vmd" / "old_module.py").write_text("gone in the new one\n", encoding="utf-8")
    files = new_files(tmp_path / "files")

    copy_in(files, root)

    assert not (root / "vmd" / "old_module.py").exists()
    # Not part of this update, so not this update's business to touch.
    assert (root / "scripts" / "install.ps1").read_text(encoding="utf-8") == "old\n"


def test_pruning_removes_directories_left_empty_but_not_ones_still_in_use(
    tmp_path: Path,
) -> None:
    root = an_install(tmp_path / "VMD")
    (root / "vmd" / "empty_soon").mkdir()
    (root / "vmd" / "empty_soon" / "gone.py").write_text("old\n", encoding="utf-8")
    (root / "vmd" / "still_used").mkdir()
    (root / "vmd" / "still_used" / "kept.py").write_text("old\n", encoding="utf-8")
    files = new_files(tmp_path / "files")
    (files / "vmd" / "still_used").mkdir()
    (files / "vmd" / "still_used" / "kept.py").write_text("new\n", encoding="utf-8")

    copy_in(files, root)

    assert not (root / "vmd" / "empty_soon").exists()
    assert (root / "vmd" / "still_used" / "kept.py").read_text(encoding="utf-8") == "new\n"


def test_restore_removes_what_the_update_added_that_was_never_there_before(
    tmp_path: Path,
) -> None:
    """back_up records absence as well as presence, so a rollback can undo an
    addition, not just a change. Without that, VMD.exe would survive a
    rollback to a version that never had one."""
    root = an_install(tmp_path / "VMD")  # an_install has no VMD.exe
    files = new_files(tmp_path / "files")
    (files / "VMD.exe").write_bytes(b"new-exe")

    kept = back_up(root, version=7, names=what_to_copy(files))
    copy_in(files, root)
    assert (root / "VMD.exe").is_file()

    restore(kept, root)

    assert not (root / "VMD.exe").exists()


def test_restore_without_a_kept_manifest_still_restores(tmp_path: Path) -> None:
    """A previous\\ folder written by a version of back_up that predates the
    manifest has no .kept.json. restore must not require what an older
    backup never wrote - it falls back to putting back only what it has."""
    root = an_install(tmp_path / "VMD")
    files = new_files(tmp_path / "files")
    kept = back_up(root, version=7, names=what_to_copy(files))
    (kept / ".kept.json").unlink()
    copy_in(files, root)

    restore(kept, root)

    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert (root / "VERSION").read_text(encoding="utf-8") == "7"


def test_replace_file_survives_a_locked_leftover_from_a_previous_run(
    tmp_path: Path, monkeypatch
) -> None:
    """Stands in for two real Windows faults at once: an executable still
    open (copy2 fails) and a leftover rename from an earlier run that cannot
    be deleted either (unlink fails) - the kind of thing a slow-to-exit
    process or an antivirus scanner does, not something worth holding a real
    file handle open to test. replace_file must still succeed, by picking an
    aside name the stuck leftover does not occupy."""
    target = tmp_path / "VMD.exe"
    target.write_bytes(b"old")
    leftover = tmp_path / "VMD.exe.old-replaced"
    leftover.write_bytes(b"stale")
    source = tmp_path / "new" / "VMD.exe"
    source.parent.mkdir()
    source.write_bytes(b"new")

    real_unlink = Path.unlink

    def locked_unlink(self, *args, **kwargs):
        if self == leftover:
            raise PermissionError("locked")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_unlink)

    real_copy2 = shutil.copy2
    calls = {"n": 0}

    def flaky_copy2(src, dst, *args, **kwargs):
        if calls["n"] == 0 and Path(dst) == target:
            calls["n"] += 1
            raise OSError("locked")
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", flaky_copy2)

    replace_file(source, target)

    assert target.read_bytes() == b"new"
    assert leftover.read_bytes() == b"stale"  # could not be removed, left alone


def _make_junction(link: Path, target: Path) -> bool:
    """True if a real junction now sits at link, pointing at target.

    mklink /J needs no elevated privilege on Windows, unlike a symlink - which
    is exactly why a junction, not a symlink, is the realistic way this could
    turn up on a locked-down, air-gapped console.
    """
    if sys.platform != "win32":
        return False
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )
    return result.returncode == 0 and link.is_junction()


def test_pruning_never_follows_a_junction_out_of_the_tree_it_was_given(
    tmp_path: Path,
) -> None:
    """The reported escape: a junction inside vmd\\ pointing outside the
    install, an update that does not carry it, and a real file behind the
    junction that must survive copy_in untouched. See _prune_directory's
    docstring in vmd/update/apply.py for why rglob could not be trusted here -
    it would step through the junction as if it were an ordinary subdirectory
    and delete whatever it found on the other side."""
    root = an_install(tmp_path / "VMD")
    outside = tmp_path / "outside"
    outside.mkdir()
    real_file = outside / "real.txt"
    real_file.write_text("somebody's data\n", encoding="utf-8")

    link = root / "vmd" / "escape"
    if not _make_junction(link, outside):
        pytest.skip("could not create a junction on this machine")

    files = new_files(tmp_path / "files")  # does not carry vmd\escape

    copy_in(files, root)

    assert real_file.read_text(encoding="utf-8") == "somebody's data\n"


def test_a_path_that_resolves_outside_the_tree_is_skipped_not_deleted(
    tmp_path: Path, monkeypatch
) -> None:
    """The second, independent guard inside _prune_directory: even a path
    that does not read as a symlink or a junction at all is left alone if
    resolving it lands outside the directory being pruned - the belt beneath
    the link check, for a kind of reparse point nobody has named yet.
    Monkeypatched rather than built with a real link, because this is
    specifically exercising the case where is_symlink() and is_junction()
    both say no and the confinement check is the only thing left standing."""
    root = an_install(tmp_path / "VMD")
    mystery = root / "vmd" / "mystery.py"
    mystery.write_text("not part of the update, and not a link either\n", encoding="utf-8")
    files = new_files(tmp_path / "files")  # does not carry vmd\mystery.py

    outside = tmp_path / "outside" / "somewhere.py"
    outside.parent.mkdir()
    outside.write_text("elsewhere\n", encoding="utf-8")

    real_resolve = Path.resolve

    def lying_resolve(self, *args, **kwargs):
        if self == mystery:
            return outside
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", lying_resolve)

    copy_in(files, root)

    # Would have been pruned as unclaimed by the update, if not for the
    # resolved path landing outside root\vmd.
    assert mystery.exists()


def a_stick(folder: Path, version: int, files: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copytree(files, folder / "files")
    (folder / "update.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    write_manifest(folder / "files", folder / "manifest.json")
    return folder


def test_a_good_update_lands_and_reports_the_two_versions(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    report = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
                 stop=lambda: None, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    assert report.ok is True
    assert report.moved_from == 7 and report.moved_to == 8
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "new\n"


def test_the_note_is_written_before_anything_is_replaced(tmp_path: Path) -> None:
    """Even a refused update teaches the laptop what this machine has. The trip
    is already wasted; it must not also be uninformative."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))
    (stick / "files" / "VERSION").write_text("9", encoding="utf-8")  # breaks the manifest

    report = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
                 stop=lambda: None, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    assert report.ok is False
    assert "VERSION" in report.message
    assert (stick / "machines" / "WIN-TEST.json").is_file()
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"


def test_a_new_version_that_does_not_run_is_thrown_away(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    report = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
                 stop=lambda: None, sync=lambda *_: (True, ""),
                 selftest=lambda: (False, "ImportError: no module named cv2"))

    assert report.ok is False
    assert "did not start" in report.message
    assert "cv2" in "\n".join(report.output)
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert (root / "VERSION").read_text(encoding="utf-8") == "7"


def test_libraries_that_will_not_install_undo_the_update_too(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    report = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
                 stop=lambda: None,
                 sync=lambda *_: (False, "no wheel for numpy 2.2.0 on the stick"),
                 selftest=lambda: (True, ""))

    assert report.ok is False
    assert "numpy" in report.message
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"


def test_the_marker_is_up_while_it_runs_and_gone_afterwards(tmp_path: Path) -> None:
    """A power cut in the middle leaves the marker behind, and that is how the
    next start knows to offer a way back."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))
    seen = {}

    def stop() -> None:
        seen["marker"] = (root / "bin" / "logs" / "update-in-progress.json").is_file()

    run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
        stop=stop, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    assert seen["marker"] is True
    assert not (root / "bin" / "logs" / "update-in-progress.json").exists()


def test_every_step_is_written_where_the_console_can_read_it(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
        stop=lambda: None, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    status = json.loads((root / "bin" / "logs" / "update-status.json").read_text(encoding="utf-8"))
    assert status["finished"] is True and status["ok"] is True
    assert (root / "bin" / "logs" / "update.log").read_text(encoding="utf-8").strip()


def test_the_report_and_the_status_file_never_disagree(tmp_path: Path) -> None:
    """The console never sees the Report - it is returned to a process that is
    about to exit. What it sees is update-status.json. If the two could differ,
    every test written against the Report would be proving something about a
    value nobody reads."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    good = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
               stop=lambda: None, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    assert isinstance(good, Report)
    assert _status_from_report(root) == _as_status(good)

    bad = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
              stop=lambda: None, sync=lambda *_: (True, ""),
              selftest=lambda: (False, "ImportError: no module named cv2"))

    assert bad.ok is False
    assert _status_from_report(root) == _as_status(bad)


def _status(root: Path) -> dict:
    text = (root / "bin" / "logs" / "update-status.json").read_text(encoding="utf-8")
    return json.loads(text)


#: The keys in the status file that do not come from the Report at all: they
#: say WHO wrote it, so the console can tell an update that is running from one
#: that was killed. See Progress.write_status and UpdatePanel.already_running.
WRITER_KEYS = {"pid", "booted"}


def _status_from_report(root: Path) -> dict:
    """The status file with the writer's own identity taken back off.

    Taken off by name and checked, rather than filtered loosely: the test this
    serves exists to prove the file and the Report cannot drift, so a key
    appearing in one and not the other must still fail unless it is one of the
    two that is meant to be there.
    """
    status = _status(root)
    extra = set(status) - set(_as_status(Report()))
    assert extra == WRITER_KEYS, f"unexpected keys in the status file: {extra}"
    assert isinstance(status["pid"], int) and status["pid"] > 0
    for key in WRITER_KEYS:
        status.pop(key)
    return status


def _as_status(report: Report) -> dict:
    """The Report said the way the finished status file says it.

    Nothing is trimmed or reshaped on the way through. This used to slice
    report.output to its last 200 lines to match what the file holds, which
    made the test assume the very property it exists to prove - and hid a real
    divergence of 401 lines in the Report against 200 in the file.
    """
    return {
        "step": report.step,
        "ok": report.ok,
        "message": report.message,
        "from": report.moved_from,
        "to": report.moved_to,
        "output": report.output,
        "finished": True,
    }


def test_a_rollback_that_cannot_be_done_leaves_the_marker_up_and_says_so(
    tmp_path: Path, monkeypatch
) -> None:
    """The worst case there is: the new version does not run AND the old one
    cannot be put back, because something is holding a file open that will not
    die. The half-updated copy must not be reported as merely failed and must
    not have its marker cleared - the marker is the only thing that tells the
    next start of the console that this copy is not to be trusted."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    def stuck(*args, **kwargs):
        raise PermissionError("vmd\\app.py is held open by another process")

    monkeypatch.setattr(apply_module, "restore", stuck)

    report = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
                 stop=lambda: None, sync=lambda *_: (True, ""),
                 selftest=lambda: (False, "ImportError: no module named cv2"))

    assert report.ok is False
    assert "half updated" in report.message
    assert str(root / "previous" / "7") in report.message
    assert (root / "bin" / "logs" / "update-in-progress.json").is_file()
    assert _status(root)["finished"] is True


def test_a_step_that_throws_is_reported_rather_than_vanishing(
    tmp_path: Path, monkeypatch
) -> None:
    """run is the whole of a detached process. An exception escaping it kills
    that process with the status file still saying finished: false, and the
    console waits for a program that is no longer running until somebody gives
    up. Whatever happens has to come back as a finished status."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    def falls_over(*args, **kwargs):
        raise OSError("the disk filled up halfway through")

    monkeypatch.setattr(apply_module, "copy_in", falls_over)

    report = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
                 stop=lambda: None, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    assert report.ok is False
    assert "disk filled up" in "\n".join(report.output)
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert _status(root)["finished"] is True
    assert not (root / "bin" / "logs" / "update-in-progress.json").exists()


def test_nothing_is_touched_when_the_stick_cannot_be_read_at_all(tmp_path: Path) -> None:
    """A drive that has an update.json which is not JSON. There is nothing to
    verify and nothing to copy, so the install must be exactly as it was and
    the marker must never have gone up."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))
    (stick / "update.json").write_text("{not json", encoding="utf-8")

    report = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
                 stop=lambda: None, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    assert report.ok is False
    assert "could not be read" in report.message
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert (stick / "machines" / "WIN-TEST.json").is_file()
    assert not (root / "bin" / "logs" / "update-in-progress.json").exists()


def test_a_stick_whose_json_is_the_wrong_shape_is_refused_not_crashed_on(
    tmp_path: Path,
) -> None:
    """Valid JSON that is not an object. Everything downstream asks it for a
    key, and asking a list for a key raises out of a process nobody is
    watching, leaving the status file saying it is still working."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))
    (stick / "update.json").write_text("[8]", encoding="utf-8")

    report = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
                 stop=lambda: None, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    assert report.ok is False
    assert "could not be read" in report.message
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert _status(root)["finished"] is True


def a_run(root: Path, stick: Path, **replaced):
    """run with the three injected steps standing still unless a test says so."""
    steps = {"stop": lambda: None, "sync": lambda *_: (True, ""), "selftest": lambda: (True, "")}
    steps.update(replaced)
    return run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00", **steps)


def test_a_manifest_entry_that_is_not_a_file_is_refused_not_crashed_on(
    tmp_path: Path,
) -> None:
    """The shape check on update.json and manifest.json only reached the top
    level. Underneath it, verify indexed entry["size"] on whatever it found -
    a KeyError out of a detached process, with the status file left saying
    finished: false for ever and a console waiting on it."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    for damaged in ({"files": [{"path": "VERSION"}]}, {"files": ["VERSION"]}, {"files": 8}):
        (stick / "manifest.json").write_text(json.dumps(damaged), encoding="utf-8")

        report = a_run(root, stick)

        assert report.ok is False, damaged
        assert "damaged" in report.message
        assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
        assert _status(root)["finished"] is True


def test_a_stick_with_no_files_folder_is_refused_before_the_console_is_stopped(
    tmp_path: Path,
) -> None:
    """A manifest that lists nothing agrees with an empty stick, so verification
    passed, the console was killed, and only then did what_to_copy fall over -
    an operator reading "nothing was changed" on a machine that had just been
    shut down under him."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))
    shutil.rmtree(stick / "files")
    (stick / "manifest.json").write_text(json.dumps({"files": []}), encoding="utf-8")
    stopped = []

    report = a_run(root, stick, stop=lambda: stopped.append(True))

    assert report.ok is False
    assert "damaged" in report.message
    assert stopped == []
    assert not (root / "bin" / "logs" / "update-in-progress.json").exists()


def test_a_stick_that_does_not_say_which_version_it_carries_is_refused(
    tmp_path: Path,
) -> None:
    """stick.look already refuses this, but run is the layer that does the
    damage and cannot assume it was called through the panel. Without the
    check the operator is told "Updated to VMD None"."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))
    (stick / "update.json").write_text(json.dumps({"built": "today"}), encoding="utf-8")
    stopped = []

    report = a_run(root, stick, stop=lambda: stopped.append(True))

    assert report.ok is False
    assert "None" not in report.message
    assert stopped == []


def test_no_sentence_ever_says_vmd_none(tmp_path: Path) -> None:
    """A machine whose VERSION cannot be read is the likeliest one to be
    updating, and "so VMD None was put back" is not a sentence to show
    somebody standing in a plant room at night."""
    root = an_install(tmp_path / "VMD")
    (root / "VERSION").unlink()
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    report = a_run(root, stick, selftest=lambda: (False, "ImportError: no module named cv2"))

    assert report.ok is False
    assert "None" not in report.message
    assert "VMD (version unknown)" in report.message
    assert _status(root)["message"] == report.message


def test_the_status_still_reaches_somewhere_readable_when_the_log_folder_cannot_be_used(
    tmp_path: Path, monkeypatch
) -> None:
    """bin\\logs present as a FILE. mkdir raises, and it used to raise from
    outside every try in run - so nothing was written anywhere at all, while
    runner.start had already deleted the previous status file. The panel then
    waited on a file that was never going to appear."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))
    (root / "bin" / "logs").write_text("not a folder", encoding="utf-8")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp"))

    report = a_run(root, stick)

    assert report.ok is True
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "new\n"
    elsewhere = Path(tempfile.gettempdir()) / apply_module.ELSEWHERE
    status = json.loads((elsewhere / "update-status.json").read_text(encoding="utf-8"))
    assert status["finished"] is True and status["ok"] is True


def test_a_log_that_cannot_be_written_does_not_stop_the_update(tmp_path: Path) -> None:
    """update.log present as a DIRECTORY: opening it for append raises. The log
    is a courtesy; the status file is the answer, and it is a different file."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))
    (root / "bin" / "logs").mkdir()
    (root / "bin" / "logs" / "update.log").mkdir()

    report = a_run(root, stick)

    assert report.ok is True
    assert _status(root)["finished"] is True


@pytest.mark.parametrize("step", ["stop", "sync", "selftest"])
def test_the_three_injected_steps_are_allowed_to_raise(tmp_path: Path, step: str) -> None:
    """The real three kill processes, run uv and start an interpreter. Every
    one of them can raise something nobody listed, and none of them may end the
    updater without a finished status."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    def falls_over(*args, **kwargs):
        raise RuntimeError("the process would not die")

    report = a_run(root, stick, **{step: falls_over})

    assert report.ok is False
    assert "would not die" in report.message
    # Whether it stopped before anything was written or was put back after,
    # what is on the disk afterwards is the version that was there before.
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert (root / "VERSION").read_text(encoding="utf-8") == "7"
    assert _status(root)["finished"] is True
    assert not (root / "bin" / "logs" / "update-in-progress.json").exists()


def test_an_interrupt_after_the_backup_leaves_the_marker_up(tmp_path: Path, monkeypatch) -> None:
    """KeyboardInterrupt and SystemExit are deliberately not caught - but the
    finally still ran, and it cleared the marker. An interrupt in the middle of
    copy_in therefore left a half-updated install that looked untouched, which
    is the exact state the marker exists to make visible."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(apply_module, "copy_in", interrupted)

    with pytest.raises(KeyboardInterrupt):
        a_run(root, stick)

    assert (root / "bin" / "logs" / "update-in-progress.json").is_file()


def test_an_interrupt_before_anything_is_written_takes_the_marker_with_it(
    tmp_path: Path,
) -> None:
    """The other half of the same rule: nothing had been replaced yet, so there
    is nothing for the next start to warn about."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    def interrupted():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        a_run(root, stick, stop=interrupted)

    assert not (root / "bin" / "logs" / "update-in-progress.json").exists()


def test_the_report_keeps_exactly_what_the_status_file_keeps(tmp_path: Path) -> None:
    """Measured at 401 lines in the Report against 200 in the file. Progress
    says in its own docstring that the two cannot disagree; one list is how
    that is true rather than aspirational."""
    root = an_install(tmp_path / "VMD")
    progress = Progress(root)
    for number in range(OUTPUT_LINES * 2 + 1):
        progress.say("counting", f"line {number}")
    progress.finish(True, "done")

    assert len(progress.report.output) == OUTPUT_LINES
    assert _status(root)["output"] == progress.report.output
    # The end of the run is what says why it ended, so it is the end that is kept.
    assert progress.report.output[-1] == f"line {OUTPUT_LINES * 2}"


def test_the_log_says_when_and_starts_a_new_run_on_a_new_line(tmp_path: Path) -> None:
    """Two updates in one visit wrote into one another with nothing between
    them, so the log of the second read as a continuation of the first."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    a_run(root, stick)
    a_run(root, stick)

    log = (root / "bin" / "logs" / "update.log").read_text(encoding="utf-8")
    assert log.count("update started") == 2
    assert "2026-08-22T10:00:00" in log
    assert "stopping the console" in log


# --------------------------------------------------------------------------- #
#  A stick that is whole, not merely self-consistent
# --------------------------------------------------------------------------- #


def test_a_stick_missing_the_program_is_refused_before_anything_is_touched(
    tmp_path: Path,
) -> None:
    """The failure this exists for, reproduced.

    A write to the stick that is cut short leaves a stick whose manifest was
    generated from what actually landed - so every checksum matches and
    `verify` passes - while most of VMD is simply not on it. Copied in, the
    prune then deletes from the install every module the stick has not got. It
    took a real console to "No module named vmd.settings" with seven files left
    in the vmd package, reported success, and relaunched the program it had
    just gutted.
    """
    root = an_install(tmp_path / "VMD")
    files = new_files(tmp_path / "files")
    (files / "vmd" / "settings.py").unlink()
    # The manifest is written AFTER the truncation, exactly as the builder
    # writes it: from what is on the stick. This is what makes the stick
    # internally perfect and still unusable.
    stick = a_stick(tmp_path / "E", 8, files)

    report = run(root, stick, machine="WIN-TEST", when="2026-08-24T10:00:00",
                 stop=lambda: None, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    assert report.ok is False
    assert "settings.py" in report.message
    assert "Nothing was changed" in report.message
    # The install is exactly as it was: the old program, and the machine's own.
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert (root / "VERSION").read_text(encoding="utf-8") == "7"
    assert (root / "settings.json").read_text(encoding="utf-8") == '{"mine": true}'
    # And nothing was even begun: no backup, no marker.
    assert not (root / "previous").exists()
    assert not (root / "bin" / "logs" / "update-in-progress.json").exists()


def test_every_essential_name_is_checked_for(tmp_path: Path) -> None:
    """Each one on its own, so a list that quietly stops being enforced fails
    here rather than at a site."""
    for name in ESSENTIAL:
        root = an_install(tmp_path / f"VMD-{name.replace('/', '-')}")
        files = new_files(tmp_path / f"files-{name.replace('/', '-')}")
        (files / name).unlink()
        stick = a_stick(tmp_path / f"E-{name.replace('/', '-')}", 8, files)

        report = run(root, stick, machine="WIN-TEST", when="2026-08-24T10:00:00",
                     stop=lambda: None, sync=lambda *_: (True, ""),
                     selftest=lambda: (True, ""))

        assert report.ok is False, f"a stick with no {name} was accepted"
        assert (root / "VERSION").read_text(encoding="utf-8") == "7"


def test_a_whole_stick_is_still_installed(tmp_path: Path) -> None:
    """The guard must refuse the truncated stick and nothing else."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    report = run(root, stick, machine="WIN-TEST", when="2026-08-24T10:00:00",
                 stop=lambda: None, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    assert report.ok is True
    assert (root / "vmd" / "settings.py").is_file()


def test_missing_essentials_names_what_is_missing(tmp_path: Path) -> None:
    files = new_files(tmp_path / "files")
    assert missing_essentials(files) == []

    (files / "vmd" / "settings.py").unlink()
    assert missing_essentials(files) == ["vmd/settings.py"]
