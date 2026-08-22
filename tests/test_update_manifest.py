"""Every byte on the stick is checked before anything on the machine is touched."""

from __future__ import annotations

import json
from pathlib import Path

from vmd.update.manifest import build, verify, write


def a_tree(root: Path) -> Path:
    folder = root / "files"
    (folder / "vmd").mkdir(parents=True)
    (folder / "vmd" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (folder / "VERSION").write_text("8\n", encoding="utf-8")
    return folder


def test_a_manifest_lists_every_file_with_its_hash(tmp_path: Path) -> None:
    folder = a_tree(tmp_path)
    manifest = build(folder)
    paths = {entry["path"] for entry in manifest["files"]}
    assert paths == {"vmd/app.py", "VERSION"}
    for entry in manifest["files"]:
        assert len(entry["sha256"]) == 64
        assert entry["size"] > 0


def test_paths_are_written_with_forward_slashes(tmp_path: Path) -> None:
    """The stick is read on Windows and written on Windows, and the manifest is
    still not the place to put backslashes: it is compared as text, and one
    machine writing vmd\\app.py while another looks for vmd/app.py is a stick
    that reports every file as missing."""
    folder = a_tree(tmp_path)
    manifest = build(folder)
    assert all("\\" not in entry["path"] for entry in manifest["files"])


def test_an_untouched_tree_verifies(tmp_path: Path) -> None:
    folder = a_tree(tmp_path)
    assert verify(folder, build(folder)) == []


def test_one_changed_byte_is_reported_and_the_file_is_named(tmp_path: Path) -> None:
    folder = a_tree(tmp_path)
    manifest = build(folder)
    (folder / "vmd" / "app.py").write_text("print('goodbye')\n", encoding="utf-8")

    problems = verify(folder, manifest)
    assert len(problems) == 1
    assert "vmd/app.py" in problems[0]


def test_a_missing_file_is_reported_and_named(tmp_path: Path) -> None:
    folder = a_tree(tmp_path)
    manifest = build(folder)
    (folder / "VERSION").unlink()

    problems = verify(folder, manifest)
    assert len(problems) == 1
    assert "VERSION" in problems[0]


def test_a_file_the_manifest_never_heard_of_is_reported(tmp_path: Path) -> None:
    """A stick with something extra on it is a stick somebody has edited, or one
    written by two builds at once. Neither is applied."""
    folder = a_tree(tmp_path)
    manifest = build(folder)
    (folder / "stray.py").write_text("", encoding="utf-8")

    problems = verify(folder, manifest)
    assert len(problems) == 1
    assert "stray.py" in problems[0]


def test_a_folder_that_is_not_there_is_refused(tmp_path: Path) -> None:
    """A manifest that lists nothing, beside no files\\ folder at all, used to
    agree with itself and pass. The updater would then kill the console before
    what_to_copy discovered there was nothing to copy - an operator told
    "nothing was changed" by a machine that had just been shut down."""
    problems = verify(tmp_path / "not-here", {"files": []})

    assert problems
    assert "files" in problems[0]


def test_an_empty_folder_that_is_there_still_verifies(tmp_path: Path) -> None:
    """The check is that the folder exists, not that it has something in it."""
    folder = tmp_path / "files"
    folder.mkdir()

    assert verify(folder, {"files": []}) == []


def test_a_manifest_entry_that_is_not_a_file_is_a_sentence_not_an_exception(
    tmp_path: Path,
) -> None:
    """Every shape of rubbish that is still valid JSON. verify is called from a
    detached process whose only way of speaking is its status file, so an
    exception raised here is not a message anybody ever sees - it is a console
    left waiting on a program that has already died."""
    folder = a_tree(tmp_path)

    for damaged in (
        {"files": [{"path": "VERSION"}]},  # KeyError: 'size'
        {"files": ["VERSION"]},  # TypeError: string indices must be integers
        {"files": [{"path": 5, "size": 1, "sha256": "x"}]},  # a path that is not one
        {"files": "VERSION"},  # not a list of anything
        {"files": 8},
        {},  # no list at all
        ["VERSION"],  # not even an object
    ):
        problems = verify(folder, damaged)
        assert problems, damaged
        assert all(isinstance(line, str) for line in problems)


def test_write_puts_the_manifest_beside_the_folder(tmp_path: Path) -> None:
    folder = a_tree(tmp_path)
    target = tmp_path / "manifest.json"
    write(folder, target)
    assert json.loads(target.read_text(encoding="utf-8"))["files"]
