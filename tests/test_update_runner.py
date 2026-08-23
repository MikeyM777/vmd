"""The three things the applier does to the machine rather than to files."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from vmd.update import runner
from vmd.update.runner import (
    selftest_command,
    start,
    start_rollback,
    stop_command,
    sync_command,
    temp_copy_of,
    temp_folder,
)


def an_install(root: Path) -> Path:
    """A copy with a `vmd` package and an interpreter of its own."""
    (root / "vmd").mkdir(parents=True)
    interpreter = root / "bin" / "python" / "cpython-3.12.9" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"not really an interpreter")
    return interpreter


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


def test_a_folder_with_an_apostrophe_in_it_is_still_quoted_as_one_word(
    tmp_path: Path,
) -> None:
    """PowerShell ends a single-quoted string at the next apostrophe, and
    C:\\Users\\O'Brien\\VMD is an ordinary Windows path. Unescaped, the console
    is not stopped and what follows the apostrophe is read as commands - so the
    update goes ahead with the recorder still holding the files it is about to
    replace. An apostrophe inside such a string is written twice."""
    root = tmp_path / "O'Brien" / "VMD"

    command = stop_command(root, spare=4242)

    assert str(root) not in command[-1], "the raw path would end the quoting early"
    assert str(root).replace("'", "''") in command[-1]


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


def test_going_back_is_started_the_same_orphaned_way_an_update_is(
    tmp_path: Path, monkeypatch
) -> None:
    """The rollback stops the console too, and the console's stopper kills a
    process TREE. A rollback started as a plain detached child of the console
    is killed by its own second step, halfway through putting the old version
    back - which is the worst state this machine can be left in. It goes
    through `cmd /c start` for the same reason an update does."""
    root = tmp_path / "VMD"
    interpreter = an_install(root)
    seen = {}

    def remember(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs

    monkeypatch.setattr(runner, "temp_folder", lambda: tmp_path / "temp")
    monkeypatch.setattr(subprocess, "Popen", remember)

    started, why = start_rollback(root, 7, root / "settings.json")

    assert (started, why) == (True, "")
    assert seen["command"][0].lower().endswith("cmd.exe")
    assert seen["command"][1:5] == ["/c", "start", "", "/B"]
    assert str(interpreter) in seen["command"]
    assert seen["command"][seen["command"].index("--rollback") + 1] == "7"
    assert seen["kwargs"]["cwd"] == str(tmp_path / "temp")
    assert seen["kwargs"]["env"]["PYTHONPATH"] == str(tmp_path / "temp")


def test_going_back_clears_the_last_update_s_answer_too(
    tmp_path: Path, monkeypatch
) -> None:
    """The panel watches the same file for a rollback as for an update, and the
    last update's `finished` would answer the instant Go back was pressed."""
    root = tmp_path / "VMD"
    an_install(root)
    status = root / "bin" / "logs" / "update-status.json"
    status.parent.mkdir(parents=True)
    status.write_text('{"finished": true}', encoding="utf-8")
    monkeypatch.setattr(runner, "temp_folder", lambda: tmp_path / "temp")
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: None)

    start_rollback(root, 7, root / "settings.json")

    assert not status.exists()


def test_a_rollback_that_cannot_be_started_is_reported_rather_than_raised(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "VMD"
    an_install(root)

    def refuse(*args, **kwargs):
        raise OSError("[WinError 5] Access is denied")

    monkeypatch.setattr(runner, "temp_folder", lambda: tmp_path / "temp")
    monkeypatch.setattr(subprocess, "Popen", refuse)

    started, why = start_rollback(root, 7, root / "settings.json")

    assert started is False
    assert "Access is denied" in why


def test_a_rollback_with_no_interpreter_of_its_own_says_so(tmp_path: Path) -> None:
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)

    started, why = start_rollback(root, 7, root / "settings.json")

    assert started is False
    assert "bin\\python" in why


# --------------------------------------------------------- what it then runs


