"""The one number that says which software this is."""

from __future__ import annotations

from pathlib import Path

from vmd.update.version import describe, read_version


def test_the_version_is_the_number_in_the_version_file(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("7\n", encoding="utf-8")
    assert read_version(tmp_path) == 7
    assert describe(tmp_path) == "VMD 7"


def test_a_folder_with_no_version_file_says_so_rather_than_guessing(tmp_path: Path) -> None:
    """A copy that predates this, or a half-copied folder. Calling it 0 would
    make every stick look newer than it and every comparison meaningless."""
    assert read_version(tmp_path) is None
    assert describe(tmp_path) == "VMD (version unknown)"


def test_rubbish_in_the_file_is_not_a_version(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("eight", encoding="utf-8")
    assert read_version(tmp_path) is None


def test_this_repository_carries_a_version() -> None:
    """The file has to be in the repository itself: it travels inside every
    update, and it is what the offline machine compares against a stick."""
    root = Path(__file__).resolve().parent.parent
    assert read_version(root) is not None, "VERSION is missing from the project root"
