"""The launcher starts the desktop console, not the web server."""

from __future__ import annotations

from pathlib import Path

from vmd import launcher


class Result:
    returncode = 0


def stub_run(monkeypatch, calls: list[dict]):
    """Stand in for subprocess.run, recording exactly how it was called.

    Keyword-tolerant on purpose: the launcher hands the child an environment as
    well as a command, and a stub with a fixed signature turns "an argument was
    added" into a TypeError somewhere unrelated.
    """

    def run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return Result()

    monkeypatch.setattr(launcher.subprocess, "run", run)
    return calls


def launch(calls):
    """The call that started the console.

    Not simply the first one: the launcher asks each uv it finds for its
    version before trusting it, so the recorded calls begin with a probe.
    """
    for call in calls:
        if "vmd.desktop" in call["command"]:
            return call
    raise AssertionError(f"the console was never started: {calls}")


def project(tmp_path: Path, monkeypatch, *, uv_on_path: str | None = "uv") -> Path:
    (tmp_path / "vmd" / "desktop").mkdir(parents=True)
    monkeypatch.setattr(launcher, "project_root", lambda: tmp_path)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: uv_on_path)
    return tmp_path


def bundled_uv(root: Path) -> Path:
    path = root / "bin" / "uv.exe"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_it_runs_the_desktop_module(monkeypatch, tmp_path: Path) -> None:
    project(tmp_path, monkeypatch)
    calls = stub_run(monkeypatch, [])

    assert launcher.main([]) == 0
    assert launch(calls)["command"][-2:] == ["-m", "vmd.desktop"]


