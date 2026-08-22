"""The laptop side, driven the way the GUI drives it.

PowerShell rather than Python because the laptop has nothing installed on it -
no git, no Python, nothing to keep up to date. It is still tested here: the
script takes a folder instead of a download, so a test can hand it a fake
repository and read what lands on the fake stick.

Every test passes -SourceFolder and -NoWheels, and that is not only for speed:
`tests/conftest.py` refuses any socket to an address that is not loopback or a
documented test network, so a test that let this script reach GitHub would be a
test that fails on the machine it was written on and hangs on anybody else's.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "update_stick.ps1"


def build_stick(source: Path, stick: Path, extra: list[str] | None = None):
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-SourceFolder",
            str(source),
            "-To",
            str(stick),
            "-NoWheels",
            *(extra or []),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def a_repository(folder: Path, version: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "vmd").mkdir()
    (folder / "vmd" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (folder / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (folder / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (folder / "settings.json").write_text('{"secret": true}', encoding="utf-8")
    (folder / ".git").mkdir()
    (folder / ".git" / "config").write_text("", encoding="utf-8")
    return folder


def test_the_stick_gets_the_code_a_manifest_and_a_description(tmp_path: Path) -> None:
    source = a_repository(tmp_path / "repo", 8)
    stick = tmp_path / "E"

    result = build_stick(source, stick)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (stick / "files" / "vmd" / "app.py").is_file()
    assert (stick / "files" / "VERSION").read_text(encoding="utf-8").strip() == "8"
    assert json.loads((stick / "update.json").read_text(encoding="utf-8"))["version"] == 8
    assert json.loads((stick / "manifest.json").read_text(encoding="utf-8"))["files"]
    assert "VMD 8" in (stick / "README.txt").read_text(encoding="utf-8")


def test_nothing_of_the_developer_s_own_reaches_the_stick(tmp_path: Path) -> None:
    """A settings file with a camera password in it, and a .git with the whole
    history. Neither belongs on a stick that goes to a customer's site."""
    source = a_repository(tmp_path / "repo", 8)
    stick = tmp_path / "E"

    build_stick(source, stick)

    assert not (stick / "files" / "settings.json").exists()
    assert not (stick / "files" / ".git").exists()


def test_the_manifest_it_writes_matches_what_it_wrote(tmp_path: Path) -> None:
    """The stick verifies its own work before it is carried anywhere. A stick
    that fails its own check is one nobody has to drive to a site to discover."""
    from vmd.update.manifest import verify

    source = a_repository(tmp_path / "repo", 8)
    stick = tmp_path / "E"
    build_stick(source, stick)

    listed = json.loads((stick / "manifest.json").read_text(encoding="utf-8"))
    assert verify(stick / "files", listed) == []


def test_a_stick_carrying_one_file_still_lists_it_as_a_list(tmp_path: Path) -> None:
    """PowerShell's ConvertTo-Json turns an array of one into the thing itself,
    and a manifest whose "files" was an object rather than a list is one the
    machine at the far end reads as "no files listed" and refuses. It cannot
    happen with a real repository, which is exactly why it would be found on a
    customer's site rather than here.

    It also exercises the script's own check of its own work: that check reads
    manifest.json back off the stick, so a manifest of the wrong shape fails the
    build rather than the delivery.
    """
    from vmd.update.manifest import verify

    source = tmp_path / "repo"
    source.mkdir()
    (source / "VERSION").write_text("8\n", encoding="utf-8")
    stick = tmp_path / "E"

    result = build_stick(source, stick)

    assert result.returncode == 0, result.stdout + result.stderr
    listed = json.loads((stick / "manifest.json").read_text(encoding="utf-8"))
    assert [entry["path"] for entry in listed["files"]] == ["VERSION"]
    assert verify(stick / "files", listed) == []


def test_building_again_replaces_the_old_contents(tmp_path: Path) -> None:
    """Version 9 over version 8. A file that version 9 deleted must not be left
    on the stick to be copied onto the machine."""
    stick = tmp_path / "E"
    build_stick(a_repository(tmp_path / "eight", 8), stick)
    (stick / "files" / "vmd" / "gone.py").write_text("", encoding="utf-8")

    build_stick(a_repository(tmp_path / "nine", 9), stick)

    assert not (stick / "files" / "vmd" / "gone.py").exists()
    assert json.loads((stick / "update.json").read_text(encoding="utf-8"))["version"] == 9


def test_a_machine_note_on_the_stick_is_left_alone(tmp_path: Path) -> None:
    """It is the only thing on the stick the offline machine writes, and it is
    what the next build reads to decide which wheels to fetch."""
    source = a_repository(tmp_path / "repo", 8)
    stick = tmp_path / "E"
    (stick / "machines").mkdir(parents=True)
    (stick / "machines" / "WIN-TEST.json").write_text(
        json.dumps({"machine": "WIN-TEST", "version": 7, "libraries": {"numpy": "2.1.0"}}),
        encoding="utf-8",
    )

    build_stick(source, stick)

    assert (stick / "machines" / "WIN-TEST.json").is_file()


