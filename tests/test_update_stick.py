"""What the console makes of the drives it can see."""

from __future__ import annotations

import json
from pathlib import Path

from vmd.update.stick import look


def a_stick(folder: Path, version: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "update.json").write_text(
        json.dumps({"version": version, "built": "2026-08-22T09:00:00"}),
        encoding="utf-8",
    )
    (folder / "manifest.json").write_text(json.dumps({"files": []}), encoding="utf-8")
    (folder / "files").mkdir(exist_ok=True)
    return folder


def a_console(folder: Path, version: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "VERSION").write_text(str(version), encoding="utf-8")
    return folder


def test_no_drives_at_all_is_not_a_fault(tmp_path: Path) -> None:
    state = look(a_console(tmp_path / "VMD", 7), drives=[])
    assert state.kind == "none"
    assert "No update stick" in state.message


def test_a_newer_stick_is_ready_to_apply(tmp_path: Path) -> None:
    stick = a_stick(tmp_path / "E", 8)
    state = look(a_console(tmp_path / "VMD", 7), drives=[stick])
    assert state.kind == "ready"
    assert state.version == 8
    assert state.stick == stick
    assert "8" in state.message


def test_the_same_version_is_said_plainly_rather_than_offered(tmp_path: Path) -> None:
    stick = a_stick(tmp_path / "E", 7)
    state = look(a_console(tmp_path / "VMD", 7), drives=[stick])
    assert state.kind == "same"


def test_an_older_stick_is_refused_here_because_going_back_is_another_button(
    tmp_path: Path,
) -> None:
    stick = a_stick(tmp_path / "E", 6)
    state = look(a_console(tmp_path / "VMD", 7), drives=[stick])
    assert state.kind == "older"


def test_two_sticks_are_refused_and_both_are_named(tmp_path: Path) -> None:
    """Two answers to "what am I about to install" is no answer. Naming both is
    what lets somebody unplug the right one."""
    first = a_stick(tmp_path / "E", 8)
    second = a_stick(tmp_path / "F", 9)
    state = look(a_console(tmp_path / "VMD", 7), drives=[first, second])
    assert state.kind == "many"
    assert str(first) in state.message and str(second) in state.message


def test_a_drive_with_something_else_on_it_is_not_a_stick(tmp_path: Path) -> None:
    other = tmp_path / "E"
    other.mkdir()
    (other / "holiday.jpg").write_bytes(b"")
    state = look(a_console(tmp_path / "VMD", 7), drives=[other])
    assert state.kind == "none"


def test_a_stick_with_no_version_in_its_update_file_is_damaged(tmp_path: Path) -> None:
    stick = tmp_path / "E"
    stick.mkdir()
    (stick / "update.json").write_text("{}", encoding="utf-8")
    (stick / "manifest.json").write_text("{}", encoding="utf-8")
    state = look(a_console(tmp_path / "VMD", 7), drives=[stick])
    assert state.kind == "damaged"


def test_a_console_with_no_version_of_its_own_can_still_be_updated(
    tmp_path: Path,
) -> None:
    """A copy from before any of this existed. It cannot be compared, so the
    stick is offered rather than withheld - and the message says as much."""
    stick = a_stick(tmp_path / "E", 8)
    console = tmp_path / "VMD"
    console.mkdir()
    state = look(console, drives=[stick])
    assert state.kind == "ready"
    assert "unknown" in state.message.lower()
