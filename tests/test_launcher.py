"""The launcher starts the desktop console, not the web server."""

from __future__ import annotations

from pathlib import Path

from vmd import launcher


def test_it_runs_the_desktop_module(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "vmd" / "desktop").mkdir(parents=True)
    commands: list[list[str]] = []

    class Result:
        returncode = 0

    monkeypatch.setattr(launcher, "project_root", lambda: tmp_path)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "uv")
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, cwd, check: (commands.append(command), Result())[1],
    )

    assert launcher.main([]) == 0
    assert commands[0][-2:] == ["-m", "vmd.desktop"]


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
    (tmp_path / "vmd" / "desktop").mkdir(parents=True)
    commands: list[list[str]] = []

    class Result:
        returncode = 0

    monkeypatch.setattr(launcher, "project_root", lambda: tmp_path)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "uv")
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, cwd, check: (commands.append(command), Result())[1],
    )

    launcher.main([])
    command = commands[0]
    for flag in ("--offline", "--frozen", "--no-sync"):
        assert flag in command, f"{flag} missing from {command}"
    # Before `python`, or uv reads them as arguments to the program instead.
    assert command.index("--no-sync") < command.index("python")


def test_the_double_click_batch_file_starts_offline_too() -> None:
    """VMD.bat is what the operator actually double-clicks, and it must match."""
    text = Path(__file__).resolve().parents[1].joinpath("VMD.bat").read_text(encoding="utf-8")
    launch = [line for line in text.splitlines() if "vmd.desktop" in line and not line.startswith("REM")]
    assert launch, "VMD.bat no longer starts the console"
    for flag in ("--offline", "--frozen", "--no-sync"):
        assert flag in launch[0], f"{flag} missing from: {launch[0]}"
