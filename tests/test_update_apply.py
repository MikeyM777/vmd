"""The half of the updater that moves files about."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from vmd.update import apply as apply_module
from vmd.update.apply import (
    KEEP_OUT,
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


def new_files(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "vmd").mkdir()
    (folder / "vmd" / "app.py").write_text("new\n", encoding="utf-8")
    (folder / "VMD.bat").write_text("new\n", encoding="utf-8")
    (folder / "VERSION").write_text("8", encoding="utf-8")
    return folder


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
    assert _status(root) == _as_status(good)

    bad = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
              stop=lambda: None, sync=lambda *_: (True, ""),
              selftest=lambda: (False, "ImportError: no module named cv2"))

    assert bad.ok is False
    assert _status(root) == _as_status(bad)


def _status(root: Path) -> dict:
    text = (root / "bin" / "logs" / "update-status.json").read_text(encoding="utf-8")
    return json.loads(text)


def _as_status(report: Report) -> dict:
    """The Report said the way the finished status file says it."""
    return {
        "step": report.step,
        "ok": report.ok,
        "message": report.message,
        "from": report.moved_from,
        "to": report.moved_to,
        "output": report.output[-200:],
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
