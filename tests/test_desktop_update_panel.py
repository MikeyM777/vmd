"""The one control on this machine that changes the software."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from vmd.desktop.update_panel import UpdatePanel


def a_stick(folder: Path, version: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "update.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps({"files": []}), encoding="utf-8")
    (folder / "files").mkdir(exist_ok=True)
    return folder


def a_console(folder: Path, version: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "VERSION").write_text(str(version), encoding="utf-8")
    return folder


def a_marker(root: Path, **fields) -> Path:
    """The file left behind by an update that was cut off part of the way."""
    logs = root / "bin" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "update-in-progress.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


def a_status(root: Path, payload: dict, age_seconds: float = 0.0) -> Path:
    """The file the updater writes as it goes, as the panel finds it."""
    logs = root / "bin" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "update-status.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    if age_seconds:
        when = time.time() - age_seconds
        os.utime(path, (when, when))
    return path


def build(qtbot, root: Path, drives) -> UpdatePanel:
    panel = UpdatePanel(root=root, settings_path=root / "settings.json", drives=lambda: drives)
    qtbot.addWidget(panel)
    # Shown, because half of what this panel decides it says by hiding a
    # button, and a widget whose window was never shown reports every child as
    # invisible whatever it was told.
    panel.show()
    panel.look()
    return panel


def test_it_says_which_version_this_system_is(qtbot, tmp_path: Path) -> None:
    panel = build(qtbot, a_console(tmp_path / "VMD", 7), [])
    assert "VMD 7" in panel.this_system.text()


def test_with_no_stick_it_offers_to_look_again_and_not_to_update(
    qtbot, tmp_path: Path
) -> None:
    panel = build(qtbot, a_console(tmp_path / "VMD", 7), [])
    assert "No update stick" in panel.stick_line.text()
    assert panel.update_button.isEnabled() is False
    assert panel.look_button.isVisible() is True


def test_a_newer_stick_makes_the_update_button_live(qtbot, tmp_path: Path) -> None:
    stick = a_stick(tmp_path / "E", 8)
    panel = build(qtbot, a_console(tmp_path / "VMD", 7), [stick])
    assert "VMD 8" in panel.stick_line.text()
    assert panel.update_button.isEnabled() is True


def test_the_same_version_is_not_offered_as_an_update(qtbot, tmp_path: Path) -> None:
    stick = a_stick(tmp_path / "E", 7)
    panel = build(qtbot, a_console(tmp_path / "VMD", 7), [stick])
    assert panel.update_button.isEnabled() is False
    assert "same version" in panel.stick_line.text()


def test_going_back_is_offered_only_when_there_is_something_to_go_back_to(
    qtbot, tmp_path: Path
) -> None:
    root = a_console(tmp_path / "VMD", 8)
    panel = build(qtbot, root, [])
    assert panel.back_button.isVisible() is False

    (root / "previous" / "7").mkdir(parents=True)
    panel.look()
    assert panel.back_button.isVisible() is True
    assert "VMD 7" in panel.back_button.text()


def test_the_version_this_console_already_runs_is_not_offered_as_a_way_back(
    qtbot, tmp_path: Path
) -> None:
    """Nothing deletes previous\\7, so going back to VMD 7 leaves a machine
    running VMD 7 with a button offering VMD 7."""
    root = a_console(tmp_path / "VMD", 7)
    (root / "previous" / "7").mkdir(parents=True)

    panel = build(qtbot, root, [])

    assert panel.back_button.isVisible() is False


def test_going_back_asks_first_and_a_no_does_nothing(qtbot, tmp_path, monkeypatch) -> None:
    """One press away from undoing an update somebody has just travelled to
    deliver, on a machine where the way to redo it is another trip."""
    root = a_console(tmp_path / "VMD", 8)
    (root / "previous" / "7").mkdir(parents=True)
    panel = build(qtbot, root, [])
    started = []
    panel.start_rollback = lambda version: started.append(version)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    panel.go_back()

    assert started == []


def test_going_back_after_a_yes_starts_it(qtbot, tmp_path, monkeypatch) -> None:
    root = a_console(tmp_path / "VMD", 8)
    (root / "previous" / "7").mkdir(parents=True)
    panel = build(qtbot, root, [])
    started = []
    panel.start_rollback = lambda version: started.append(version)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    panel.go_back()

    assert started == [7]


def test_an_interrupted_update_is_reported_at_the_next_start(qtbot, tmp_path: Path) -> None:
    """The marker file left by a power cut. Nobody would think to look for it,
    so the panel says it in the one place they will be looking."""
    root = a_console(tmp_path / "VMD", 7)
    a_marker(root, to=8)

    panel = build(qtbot, root, [])

    assert "interrupted" in panel.stick_line.text().lower()


def test_an_update_cut_during_the_copy_still_offers_the_version_it_kept(
    qtbot, tmp_path, monkeypatch
) -> None:
    """The dead end. VERSION is one of the files an update copies in, so a cut
    during the copy leaves it still reading 7 with previous\\7 beside it. The
    rule that stops "Go back to VMD 7" appearing on a console running VMD 7
    then hid the only button that does anything, under a line telling the
    operator to press it - on the machine where this panel is the whole of
    maintenance, with a marker that no restart clears.

    While the marker is up, what VERSION says is not evidence of anything: it
    is a file that may or may not have been replaced yet."""
    root = a_console(tmp_path / "VMD", 7)
    (root / "previous" / "7").mkdir(parents=True)
    a_marker(root, started="2026-08-22T10:00:00", to=8)
    panel = build(qtbot, root, [])
    started = []
    panel.start_rollback = lambda version: started.append(version)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    assert panel.back_button.isVisible() is True
    assert "VMD 7" in panel.back_button.text()

    panel.go_back()

    assert started == [7]


def test_the_interrupted_line_names_the_version_going_on_and_the_one_kept(
    qtbot, tmp_path: Path
) -> None:
    """"An update was interrupted" leaves the operator with no idea which
    version this machine is now nor which one the button would bring back."""
    root = a_console(tmp_path / "VMD", 7)
    (root / "previous" / "7").mkdir(parents=True)
    a_marker(root, to=8)

    panel = build(qtbot, root, [])

    assert "VMD 8" in panel.stick_line.text()
    assert "VMD 7" in panel.stick_line.text()


def test_an_interrupted_update_offers_the_copy_it_took_not_the_oldest_one(
    qtbot, tmp_path: Path
) -> None:
    """A machine that has been updated before keeps more than one previous\\.
    The highest number in the folder is not the answer either: what this
    update kept is the version that was running when it started, and the
    marker is what says which version it was putting on."""
    root = a_console(tmp_path / "VMD", 7)
    for version in (6, 7):
        (root / "previous" / str(version)).mkdir(parents=True)
    a_marker(root, to=8)

    panel = build(qtbot, root, [])

    assert "VMD 7" in panel.back_button.text()


def test_an_interrupted_rollback_offers_to_finish_going_back(qtbot, tmp_path: Path) -> None:
    """A rollback raises a marker of its own, and the way out of one that was
    cut off is to finish it - not to go back to something else. Its marker
    says which copy it was putting back, and that is the button."""
    root = a_console(tmp_path / "VMD", 8)
    (root / "previous" / "7").mkdir(parents=True)
    a_marker(root, to=7, kept=7, rollback=True)

    panel = build(qtbot, root, [])

    assert panel.back_button.isVisible() is True
    assert "VMD 7" in panel.back_button.text()


def test_an_interrupted_update_with_nothing_kept_is_still_offered_the_stick(
    qtbot, tmp_path: Path
) -> None:
    """The other way out. With no kept copy on the machine there is nothing to
    go back to, so the update the stick is offering IS the repair - and a
    disabled Update button under an interrupted-update warning is a panel on
    which nothing at all can be pressed."""
    root = a_console(tmp_path / "VMD", 7)
    stick = a_stick(tmp_path / "E", 8)
    a_marker(root, to=8)

    panel = build(qtbot, root, [stick])

    assert panel.back_button.isVisible() is False
    assert panel.update_button.isEnabled() is True
    assert "interrupted" in panel.stick_line.text().lower()


def test_an_updater_that_never_starts_does_not_leave_the_panel_waiting_for_ever(
    qtbot, tmp_path: Path
) -> None:
    """`start` deletes the last update's status file before it spawns anything,
    so an updater that dies in its first second leaves no file at all - and a
    watcher that returns quietly on a missing file waits for the rest of the
    day, saying "the console will close and start again" to somebody whose
    console is not going to close."""
    stick = a_stick(tmp_path / "E", 8)
    panel = build(qtbot, a_console(tmp_path / "VMD", 7), [stick])
    now = [0.0]
    panel.clock = lambda: now[0]
    panel.start_update = lambda stick: (True, "")

    panel.update_now()
    assert panel.update_button.isEnabled() is False
    assert panel._watch.isActive() is True

    now[0] = 45.0
    panel._read_status()
    assert "did not start" not in panel.stick_line.text(), "it has not waited long enough yet"

    now[0] = 91.0
    panel._read_status()

    assert panel.stick_line.text() == "The updater did not start. Nothing has been changed."
    assert panel.update_button.isEnabled() is True
    assert panel.look_button.isEnabled() is True
    assert panel._watch.isActive() is False


def test_a_status_file_that_appears_late_cancels_the_deadline(qtbot, tmp_path: Path) -> None:
    """The deadline is about an updater that never started, not about a slow
    one. A stick that is being checksummed over USB 2 can take a minute before
    anything else is written, and giving up on that would tell the operator
    nothing was changed while the update was running."""
    root = a_console(tmp_path / "VMD", 7)
    stick = a_stick(tmp_path / "E", 8)
    panel = build(qtbot, root, [stick])
    now = [0.0]
    panel.clock = lambda: now[0]
    panel.start_update = lambda stick: (True, "")
    panel.update_now()

    now[0] = 60.0
    a_status(root, {"step": "checking the stick", "finished": False})
    panel._read_status()
    assert panel.stick_line.text() == "checking the stick"

    now[0] = 600.0
    (root / "bin" / "logs" / "update-status.json").unlink()
    panel._read_status()
    assert "did not start" not in panel.stick_line.text()
    assert panel._watch.isActive() is True


def test_the_second_console_does_not_start_a_second_update(qtbot, tmp_path: Path) -> None:
    """Two consoles run on one machine, one per camera, out of one install.
    Both draw this panel, and both would be applying the same stick over the
    same files - the second copying in while the first is halfway through its
    backup. One at a time, and the second one is told why."""
    root = a_console(tmp_path / "VMD", 7)
    stick = a_stick(tmp_path / "E", 8)
    a_status(root, {"step": "copying the new version in", "finished": False})
    panel = build(qtbot, root, [stick])
    tried = []
    panel.start_update = lambda stick: (tried.append(stick), (True, ""))[1]

    panel.update_now()

    assert tried == []
    assert panel.update_button.isEnabled() is False
    assert "already" in panel.stick_line.text()


def test_the_second_console_cannot_go_back_over_a_running_update(
    qtbot, tmp_path, monkeypatch
) -> None:
    """The worse half of the same collision: putting the old files back over an
    update that is halfway through writing the new ones leaves an install that
    is neither version and that nothing on this machine can repair."""
    root = a_console(tmp_path / "VMD", 8)
    (root / "previous" / "7").mkdir(parents=True)
    a_status(root, {"step": "copying the new version in", "finished": False})
    panel = build(qtbot, root, [])
    started = []
    panel.start_rollback = lambda version: started.append(version)
    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.Yes,
    )

    panel.go_back()

    assert started == [] and asked == [], "it must not even ask"
    assert panel.back_button.isEnabled() is False


def test_a_running_update_is_not_read_as_an_interrupted_one(qtbot, tmp_path: Path) -> None:
    """The marker is up for the whole of the dangerous half of an update, so
    the other console sees a marker and a status file being written at the same
    time. Read as "interrupted" it would put a live Go back button in front of
    an operator while the files are being replaced under it."""
    root = a_console(tmp_path / "VMD", 7)
    (root / "previous" / "6").mkdir(parents=True)
    a_marker(root, to=8)
    a_status(root, {"step": "copying the new version in", "finished": False})

    panel = build(qtbot, root, [])

    assert "already running" in panel.stick_line.text()
    assert panel.back_button.isEnabled() is False
    assert panel.update_button.isEnabled() is False


def test_an_update_that_finished_is_not_mistaken_for_one_still_running(
    qtbot, tmp_path: Path
) -> None:
    root = a_console(tmp_path / "VMD", 8)
    stick = a_stick(tmp_path / "E", 9)
    a_status(root, {"step": "", "finished": True, "ok": True, "message": "Updated to VMD 8."})

    panel = build(qtbot, root, [stick])

    assert panel.update_button.isEnabled() is True


def test_a_status_file_nothing_has_written_to_for_hours_does_not_lock_the_button(
    qtbot, tmp_path: Path
) -> None:
    """An updater killed by a power cut before it reached the marker leaves an
    unfinished status file and no process. Read as "an update is running", that
    file refuses every future update on this machine for ever, and the only
    cure is a file nobody on that site knows exists. Every subprocess the
    updater runs is bounded by TIMEOUT_SECONDS, so a file untouched for longer
    than that was written by something that is no longer alive."""
    root = a_console(tmp_path / "VMD", 7)
    stick = a_stick(tmp_path / "E", 8)
    a_status(root, {"step": "checking the stick", "finished": False}, age_seconds=6 * 3600)

    panel = build(qtbot, root, [stick])

    assert panel.update_button.isEnabled() is True


def test_the_answer_the_updater_writes_is_what_the_panel_ends_up_saying(
    qtbot, tmp_path: Path
) -> None:
    root = a_console(tmp_path / "VMD", 7)
    stick = a_stick(tmp_path / "E", 8)
    panel = build(qtbot, root, [stick])
    panel.start_update = lambda stick: (True, "")
    panel.update_now()

    a_status(
        root,
        {
            "step": "",
            "finished": True,
            "ok": False,
            "message": "The stick is damaged: 2 file(s) do not match. Nothing was changed.",
        },
    )
    panel._read_status()

    assert "The stick is damaged" in panel.stick_line.text()
    assert panel._watch.isActive() is False
    assert panel.look_button.isEnabled() is True


def test_an_updater_that_refuses_to_start_says_so_instead_of_waiting(
    qtbot, tmp_path: Path
) -> None:
    stick = a_stick(tmp_path / "E", 8)
    panel = build(qtbot, a_console(tmp_path / "VMD", 7), [stick])
    panel.start_update = lambda stick: (False, "The updater could not be started: no room on disk.")

    panel.update_now()

    assert "no room on disk" in panel.stick_line.text()
    assert panel._watch.isActive() is False
    assert panel.update_button.isEnabled() is True


def test_an_updater_that_wrote_one_line_and_died_is_not_watched_for_ever(
    qtbot, tmp_path: Path
) -> None:
    """The deadline only covers an updater that never wrote a word. One that
    wrote a step and was then killed left the panel watching a file nothing
    would ever touch again, with all three buttons dead - which is the same
    dead end by a different road. The rule is the one `already_running` uses:
    every step the updater takes is bounded by TIMEOUT_SECONDS, so a status
    file older than that was written by something no longer alive."""
    root = a_console(tmp_path / "VMD", 7)
    stick = a_stick(tmp_path / "E", 8)
    panel = build(qtbot, root, [stick])
    panel.start_update = lambda stick: (True, "")
    panel.update_now()

    a_status(root, {"step": "copying the new version in", "finished": False}, age_seconds=6 * 3600)
    panel._read_status()

    assert panel._watch.isActive() is False
    assert "stopped without finishing" in panel.stick_line.text()
    assert panel.look_button.isEnabled() is True
    assert panel.back_button.isEnabled() is True
    assert panel.update_button.isEnabled() is True


def test_a_status_file_being_written_to_is_watched_however_long_it_takes(
    qtbot, tmp_path: Path
) -> None:
    """A uv sync is minutes, and the updater says nothing while it runs."""
    root = a_console(tmp_path / "VMD", 7)
    stick = a_stick(tmp_path / "E", 8)
    panel = build(qtbot, root, [stick])
    panel.start_update = lambda stick: (True, "")
    panel.update_now()

    a_status(root, {"step": "installing any new libraries", "finished": False}, age_seconds=600)
    panel._read_status()

    assert panel._watch.isActive() is True
    assert panel.stick_line.text() == "installing any new libraries"


def test_the_form_reloading_does_not_undo_a_running_update(qtbot, tmp_path: Path) -> None:
    """The Settings tab redraws this box whenever it fills its own form, and
    that has nothing to do with what the updater is doing. A redraw in the
    middle of an update would paint over the one line saying so and hand back
    the button that starts a second one."""
    stick = a_stick(tmp_path / "E", 8)
    panel = build(qtbot, a_console(tmp_path / "VMD", 7), [stick])
    panel.start_update = lambda stick: (True, "")
    panel.update_now()

    panel.look()

    assert panel.stick_line.text() == "Updating. The console will close and start again."
    assert panel.update_button.isEnabled() is False
    assert panel._watch.isActive() is True


def test_the_watcher_belongs_to_the_panel_so_it_stops_when_the_tab_is_shut(
    qtbot, tmp_path: Path
) -> None:
    """A QTimer with no parent outlives the widget its timeout is bound to, and
    a timeout delivered to a deleted widget is not an exception - it is the
    console vanishing. Parented, Qt stops and deletes it with the panel."""
    panel = build(qtbot, a_console(tmp_path / "VMD", 7), [])

    assert panel._watch.parent() is panel