def test_it_says_when_it_has_never_seen_a_machine(tmp_path: Path) -> None:
    """With no note it cannot know what to pack, and a stick that quietly
    carries no libraries is one that fails at the far end of a car journey."""
    source = a_repository(tmp_path / "repo", 8)
    stick = tmp_path / "E"

    result = build_stick(source, stick)

    assert "never been to a VMD machine" in result.stdout


def test_it_asks_for_exactly_the_libraries_the_machine_lacks(tmp_path: Path) -> None:
    """The whole reason the machine writes a note. Packing the lock's full set
    every time means torch, and torch is over 2 GB - a stick, a wait and a
    trip, for a change of three lines."""
    source = a_repository(tmp_path / "repo", 8)
    (source / "uv.lock").write_text(
        '[[package]]\nname = "numpy"\nversion = "2.2.0"\n\n'
        '[[package]]\nname = "torch"\nversion = "2.6.0"\n',
        encoding="utf-8",
    )
    stick = tmp_path / "E"
    (stick / "machines").mkdir(parents=True)
    (stick / "machines" / "WIN-TEST.json").write_text(
        json.dumps(
            {
                "machine": "WIN-TEST",
                "version": 7,
                "libraries": {"numpy": "2.1.0", "torch": "2.6.0"},
            }
        ),
        encoding="utf-8",
    )

    result = build_stick(source, stick, extra=["-ListWheelsOnly"])

    assert "numpy==2.2.0" in result.stdout
    assert "torch" not in result.stdout, "torch is already on that machine"


def test_the_two_sides_normalise_a_name_the_same_way(tmp_path: Path) -> None:
    """The machine writes pyside6-essentials (PEP 503) and uv.lock spells the
    same library PySide6_Essentials. If the laptop's normalisation disagreed
    with vmd/update/note.py's, it would list that 90 MB wheel as missing and
    pack it for a machine that already has it. Same normalisation, same version,
    nothing to download."""
    source = a_repository(tmp_path / "repo", 8)
    (source / "uv.lock").write_text(
        '[[package]]\nname = "PySide6_Essentials"\nversion = "6.8.0"\n',
        encoding="utf-8",
    )
    stick = tmp_path / "E"
    (stick / "machines").mkdir(parents=True)
    (stick / "machines" / "WIN-TEST.json").write_text(
        json.dumps(
            {
                "machine": "WIN-TEST",
                "version": 7,
                "libraries": {"pyside6-essentials": "6.8.0"},
            }
        ),
        encoding="utf-8",
    )

    result = build_stick(source, stick, extra=["-ListWheelsOnly"])

    assert "already on the machine" in result.stdout
    assert "needs pyside6-essentials" not in result.stdout


def test_a_package_pinned_per_python_version_uses_the_target_s_pin(
    tmp_path: Path,
) -> None:
    """uv lists a package once per resolution-marker set, so numpy is in the
    real lock twice: 2.4.6 for python < 3.12 and 2.5.1 for python >= 3.12. The
    offline machine runs 3.12, so the stick must diff against 2.5.1. Taking
    whichever the lock happened to write last would pack the wrong wheel, and
    the far end would refuse it. The machine here already has 2.5.1, so once the
    right pin is chosen there is nothing to fetch."""
    source = a_repository(tmp_path / "repo", 8)
    (source / "uv.lock").write_text(
        "[[package]]\nname = \"numpy\"\nversion = \"2.4.6\"\n"
        "resolution-markers = [\n"
        "    \"python_full_version < '3.12' and sys_platform == 'win32'\",\n"
        "    \"python_full_version < '3.12' and sys_platform != 'win32'\",\n"
        "]\n\n"
        "[[package]]\nname = \"numpy\"\nversion = \"2.5.1\"\n"
        "resolution-markers = [\n"
        "    \"python_full_version >= '3.12' and sys_platform == 'win32'\",\n"
        "    \"python_full_version >= '3.12' and sys_platform != 'win32'\",\n"
        "]\n",
        encoding="utf-8",
    )
    stick = tmp_path / "E"
    (stick / "machines").mkdir(parents=True)
    (stick / "machines" / "WIN-TEST.json").write_text(
        json.dumps(
            {"machine": "WIN-TEST", "version": 7, "libraries": {"numpy": "2.5.1"}}
        ),
        encoding="utf-8",
    )

    result = build_stick(source, stick, extra=["-ListWheelsOnly"])

    assert "already on the machine" in result.stdout
    assert "2.4.6" not in result.stdout, "the < 3.12 pin is not for this machine"
    assert "needs numpy" not in result.stdout