def kept_copy(root: Path, version: int) -> Path:
    """A `previous\\<version>` of the shape back_up leaves behind."""
    kept = root / "previous" / str(version)
    (kept / "vmd").mkdir(parents=True)
    (kept / "vmd" / "app.py").write_text("# the old one\n", encoding="utf-8")
    (kept / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    return kept


class Ran:
    """Every subprocess the rollback would have run, and none of them run."""

    def __init__(self, code: int = 0, stop_raises: BaseException | None = None) -> None:
        self.commands: list[list[str]] = []
        self.started: list[list[str]] = []
        self.code = code
        self.stop_raises = stop_raises

    def run(self, command, **kwargs):
        self.commands.append(list(command))
        if self.stop_raises is not None and "Stop-ProjectProcesses" in " ".join(command):
            raise self.stop_raises
        return subprocess.CompletedProcess(command, self.code, "", "the cache is empty")

    def popen(self, command, **kwargs):
        self.started.append(list(command))
        return None


def rollback(root: Path, version: int, monkeypatch, ran: Ran) -> int:
    from vmd.update import main as main_module

    monkeypatch.setattr(subprocess, "run", ran.run)
    monkeypatch.setattr(subprocess, "Popen", ran.popen)
    return main_module.main(
        [
            "--root",
            str(root),
            "--rollback",
            str(version),
            "--settings",
            str(root / "settings.json"),
        ]
    )


def read_status(root: Path) -> dict:
    return json.loads(
        (root / "bin" / "logs" / "update-status.json").read_text(encoding="utf-8")
    )


def marker(root: Path) -> Path:
    return root / "bin" / "logs" / "update-in-progress.json"


def test_going_back_puts_the_kept_version_over_the_install(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    (root / "vmd" / "app.py").write_text("# the new one\n", encoding="utf-8")
    (root / "VERSION").write_text("8\n", encoding="utf-8")
    kept_copy(root, 7)
    ran = Ran()

    code = rollback(root, 7, monkeypatch, ran)

    assert code == 0
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "# the old one\n"
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "7"
    status = read_status(root)
    assert status["finished"] is True and status["ok"] is True
    assert "VMD 7 is back" in status["message"]


def test_going_back_stops_the_console_and_starts_it_again(
    tmp_path: Path, monkeypatch
) -> None:
    """The rollback replaces the files the console is running out of, so the
    console has to be down while it happens - and something has to put it back
    up, because the operator is looking at a screen with nothing on it."""
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    kept_copy(root, 7)
    ran = Ran()

    rollback(root, 7, monkeypatch, ran)

    assert any("Stop-ProjectProcesses" in " ".join(command) for command in ran.commands)
    assert any(str(root / "VMD.bat") in command[0] for command in ran.started)


def test_going_back_reinstalls_the_old_libraries_from_this_machine_s_cache(
    tmp_path: Path, monkeypatch
) -> None:
    """There is no stick for the old version's wheels - the stick that is in
    the machine carries the new ones. uv's own cache on this machine is where
    the old ones are: they were installed here once."""
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    kept_copy(root, 7)
    ran = Ran()

    rollback(root, 7, monkeypatch, ran)

    synced = [command for command in ran.commands if "sync" in command]
    assert synced, ran.commands
    assert synced[0][0].endswith("uv.exe")
    assert "--offline" in synced[0] and "--frozen" in synced[0]


def test_a_rollback_whose_libraries_are_gone_says_what_is_wrong_with_the_machine(
    tmp_path: Path, monkeypatch
) -> None:
    """A cleared cache. The files are back and the console will start, but it
    is a console that may not run - and the answer is another trip with a stick
    carrying that version, which is a sentence somebody has to be able to read
    down a telephone."""
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    kept_copy(root, 7)
    ran = Ran(code=1)

    code = rollback(root, 7, monkeypatch, ran)

    status = read_status(root)
    assert code == 1
    assert status["ok"] is False
    assert "the cache is empty" in status["message"]
    assert "Bring a stick with VMD 7 on it" in status["message"]
    assert ran.started, "the console is started again whatever happened"


def test_going_back_raises_the_same_marker_an_update_does(
    tmp_path: Path, monkeypatch
) -> None:
    """A rollback rewrites the same tree an update does, and until now nothing
    recorded that it was doing it. Killed in the middle, it left a part-7,
    part-8 install that the panel read as a machine with nothing wrong."""
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    (root / "VERSION").write_text("8\n", encoding="utf-8")
    kept_copy(root, 7)
    ran = Ran()

    rollback(root, 7, monkeypatch, ran)

    assert not marker(root).exists(), "it comes down when the tree is whole again"


def test_a_rollback_that_cannot_finish_leaves_the_marker_up(
    tmp_path: Path, monkeypatch
) -> None:
    """The one file that says this install is neither version. It stays up so
    that the panel says so at the next start, and the marker names the copy
    that was going back so the operator is offered the same button again."""
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    (root / "VERSION").write_text("8\n", encoding="utf-8")
    kept_copy(root, 7)
    ran = Ran()

    def refuse(kept, where):
        raise OSError("[WinError 32] the file is being used by another process")

    from vmd.update import main as main_module

    monkeypatch.setattr(main_module, "restore", refuse)
    code = rollback(root, 7, monkeypatch, ran)

    assert code == 1
    assert json.loads(marker(root).read_text(encoding="utf-8"))["kept"] == 7
    status = read_status(root)
    assert status["finished"] is True and status["ok"] is False
    assert "part VMD 8 and part VMD 7" in status["message"]


def test_a_sync_that_wedges_after_the_files_are_back_does_not_say_nothing_changed(
    tmp_path: Path, monkeypatch
) -> None:
    """"Nothing was changed" has to be true when it is said.

    go_back kept one flag for two opposite facts - the tree was never opened,
    and the tree was opened and closed again - and the failure message read it
    the first way for both. So a sync that RAISED after the restore had already
    finished (uv wedging until it times out is how that happens) told the
    operator "stopped before anything was replaced. Nothing was changed", on a
    machine that had in fact just been rolled back to the older version with
    the newer version's libraries still in .venv. Believing it, he had no
    reason to look any further.

    The non-raising sync failure right beside it has always been reported
    correctly, so this asserts the two now agree.
    """
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    (root / "VERSION").write_text("8\n", encoding="utf-8")
    kept_copy(root, 7)
    restored: list[bool] = []

    def note_it_ran(kept, where):
        restored.append(True)

    def wedge(command, **kwargs):
        if "Stop-ProjectProcesses" in " ".join(command):
            return subprocess.CompletedProcess(command, 0, "", "")
        raise subprocess.TimeoutExpired("uv sync", 1800)

    from vmd.update import main as main_module

    monkeypatch.setattr(main_module, "restore", note_it_ran)
    ran = Ran()
    monkeypatch.setattr(subprocess, "Popen", ran.popen)
    monkeypatch.setattr(subprocess, "run", wedge)
    code = main_module.main(
        ["--root", str(root), "--rollback", "7", "--settings", str(root / "settings.json")]
    )

    assert restored, "the files really were put back"
    status = read_status(root)
    assert code == 1
    assert status["finished"] is True and status["ok"] is False
    assert "Nothing was changed" not in status["message"], status["message"]
    assert "files are back" in status["message"], status["message"]


def test_a_stopper_that_never_returns_still_writes_an_answer(
    tmp_path: Path, monkeypatch
) -> None:
    """`subprocess.run(timeout=300)` raises. Outside the guard it ended the
    process with the status file still saying finished: false, and the console
    watching it had nothing to read and nothing to press."""
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    kept_copy(root, 7)
    ran = Ran(stop_raises=subprocess.TimeoutExpired("powershell", 300))

    code = rollback(root, 7, monkeypatch, ran)

    status = read_status(root)
    assert code == 1
    assert status["finished"] is True and status["ok"] is False
    assert "TimeoutExpired" in status["message"]
    assert ran.started, "and the console is put back up to say it"


def test_a_machine_whose_version_cannot_be_read_is_not_told_about_VMD_None(
    tmp_path: Path, monkeypatch
) -> None:
    """The machine most likely to be going back is the one whose VERSION file
    was half written. "This copy is now part VMD None" is the sentence it was
    going to be given."""
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    kept_copy(root, 7)
    ran = Ran()

    def refuse(kept, where):
        raise OSError("[WinError 32] the file is being used by another process")

    from vmd.update import main as main_module

    monkeypatch.setattr(main_module, "restore", refuse)
    rollback(root, 7, monkeypatch, ran)

    message = read_status(root)["message"]
    assert "None" not in message
    assert "VMD (version unknown)" in message


def test_going_back_to_a_version_that_is_not_kept_changes_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """Nothing is stopped and nothing is replaced: the console that asked is
    still running, and it is the thing that reads this answer."""
    root = tmp_path / "VMD"
    (root / "vmd").mkdir(parents=True)
    (root / "vmd" / "app.py").write_text("# the new one\n", encoding="utf-8")
    ran = Ran()

    code = rollback(root, 9, monkeypatch, ran)

    assert code == 1
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "# the new one\n"
    assert ran.commands == [] and ran.started == []
    assert "no kept copy of VMD 9" in read_status(root)["message"]
