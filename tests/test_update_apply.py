"""The half of the updater that moves files about."""

from __future__ import annotations

from pathlib import Path

from vmd.update.apply import KEEP_OUT, back_up, copy_in, restore, what_to_copy


def an_install(root: Path) -> Path:
    """A folder shaped like a real one: program files, and the machine's own."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "vmd").mkdir()
    (root / "vmd" / "app.py").write_text("old\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "install.ps1").write_text("old\n", encoding="utf-8")
    (root / "VMD.bat").write_text("old\n", encoding="utf-8")
    (root / "VERSION").write_text("7", encoding="utf-8")

    (root / "settings.json").write_text('{"mine": true}', encoding="utf-8")
    (root / "cameras").mkdir()
    (root / "cameras" / "250").mkdir()
    (root / "cameras" / "250" / "settings.json").write_text("{}", encoding="utf-8")
    (root / "recordings").mkdir()
    (root / "recordings" / "clip.mp4").write_bytes(b"footage")
    (root / "bin").mkdir()
    (root / "bin" / "uv.exe").write_bytes(b"binary")
    (root / ".venv").mkdir()
    (root / ".venv" / "marker").write_text("env", encoding="utf-8")
    return root


def new_files(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "vmd").mkdir()
    (folder / "vmd" / "app.py").write_text("new\n", encoding="utf-8")
    (folder / "VMD.bat").write_text("new\n", encoding="utf-8")
    (folder / "VERSION").write_text("8", encoding="utf-8")
    return folder


def test_only_the_program_is_copied(tmp_path: Path) -> None:
    files = new_files(tmp_path / "files")
    assert sorted(what_to_copy(files)) == ["VERSION", "VMD.bat", "vmd"]


def test_nothing_belonging_to_the_machine_is_ever_copied_over(tmp_path: Path) -> None:
    """The list that matters. Everything in it is either this site's own - its
    camera, its footage, its passwords - or is bigger than the update and was
    not in it."""
    assert {"settings.json", "cameras", "recordings", "bin", ".venv"} <= set(KEEP_OUT)


def test_copying_replaces_the_program_and_leaves_the_rest_alone(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    files = new_files(tmp_path / "files")

    copy_in(files, root)

    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "new\n"
    assert (root / "VERSION").read_text(encoding="utf-8") == "8"
    assert (root / "settings.json").read_text(encoding="utf-8") == '{"mine": true}'
    assert (root / "cameras" / "250" / "settings.json").is_file()
    assert (root / "recordings" / "clip.mp4").read_bytes() == b"footage"
    assert (root / "bin" / "uv.exe").read_bytes() == b"binary"
    assert (root / ".venv" / "marker").is_file()
    # Untouched by this update because it was not in it.
    assert (root / "scripts" / "install.ps1").read_text(encoding="utf-8") == "old\n"


def test_the_old_program_is_kept_before_anything_is_written(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    files = new_files(tmp_path / "files")

    kept = back_up(root, version=7, names=what_to_copy(files))

    assert kept == root / "previous" / "7"
    assert (kept / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert (kept / "VERSION").read_text(encoding="utf-8") == "7"
    # Not the machine's own things: a backup with a settings file in it is a
    # rollback that can put somebody else's camera password back.
    assert not (kept / "settings.json").exists()


def test_putting_the_old_one_back_undoes_the_copy(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    files = new_files(tmp_path / "files")
    kept = back_up(root, version=7, names=what_to_copy(files))
    copy_in(files, root)

    restore(kept, root)

    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert (root / "VERSION").read_text(encoding="utf-8") == "7"
    assert (root / "settings.json").read_text(encoding="utf-8") == '{"mine": true}'


def test_a_second_backup_of_the_same_version_replaces_the_first(tmp_path: Path) -> None:
    """Two updates in one visit. The second must not fail because the first
    left a folder behind, and must not keep a half-written one."""
    root = an_install(tmp_path / "VMD")
    files = new_files(tmp_path / "files")
    back_up(root, version=7, names=what_to_copy(files))
    (root / "previous" / "7" / "stray.txt").write_text("x", encoding="utf-8")

    kept = back_up(root, version=7, names=what_to_copy(files))

    assert not (kept / "stray.txt").exists()
