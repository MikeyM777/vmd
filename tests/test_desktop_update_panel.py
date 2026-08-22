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
    logs = root / "bin" / "logs"
    logs.mkdir(parents=True)
    (logs / "update-in-progress.json").write_text('{"to": 8}', encoding="utf-8")

    panel = build(qtbot, root, [])

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
