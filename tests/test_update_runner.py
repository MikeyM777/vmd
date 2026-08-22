"""The three things the applier does to the machine rather than to files."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from vmd.update import runner
from vmd.update.runner import (
    selftest_command,
    start,
    stop_command,
    sync_command,
    temp_copy_of,
    temp_folder,
)


def test_libraries_are_installed_from_the_stick_and_nowhere_else(tmp_path: Path) -> None:
    """--no-index is the whole of it. Without it uv reaches for PyPI, which on
    this machine means a minute of retries and then a failure that reads like a
    broken update rather than a machine with no internet."""
    command = sync_command(root=tmp_path / "VMD", stick=tmp_path / "E")

    assert command[0].endswith("uv.exe")
    assert "--offline" in command
    assert "--no-index" in command
    assert "--find-links" in command
    assert str((tmp_path / "E" / "wheels")) in command
    assert "--extra" in command and "detect" in command


def test_stopping_the_console_is_left_to_the_script_that_already_does_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "VMD"
    command = stop_command(root, spare=4242)

    assert command[0] == "powershell"
    assert str(root / "scripts" / "_common.ps1") in command[-1]
    assert "Stop-ProjectProcesses" in command[-1]
    assert str(root) in command[-1]


def test_the_updater_tells_the_stopper_to_leave_the_updater_alone(
    tmp_path: Path,
) -> None:
    """The updater is a python running out of bin\\python\\, which is exactly
    what Stop-ProjectProcesses looks for. Not sparing it means the first thing
    an update does is kill itself, with the console already stopped and nothing
    left to start it again."""
    command = stop_command(tmp_path / "VMD", spare=4242)

    assert "4242" in command[-1]


def test_the_selftest_is_run_by_the_project_s_own_interpreter(tmp_path: Path) -> None:
    root = tmp_path / "VMD"
    (root / "bin").mkdir(parents=True)
    command = selftest_command(root=root, settings=root / "settings.json")

    assert command[0].endswith("uv.exe")
    assert command[1:5] == ["run", "--offline", "--frozen", "--no-sync"]
    assert "vmd.selftest" in command


def test_the_updater_runs_from_a_copy_of_itself(tmp_path: Path) -> None:
    """It is about to replace vmd\\, which is where it lives. A program cannot
    be sure of what it will read next while something rewrites it underneath,
    so it is copied out first and run from there."""
    root = tmp_path / "VMD"
    (root / "vmd" / "update").mkdir(parents=True)
    (root / "vmd" / "__init__.py").write_text("", encoding="utf-8")
    (root / "vmd" / "update" / "apply.py").write_text("# applier\n", encoding="utf-8")

    where = temp_copy_of(root, tmp_path / "temp")

    assert (where / "vmd" / "update" / "apply.py").read_text(encoding="utf-8") == "# applier\n"
    assert where != root


def test_a_copy_left_behind_by_a_previous_update_is_replaced_not_merged(
    tmp_path: Path,
) -> None:
    """The copy is never cleaned up: the process that made it is killed by the
    update it started. So every update finds the last one's leftovers, and what
    must not happen is the old files being left among the new - that is an
    updater running half of one version and half of another."""
    root = tmp_path / "VMD"
    (root / "vmd" / "update").mkdir(parents=True)
    (root / "vmd" / "update" / "apply.py").write_text("# new\n", encoding="utf-8")
    leftover = tmp_path / "temp"
    (leftover / "vmd" / "update").mkdir(parents=True)
    (leftover / "vmd" / "update" / "apply.py").write_text("# old\n", encoding="utf-8")
    (leftover / "vmd" / "gone.py").write_text("# deleted since\n", encoding="utf-8")

    where = temp_copy_of(root, leftover)

    assert (where / "vmd" / "update" / "apply.py").read_text(encoding="utf-8") == "# new\n"
    assert not (where / "vmd" / "gone.py").exists()


def test_each_console_copies_itself_somewhere_of_its_own(tmp_path: Path) -> None:
    """One install can run several consoles - one per camera - and they all
    share a TEMP folder. Two of them updating at once into one folder is the
    second one deleting the code the first is running out of, which is the
    exact accident the copy exists to prevent."""
    assert str(os.getpid()) in temp_folder().name


def test_a_copy_with_no_interpreter_of_its_own_says_so(tmp_path: Path) -> None:
    """bin\\python\\ is what runs the updater. A folder copied by hand from
    another machine may not have it, and the operator has to be told that
    rather than watching a panel wait for a process that was never started."""
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)

    started, why = start(root, tmp_path / "E", root / "settings.json")

    assert started is False
    assert "bin\\python" in why


def test_an_updater_that_will_not_start_is_reported_rather_than_raised(
    tmp_path: Path, monkeypatch
) -> None:
    """Popen raises when Windows refuses to start the process at all. Raising
    it into the button that was pressed leaves the panel saying an update is
    under way when nothing is."""
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    interpreter = root / "bin" / "python" / "cpython-3.12.9" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"not really an interpreter")

    def refuse(*args, **kwargs):
        raise OSError("[WinError 5] Access is denied")

    monkeypatch.setattr(runner, "temp_folder", lambda: tmp_path / "temp")
    monkeypatch.setattr(subprocess, "Popen", refuse)

    started, why = start(root, tmp_path / "E", root / "settings.json")

    assert started is False
    assert "Access is denied" in why


def test_the_updater_is_started_out_of_the_copy_and_not_under_the_console(
    tmp_path: Path, monkeypatch
) -> None:
    """`taskkill /T`, which is how the console is stopped, kills everything
    below the process it is given - and a detached process is still below the
    one that started it. Started straight from the console, the updater is
    killed by the very step it runs. Started through `cmd /c start`, the thing
    that started it has already exited, and there is no tree leading down to
    it."""
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    interpreter = root / "bin" / "python" / "cpython-3.12.9" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"not really an interpreter")
    seen = {}

    def remember(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs

    monkeypatch.setattr(runner, "temp_folder", lambda: tmp_path / "temp")
    monkeypatch.setattr(subprocess, "Popen", remember)

    started, _ = start(root, tmp_path / "E", root / "settings.json")

    assert started is True
    assert seen["command"][0].lower().endswith("cmd.exe")
    assert seen["command"][1:5] == ["/c", "start", "", "/B"]
    assert str(interpreter) in seen["command"]
    assert "vmd.update.main" in seen["command"]
    # Out of the copy, with the copy on the path - never out of the tree it is
    # about to rewrite.
    assert seen["kwargs"]["cwd"] == str(tmp_path / "temp")
    assert seen["kwargs"]["env"]["PYTHONPATH"] == str(tmp_path / "temp")


def test_starting_the_updater_clears_the_last_update_s_answer(
    tmp_path: Path, monkeypatch
) -> None:
    """The panel watches the status file for `finished`. Left over from the
    last update, it says finished the instant this one begins, and the console
    reports an update that has not happened yet."""
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    interpreter = root / "bin" / "python" / "cpython-3.12.9" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"not really an interpreter")
    status = root / "bin" / "logs" / "update-status.json"
    status.parent.mkdir(parents=True)
    status.write_text('{"finished": true}', encoding="utf-8")
    monkeypatch.setattr(runner, "temp_folder", lambda: tmp_path / "temp")
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: None)

    start(root, tmp_path / "E", root / "settings.json")

    assert not status.exists()
