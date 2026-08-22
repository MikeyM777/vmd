"""What the offline machine tells the laptop about itself."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vmd.update import note as note_module
from vmd.update.note import installed_libraries, write_note


def a_venv(root: Path, packages: dict[str, str]) -> None:
    site = root / ".venv" / "Lib" / "site-packages"
    site.mkdir(parents=True)
    for name, version in packages.items():
        info = site / f"{name}-{version}.dist-info"
        info.mkdir()
        # A real dist-info folder always has a METADATA file. installed_libraries
        # deliberately never opens it - the version comes off the folder name -
        # but it is written here anyway so the fixture is what a real .venv
        # actually looks like, not a shape invented to suit the reader.
        (info / "METADATA").write_text(
            f"Name: {name}\nVersion: {version}\n", encoding="utf-8"
        )


def test_the_libraries_are_read_off_the_environment(tmp_path: Path) -> None:
    a_venv(tmp_path, {"numpy": "2.1.0", "PySide6": "6.8.0"})
    assert installed_libraries(tmp_path) == {"numpy": "2.1.0", "pyside6": "6.8.0"}


def test_names_are_lowercased_so_the_two_sides_can_compare_them(tmp_path: Path) -> None:
    """PySide6 on the machine and pyside6 in uv.lock are the same library. A
    comparison that says otherwise packs a 90 MB wheel nobody needs."""
    a_venv(tmp_path, {"PySide6_Essentials": "6.8.0"})
    assert "pyside6-essentials" in installed_libraries(tmp_path)


def test_an_environment_that_is_not_there_yields_nothing_rather_than_raising(
    tmp_path: Path,
) -> None:
    assert installed_libraries(tmp_path) == {}


def test_the_note_names_the_machine_the_version_and_the_libraries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "VMD"
    root.mkdir()
    (root / "VERSION").write_text("7", encoding="utf-8")
    a_venv(root, {"numpy": "2.1.0"})
    stick = tmp_path / "stick"
    stick.mkdir()

    path = write_note(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00")

    assert path == stick / "machines" / "WIN-TEST.json"
    note = json.loads(path.read_text(encoding="utf-8"))
    assert note["machine"] == "WIN-TEST"
    assert note["version"] == 7
    assert note["libraries"] == {"numpy": "2.1.0"}
    assert note["written"] == "2026-08-22T10:00:00"


def test_writing_the_note_twice_replaces_it(tmp_path: Path) -> None:
    """The stick goes back and forth for years. Two notes for one machine is
    two answers to "what does it have", and the laptop would pack for the older
    one."""
    root = tmp_path / "VMD"
    root.mkdir()
    (root / "VERSION").write_text("7", encoding="utf-8")
    stick = tmp_path / "stick"
    stick.mkdir()

    write_note(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00")
    (root / "VERSION").write_text("8", encoding="utf-8")
    path = write_note(root, stick, machine="WIN-TEST", when="2026-08-23T10:00:00")

    assert len(list((stick / "machines").glob("*.json"))) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 8


def test_a_failure_during_the_second_write_leaves_the_first_note_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A power cut or a pulled stick can interrupt a write at any point, and the
    note is the one thing that survives a failed update to tell the laptop what
    this machine has. So a failure partway through writing it must never leave
    a truncated or half-written file behind - the destination must stay either
    the old complete note or the new one, never something in between.

    os.replace is the last step of a crash-safe write: everything up to it
    happens on a temporary file, and only a working replace ever touches the
    real path. Making that final step fail proves the point precisely because
    it comes after real disk I/O has already happened for the second write - if
    the destination were touched any earlier than that, this would already have
    corrupted it.
    """
    root = tmp_path / "VMD"
    root.mkdir()
    (root / "VERSION").write_text("7", encoding="utf-8")
    stick = tmp_path / "stick"
    stick.mkdir()

    path = write_note(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00")
    first = path.read_text(encoding="utf-8")

    def stick_pulled(*args: object, **kwargs: object) -> None:
        raise OSError("stick pulled mid-write")

    monkeypatch.setattr(note_module.os, "replace", stick_pulled)
    (root / "VERSION").write_text("8", encoding="utf-8")

    with pytest.raises(OSError):
        write_note(root, stick, machine="WIN-TEST", when="2026-08-23T10:00:00")

    assert path.read_text(encoding="utf-8") == first
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 7

    # No temporary file left behind beside the note that survived.
    leftovers = [p for p in (stick / "machines").iterdir() if p != path]
    assert leftovers == []