def test_a_folder_without_the_app_is_reported(monkeypatch, tmp_path: Path) -> None:
    held: list[str] = []
    monkeypatch.setattr(launcher, "project_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "hold", lambda message: (held.append(message), 1)[1])
    assert launcher.main([]) == 1
    assert "VMD folder" in held[0]


def test_starting_the_console_is_never_a_network_operation(monkeypatch, tmp_path: Path) -> None:
    """This laptop has no network, and the launcher must not need one.

    Plain `uv run` re-checks the lock file and syncs, so any drift - a pulled
    commit, a touched pyproject.toml - sends it to PyPI. Offline that is a hang
    or a refusal at startup, which is the one failure a non-technical operator
    cannot recover from. install.bat and the Update button are where
    dependencies are allowed to change.
    """
    project(tmp_path, monkeypatch)
    calls = stub_run(monkeypatch, [])

    launcher.main([])
    command = launch(calls)["command"]
    for flag in ("--offline", "--frozen", "--no-sync"):
        assert flag in command, f"{flag} missing from {command}"
    # Before `python`, or uv reads them as arguments to the program instead.
    assert command.index("--no-sync") < command.index("python")


# --------------------------------------------------------------- finding uv


def test_uv_beside_the_app_is_used_without_asking_path(monkeypatch, tmp_path: Path) -> None:
    """The whole PATH dependency has to go, not just usually work.

    The installer puts uv.exe in bin\\ and adds bin\\ to the user PATH, but the
    environment broadcast does not always reach Explorer before the operator
    double-clicks - and an exe launched from Explorer inherits Explorer's
    environment. The launcher then said "uv is not installed" on a machine
    where it plainly was, with no terminal to find that out from.
    """
    root = project(tmp_path, monkeypatch, uv_on_path=None)
    bundled = bundled_uv(root)
    calls = stub_run(monkeypatch, [])

    assert launcher.main([]) == 0
    assert launch(calls)["command"][0] == str(bundled)


def test_the_bundled_uv_wins_over_one_on_the_path(monkeypatch, tmp_path: Path) -> None:
    """The bundled copy is the one the installer put there and tested."""
    root = project(tmp_path, monkeypatch, uv_on_path=r"C:\somewhere\else\uv.exe")
    bundled = bundled_uv(root)
    calls = stub_run(monkeypatch, [])

    launcher.main([])
    assert launch(calls)["command"][0] == str(bundled)


def test_uv_on_the_path_still_works(monkeypatch, tmp_path: Path) -> None:
    """A development machine has no bin\\uv.exe, and must keep starting."""
    project(tmp_path, monkeypatch, uv_on_path=r"C:\tools\uv.exe")
    calls = stub_run(monkeypatch, [])

    launcher.main([])
    assert launch(calls)["command"][0] == r"C:\tools\uv.exe"


def test_no_uv_anywhere_is_still_the_install_message(monkeypatch, tmp_path: Path) -> None:
    project(tmp_path, monkeypatch, uv_on_path=None)
    held: list[str] = []
    monkeypatch.setattr(launcher, "hold", lambda message: (held.append(message), 1)[1])

    assert launcher.main([]) == 1
    assert "uv is not installed" in held[0]
    assert "install.bat" in held[0]


# ------------------------------------------------------- the child's PATH


def test_the_console_is_started_with_bin_on_its_path(monkeypatch, tmp_path: Path) -> None:
    """The console starts the recorder, which runs ffmpeg out of bin\\.

    A second belt: find_tool already looks in bin\\, so recording works without
    this. Carrying bin\\ on the child's PATH means go2rtc, ffmpeg and anything
    else resolve the same way whether they go through that lookup or a bare
    PATH one.
    """
    root = project(tmp_path, monkeypatch)
    bundled_uv(root)
    calls = stub_run(monkeypatch, [])

    launcher.main([])
    path = launch(calls)["env"]["PATH"]
    assert path.split(";")[0] == str(root / "bin"), path


def test_a_project_without_a_bin_folder_still_starts(monkeypatch, tmp_path: Path) -> None:
    """Nothing here may make a missing folder into a reason not to open."""
    project(tmp_path, monkeypatch)
    calls = stub_run(monkeypatch, [])

    assert launcher.main([]) == 0
    assert "PATH" in launch(calls)["env"] or "Path" in launch(calls)["env"]


def test_the_double_click_batch_file_starts_offline_too() -> None:
    """VMD.bat is what the operator actually double-clicks, and it must match."""
    text = Path(__file__).resolve().parents[1].joinpath("VMD.bat").read_text(encoding="utf-8")
    launch = [
        line
        for line in text.splitlines()
        if "vmd.desktop" in line and not line.strip().startswith("REM")
    ]
    assert launch, "VMD.bat no longer starts the console"
    for flag in ("--offline", "--frozen", "--no-sync"):
        assert flag in launch[0], f"{flag} missing from: {launch[0]}"


# ------------------------------------------------- a uv that is there and dead


def stub_run_with_broken(monkeypatch, calls: list[dict], broken: str):
    """subprocess.run, where one particular executable will not execute.

    A half-downloaded uv.exe is the ordinary way to arrive here: the installer
    fetches it over the same link this laptop keeps losing, and what is left is
    a file of the right name that Windows refuses with "%1 is not a valid Win32
    application".
    """

    def run(command, **kwargs):
        if command and str(command[0]) == broken:
            raise OSError(8, "%1 is not a valid Win32 application")
        calls.append({"command": command, **kwargs})
        return Result()

    monkeypatch.setattr(launcher.subprocess, "run", run)
    return calls


def test_a_uv_that_will_not_run_is_not_the_uv_that_is_used(
    monkeypatch, tmp_path: Path
) -> None:
    """Finding an executable is not that executable working.

    `is_file()` is true of a half-downloaded uv.exe, and it was the only
    question asked. The launcher then handed it to subprocess, got a Windows
    error number back, and printed it - on a machine with a working uv on PATH
    the whole time.
    """
    root = project(tmp_path, monkeypatch, uv_on_path=r"C:\tools\uv.exe")
    bundled = bundled_uv(root)
    calls = stub_run_with_broken(monkeypatch, [], str(bundled))

    assert launcher.main([]) == 0
    assert launch(calls)["command"][0] == r"C:\tools\uv.exe", calls


def test_a_uv_that_will_not_run_and_no_other_is_said_in_words_that_help(
    monkeypatch, tmp_path: Path
) -> None:
    """"uv is not installed" is false and sends the operator the wrong way.

    There is no terminal on this machine and the operator is not technical.
    The message has to name what is wrong and what to double-click.
    """
    root = project(tmp_path, monkeypatch, uv_on_path=None)
    bundled = bundled_uv(root)
    stub_run_with_broken(monkeypatch, [], str(bundled))
    held: list[str] = []
    monkeypatch.setattr(launcher, "hold", lambda message: (held.append(message), 1)[1])

    assert launcher.main([]) == 1
    assert "will not run" in held[0], held[0]
    assert "install.bat" in held[0]


def test_a_uv_that_answers_with_a_failure_is_not_used_either(
    monkeypatch, tmp_path: Path
) -> None:
    """It ran, and it said it could not work. That is not a working uv."""

    class Failed:
        returncode = 1

    root = project(tmp_path, monkeypatch, uv_on_path=None)
    bundled = bundled_uv(root)

    def run(command, **kwargs):
        if str(command[0]) == str(bundled):
            return Failed()
        return Result()

    monkeypatch.setattr(launcher.subprocess, "run", run)
    held: list[str] = []
    monkeypatch.setattr(launcher, "hold", lambda message: (held.append(message), 1)[1])

    assert launcher.main([]) == 1
    assert "will not run" in held[0], held[0]


def test_the_check_that_uv_works_cannot_hang_the_launcher(
    monkeypatch, tmp_path: Path
) -> None:
    """A wedged probe would be a console that never opens, which is worse than
    the thing being probed for."""
    root = project(tmp_path, monkeypatch, uv_on_path=None)
    bundled = bundled_uv(root)
    seen: list[dict] = []

    def run(command, **kwargs):
        seen.append({"command": command, **kwargs})
        return Result()

    monkeypatch.setattr(launcher.subprocess, "run", run)
    launcher.main([])
    probe = next(call for call in seen if call["command"][:2] == [str(bundled), "--version"])
    assert probe.get("timeout"), "the probe waits for ever on a uv that has wedged"
