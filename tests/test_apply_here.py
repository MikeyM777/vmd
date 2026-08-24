"""The fool-proof applier, scripts\\apply_here.ps1.

PowerShell, and tested the way scripts\\update_stick.ps1 is: it takes seams that
run one pure part and stop, so pytest can drive the copy engine and the
install-finding without a real console to kill or a real stick to pull out.

The one test that matters most is that the fallback copy engine never writes over
the machine's own things - its camera, its footage, its passwords - and that its
whitelist cannot drift away from the one in vmd\\update\\apply.py that the audited
engine uses.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from vmd.update.apply import COPY_IN, KEEP_OUT

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "apply_here.ps1"


def run_ps(args: list[str]):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def verdict(result) -> str:
    """The seam's answer is its last non-empty line - the engine prints progress
    before it, the same way the real run does."""
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _ps_array(name: str) -> set[str]:
    """The quoted names inside a `$script:NAME = @( ... )` list in the applier."""
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"\$script:{name}\s*=\s*@\((.*?)\)", text, re.DOTALL)
    assert match, f"{name} array not found in {SCRIPT.name}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


# --------------------------------------------------------------------------- #
#  The whitelist cannot drift from the audited one
# --------------------------------------------------------------------------- #


def test_the_applier_copy_in_matches_the_audited_one() -> None:
    assert _ps_array("COPY_IN") == set(COPY_IN)


def test_the_applier_keep_out_matches_the_audited_one() -> None:
    assert _ps_array("KEEP_OUT") == set(KEEP_OUT)


# --------------------------------------------------------------------------- #
#  Finding the install
# --------------------------------------------------------------------------- #


def a_vmd_root(root: Path, version: int = 7) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "vmd").mkdir(exist_ok=True)
    (root / "vmd" / "__init__.py").write_text("", encoding="utf-8")
    (root / "VERSION").write_text(str(version), encoding="utf-8")
    return root


def test_which_root_picks_the_first_real_install(tmp_path: Path) -> None:
    good = a_vmd_root(tmp_path / "VMD")
    empty = tmp_path / "not-vmd"
    empty.mkdir()
    result = run_ps(["-WhichRoot", "-Candidates", f"{empty};{good}"])
    assert result.stdout.strip() == str(good)


def test_which_root_is_empty_when_nothing_is_an_install(tmp_path: Path) -> None:
    empty = tmp_path / "not-vmd"
    empty.mkdir()
    result = run_ps(["-WhichRoot", "-Candidates", str(empty)])
    assert result.stdout.strip() == ""


# --------------------------------------------------------------------------- #
#  The fallback copy engine
# --------------------------------------------------------------------------- #


def an_install(root: Path) -> Path:
    """A folder shaped like a real one: the program, and the machine's own."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "vmd").mkdir()
    (root / "vmd" / "__init__.py").write_text("", encoding="utf-8")
    (root / "vmd" / "app.py").write_text("old\n", encoding="utf-8")
    (root / "vmd" / "gone.py").write_text("removed upstream\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "install.ps1").write_text("old\n", encoding="utf-8")
    (root / "VMD.bat").write_text("old\n", encoding="utf-8")
    (root / "VERSION").write_text("7", encoding="utf-8")

    # The machine's own - none of this may ever be written over.
    (root / "settings.json").write_text('{"password": "secret"}', encoding="utf-8")
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
    (folder / "vmd" / "__init__.py").write_text("", encoding="utf-8")
    (folder / "vmd" / "app.py").write_text("new\n", encoding="utf-8")
    (folder / "VMD.bat").write_text("new\n", encoding="utf-8")
    (folder / "VERSION").write_text("8", encoding="utf-8")
    # Carries the machine's own name too, to prove the engine refuses it even
    # when a badly built stick offers one.
    (folder / "settings.json").write_text('{"password": "ATTACKER"}', encoding="utf-8")
    return folder


def test_the_engine_replaces_the_program_and_leaves_the_machine_alone(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    files = new_files(tmp_path / "files")

    result = run_ps(["-CopyEngineOnly", "-Root", str(root), "-Files", str(files)])

    assert verdict(result) == "OK", result.stdout + result.stderr
    # The program is the new one.
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "new\n"
    assert (root / "VMD.bat").read_text(encoding="utf-8") == "new\n"
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "8"
    # The machine's own is exactly as it was - including the settings file the
    # stick tried to carry over it.
    assert (root / "settings.json").read_text(encoding="utf-8") == '{"password": "secret"}'
    assert (root / "cameras" / "250" / "settings.json").is_file()
    assert (root / "recordings" / "clip.mp4").read_bytes() == b"footage"
    assert (root / "bin" / "uv.exe").read_bytes() == b"binary"
    assert (root / ".venv" / "marker").is_file()


def test_the_engine_prunes_a_module_the_new_version_dropped(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")  # has vmd\gone.py
    files = new_files(tmp_path / "files")  # does not

    run_ps(["-CopyEngineOnly", "-Root", str(root), "-Files", str(files)])

    assert not (root / "vmd" / "gone.py").exists()


def test_the_engine_keeps_the_old_program_it_overwrote(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    files = new_files(tmp_path / "files")

    run_ps(["-CopyEngineOnly", "-Root", str(root), "-Files", str(files)])

    kept = root / "previous" / "7"
    assert (kept / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert (kept / "VERSION").read_text(encoding="utf-8").strip() == "7"
    # A backup with the machine's own things in it would be a rollback that puts
    # an old password back.
    assert not (kept / "settings.json").exists()


def test_the_engine_refuses_a_folder_that_is_not_an_install(tmp_path: Path) -> None:
    not_vmd = tmp_path / "random"
    not_vmd.mkdir()
    files = new_files(tmp_path / "files")

    result = run_ps(["-CopyEngineOnly", "-Root", str(not_vmd), "-Files", str(files)])

    assert verdict(result).startswith("FAIL")
    # Nothing was created in the folder it refused.
    assert not (not_vmd / "vmd").exists()
