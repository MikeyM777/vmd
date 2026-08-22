"""What the offline machine tells the laptop about itself."""

from __future__ import annotations

import json
from pathlib import Path

from vmd.update.note import installed_libraries, write_note


def a_venv(root: Path, packages: dict[str, str]) -> None:
    site = root / ".venv" / "Lib" / "site-packages"
    site.mkdir(parents=True)
    for name, version in packages.items():
        info = site / f"{name}-{version}.dist-info"
        info.mkdir()
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
