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
