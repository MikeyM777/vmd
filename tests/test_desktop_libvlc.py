"""Finding libVLC on a machine that has VLC installed.

The failure this file exists for was reported from the deployment laptop: the
console said `No video here: thermal: could not find module libvlc.dll` on a
machine with VLC installed and working. Everything here is the search that
sentence should never have needed, driven by fake registries, fake folders and
fake headers so that all seven ways it goes wrong can be run on any machine.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

from vmd.desktop.libvlc import (
    ARM64,
    X64,
    X86,
    LibVlc,
    VlcUnavailable,
    find_libvlc,
    machine_of,
)

PROGRAM_FILES = r"C:\Program Files"
PROGRAM_FILES_X86 = r"C:\Program Files (x86)"
HERE = Path(r"C:\Program Files\VideoLAN\VLC")
THERE = Path(r"C:\Program Files (x86)\VideoLAN\VLC")
PER_USER = Path(r"C:\Users\op\AppData\Local\Programs\VideoLAN\VLC")
ELSEWHERE = Path(r"D:\Tools\VLC")
# Where the console itself lives, which on the deployment laptop is a folder in
# somebody's profile and not the one any document says it is.
BESIDE = Path(r"C:\Users\op\vmd")

WINDOWS = {
    "ProgramFiles": PROGRAM_FILES,
    "ProgramFiles(x86)": PROGRAM_FILES_X86,
    "ProgramW6432": PROGRAM_FILES,
    "LOCALAPPDATA": r"C:\Users\op\AppData\Local",
    "PATH": r"C:\Windows;C:\Windows\System32",
}

# The words the console never says to whoever is standing in front of it. The
# same list the picker and the settings tab are held to.
BANNED = ("yolo", "cnn", "classifier", "inference", "model", "sensor")


def a_registry(**entries: str):
    """A registry with `HKLM_64="C:\\..."` in it and nothing else."""

    def read(hive: str, view: int) -> str | None:
        return entries.get(f"{hive}_{view}")

    return read


def an_empty_registry():
    return a_registry()


def a_disk(*paths: Path):
    """A filesystem holding exactly these paths."""
    present = {str(path).lower() for path in paths}

    def exists(path: Path) -> bool:
        return str(path).lower() in present

    return exists


def an_installation(folder: Path, machine: int = X64):
    """The three things a working VLC folder has: the library, its neighbour and
    the plugins tree."""
    return (
        a_disk(folder, folder / "libvlc.dll", folder / "libvlccore.dll", folder / "plugins"),
        {str(folder / "libvlc.dll").lower(): machine},
    )


def look(
    registry=None,
    exists=None,
    machines: dict[str, int] | None = None,
    environ: dict[str, str] | None = None,
    python_bits: int = 64,
    app_folder: Path | None = None,
) -> LibVlc:
    machines = machines or {}
    return find_libvlc(
        read_install_dir=registry or an_empty_registry(),
        exists=exists or a_disk(),
        read_machine=lambda path: machines.get(str(path).lower()),
        environ=WINDOWS if environ is None else environ,
        python_bits=python_bits,
        app_folder=app_folder,
    )


def refusal(**kwargs) -> str:
    with pytest.raises(VlcUnavailable) as raised:
        look(**kwargs)
    return str(raised.value)


# ------------------------------------------------------------------ it is found


def test_vlc_recorded_in_the_machine_wide_registry_is_found() -> None:
    """The ordinary installation, and the only case that ever worked. The folder
    is one nothing would guess, so the registry is the only way to reach it."""
    exists, machines = an_installation(ELSEWHERE)
    found = look(registry=a_registry(HKLM_64=str(ELSEWHERE)), exists=exists, machines=machines)

    assert found.folder == ELSEWHERE
    assert found.dll == ELSEWHERE / "libvlc.dll"
    assert found.plugins == ELSEWHERE / "plugins"


@pytest.mark.parametrize("key", ["HKLM_64", "HKLM_32", "HKCU_64", "HKCU_32"])
def test_the_folder_is_found_whichever_of_the_four_keys_it_landed_in(key: str) -> None:
    """VLC installed without an administrator writes under the user rather than
    the machine, and a 32-bit installer writes under `WOW6432Node`. All four
    combinations happen; python-vlc reads two of them."""
    exists, machines = an_installation(ELSEWHERE)
    found = look(registry=a_registry(**{key: str(ELSEWHERE)}), exists=exists, machines=machines)

    assert found.folder == ELSEWHERE


def test_a_64_bit_vlc_recorded_only_in_the_32_bit_view_is_still_found() -> None:
    """An installer that wrote its folder under `WOW6432Node` is invisible to
    python-vlc and perfectly usable to us. Again the folder is one no fallback
    would stumble on, so the 32-bit view is the only way it can be found."""
    exists, machines = an_installation(ELSEWHERE)
    found = look(registry=a_registry(HKLM_32=str(ELSEWHERE)), exists=exists, machines=machines)

    assert found.folder == ELSEWHERE


@pytest.mark.parametrize(
    "folder",
    [HERE, THERE, PER_USER],
    ids=["program-files", "program-files-x86", "per-user"],
)
def test_vlc_in_a_usual_folder_is_found_with_nothing_in_the_registry(folder: Path) -> None:
    """The registry entry is not sacred: the folders are checked whether or not
    anything ever recorded them.

    python-vlc's own fallback list is `%ProgramFiles%` and `%HOMEDRIVE%` and
    stops there. It never looks in `Program Files (x86)` and never in
    `%LOCALAPPDATA%\\Programs`, which is where VLC installs itself when it is
    installed without an administrator - so a per-user VLC is invisible to it
    twice over, once in the registry and once here.
    """
    exists, machines = an_installation(folder)
    found = look(exists=exists, machines=machines)

    assert found.folder == folder


# --------------------------------------------------- a VLC that travels with us


def test_a_vlc_carried_in_the_app_folder_is_found() -> None:
    """The offline laptop cannot install anything: no network, no store, and a
    person at the far end of a USB stick on the day the camera goes up. A VLC
    folder that travels inside the project and is simply found needs none of
    that."""
    exists, machines = an_installation(BESIDE / "VLC")
    found = look(exists=exists, machines=machines, app_folder=BESIDE)

    assert found.folder == BESIDE / "VLC"


def test_the_vlc_carried_with_the_app_wins_over_whatever_is_installed() -> None:
    """Someone put that folder there on purpose, and on this machine on purpose.
    What a machine-wide installer once did is the weaker claim of the two."""
    exists = a_disk(
        HERE,
        HERE / "libvlc.dll",
        HERE / "libvlccore.dll",
        HERE / "plugins",
        BESIDE / "VLC",
        BESIDE / "VLC" / "libvlc.dll",
        BESIDE / "VLC" / "libvlccore.dll",
        BESIDE / "VLC" / "plugins",
    )
    machines = {
        str(HERE / "libvlc.dll").lower(): X64,
        str(BESIDE / "VLC" / "libvlc.dll").lower(): X64,
    }
    found = look(
        registry=a_registry(HKLM_64=str(HERE)),
        exists=exists,
        machines=machines,
        app_folder=BESIDE,
    )

    assert found.folder == BESIDE / "VLC", "the carried VLC lost to an installed one"


def test_a_32_bit_vlc_carried_with_the_app_is_refused_in_those_words() -> None:
    """Shipping the wrong one is easier than installing the wrong one - it is
    copied once, on a different machine, by someone who cannot test it here. It
    has to fail as loudly as an installed one, naming the folder that travelled."""
    exists, machines = an_installation(BESIDE / "VLC", machine=X86)
    said = refusal(exists=exists, machines=machines, app_folder=BESIDE)

    assert "32-bit" in said and "64-bit" in said
    assert str(BESIDE / "VLC") in said


def test_a_carried_vlc_that_is_not_there_falls_through_to_everything_else() -> None:
    """Nothing beside the app is the ordinary case, not a fault: an installed
    VLC must still be found, exactly as it is now."""
    exists, machines = an_installation(HERE)
    found = look(exists=exists, machines=machines, app_folder=BESIDE)

    assert found.folder == HERE


def test_a_folder_named_by_the_operator_is_tried_before_anything_else() -> None:
    exists, machines = an_installation(ELSEWHERE)
    found = look(
        exists=exists,
        machines=machines,
        environ=dict(WINDOWS, PYTHON_VLC_LIB_PATH=str(ELSEWHERE / "libvlc.dll")),
    )

    assert found.folder == ELSEWHERE


def test_a_folder_the_operator_named_that_is_wrong_does_not_hide_a_good_one() -> None:
    """Honouring a stale hand-set folder is how this bug reproduces itself."""
    good, machines = an_installation(HERE)
    found = look(
        exists=good,
        machines=machines,
        environ=dict(WINDOWS, PYTHON_VLC_LIB_PATH=r"D:\gone\libvlc.dll"),
    )

    assert found.folder == HERE


# ---------------------------------------------------------- it is the wrong one


def test_a_32_bit_vlc_is_refused_in_those_words_and_not_as_a_missing_file() -> None:
    """The whole point. A 32-bit VLC cannot be loaded by this console at all,
    and the failure looks exactly like VLC not being installed - which sends
    whoever reads it off to reinstall the thing they already have."""
    exists, machines = an_installation(THERE, machine=X86)
    said = refusal(registry=a_registry(HKLM_32=str(THERE)), exists=exists, machines=machines)

    assert "32-bit" in said and "64-bit" in said
    assert str(THERE) in said
    assert "libvlc.dll" not in said
    assert "could not find" not in said.lower()


def test_a_64_bit_vlc_under_a_32_bit_console_is_refused_the_other_way_round() -> None:
    exists, machines = an_installation(HERE)
    said = refusal(
        registry=a_registry(HKLM_64=str(HERE)),
        exists=exists,
        machines=machines,
        python_bits=32,
    )

    assert "64-bit version" in said
    assert "32-bit one" in said


def test_a_32_bit_vlc_is_ignored_when_a_64_bit_one_is_also_installed() -> None:
    """Both installed at once is common: the wrong one must not win because it
    was written to the registry last."""
    exists = a_disk(
        HERE,
        HERE / "libvlc.dll",
        HERE / "libvlccore.dll",
        HERE / "plugins",
        THERE,
        THERE / "libvlc.dll",
        THERE / "libvlccore.dll",
        THERE / "plugins",
    )
    machines = {
        str(THERE / "libvlc.dll").lower(): X86,
        str(HERE / "libvlc.dll").lower(): X64,
    }
    found = look(registry=a_registry(HKLM_32=str(THERE)), exists=exists, machines=machines)

    assert found.folder == HERE


def test_half_a_vlc_folder_is_refused_before_it_can_end_the_program() -> None:
    """Measured, not imagined: a folder holding libvlc.dll and the plugins tree
    but not libvlccore.dll gets as far as python-vlc, which cannot load it and
    answers `sys.exit(1)`. That is the one exit this console cannot survive
    cleanly, and it is exactly what half-copying a folder onto a USB stick
    produces - which is now a thing this system invites people to do."""
    exists = a_disk(HERE, HERE / "libvlc.dll", HERE / "plugins")
    machines = {str(HERE / "libvlc.dll").lower(): X64}
    said = refusal(registry=a_registry(HKLM_64=str(HERE)), exists=exists, machines=machines)

    assert str(HERE) in said
    assert "missing" in said or "incomplete" in said
    assert "whole" in said, "nobody is told to copy the whole folder"


def test_vlc_without_its_plugins_is_refused_rather_than_left_to_play_nothing() -> None:
    """A library with no plugins beside it starts, reports itself well and shows
    a black rectangle for ever. Refusing is the kinder failure: it says what to
    do, where a black rectangle says nothing."""
    exists = a_disk(HERE, HERE / "libvlc.dll", HERE / "libvlccore.dll")
    machines = {str(HERE / "libvlc.dll").lower(): X64}
    said = refusal(registry=a_registry(HKLM_64=str(HERE)), exists=exists, machines=machines)

    assert "plugins" in said
    assert str(HERE) in said


# ------------------------------------------------------------- it is not there


def test_nothing_anywhere_says_where_it_looked_and_what_to_install() -> None:
    said = refusal()

    assert "not installed" in said
    assert str(HERE) in said, "the operator is not told where to put it"
    assert str(THERE) in said
    assert str(PER_USER) in said
    assert "64-bit" in said


def test_a_registry_entry_pointing_at_a_deleted_folder_is_not_believed() -> None:
    """Uninstalling VLC leaves the key behind often enough to be the ordinary
    case, not a corner one."""
    said = refusal(registry=a_registry(HKLM_64=str(ELSEWHERE)))

    assert "not installed" in said
    assert str(ELSEWHERE) in said, "the folder that was promised is not named"
    # And the folder it says to install into is the ordinary one, not the dead
    # one it was just pointed at.
    assert f"into {HERE}" in said, said


def test_a_machine_that_says_nothing_about_itself_still_gets_a_whole_sentence() -> None:
    """A stripped environment - a service account, a shell that inherited
    nothing - must not produce a sentence with a hole where a folder should be."""
    said = refusal(environ={})

    assert "Looked in ." not in said
    assert r"Program Files\VideoLAN\VLC" in said


def test_a_folder_that_is_there_but_holds_no_vlc_is_reported_as_no_vlc() -> None:
    said = refusal(registry=a_registry(HKLM_64=str(HERE)), exists=a_disk(HERE))

    assert "not installed" in said
    assert str(HERE) in said


def test_a_library_whose_header_cannot_be_read_is_not_guessed_at() -> None:
    """An unreadable or truncated file is not a working VLC, and loading it to
    find out is how the console crashes instead of explaining."""
    exists = a_disk(HERE, HERE / "libvlc.dll", HERE / "plugins")
    said = refusal(registry=a_registry(HKLM_64=str(HERE)), exists=exists, machines={})

    assert "not installed" in said or "damaged" in said


# --------------------------------------------------------------- how it reads


def a_case(*present: Path, machine: int = X64) -> dict:
    """A refusal built from a folder that holds only these files."""
    return {
        "registry": a_registry(HKLM_64=str(HERE)),
        "exists": a_disk(HERE, *present),
        "machines": {str(HERE / "libvlc.dll").lower(): machine},
    }


@pytest.mark.parametrize(
    "case",
    [
        {},
        a_case(HERE / "libvlc.dll", HERE / "libvlccore.dll", HERE / "plugins", machine=X86),
        a_case(HERE / "libvlc.dll", HERE / "libvlccore.dll"),
        a_case(HERE / "libvlc.dll", HERE / "plugins"),
    ],
    ids=["nothing-installed", "wrong-bitness", "no-plugins", "half-copied"],
)
def test_every_refusal_is_a_sentence_the_operator_can_act_on(case: dict) -> None:
    said = refusal(**case)

    assert said.endswith(".")
    assert not any(word in said.lower() for word in BANNED), said
    assert "traceback" not in said.lower()
    assert "exception" not in said.lower()
    # Every one of them ends somewhere the operator can go.
    assert "VLC" in said
    assert "console again" in said, said


# ------------------------------------------------------- telling this process


def test_what_was_found_is_handed_to_the_process_that_has_to_load_it() -> None:
    """Three names and one search path, and none of them is optional.

    python-vlc reads the first two at import. The third is what libVLC itself
    reads to find its plugins. The folder has to be added to the library search
    path on top, because since Python 3.8 a library loaded by full path no
    longer finds the neighbours it needs on `PATH` - and `libvlc.dll` cannot do
    anything at all without `libvlccore.dll` beside it.
    """
    from vmd.desktop.libvlc import announce

    found = LibVlc(folder=HERE, dll=HERE / "libvlc.dll", plugins=HERE / "plugins")
    environ: dict[str, str] = {}
    searched: list[str] = []

    announce(found, environ=environ, add_dll_directory=searched.append)

    assert environ["PYTHON_VLC_LIB_PATH"] == str(HERE / "libvlc.dll")
    assert environ["PYTHON_VLC_MODULE_PATH"] == str(HERE / "plugins")
    assert environ["VLC_PLUGIN_PATH"] == str(HERE / "plugins")
    assert searched == [str(HERE)]


def test_a_stale_hand_set_path_is_replaced_rather_than_left_to_win() -> None:
    """This is the one place the console overrules whoever came before it. A
    variable left pointing at a VLC that has been removed makes python-vlc give
    up on the spot, whatever else is installed - which is the bug, arriving by a
    second route."""
    from vmd.desktop.libvlc import announce

    found = LibVlc(folder=HERE, dll=HERE / "libvlc.dll", plugins=HERE / "plugins")
    environ = {"PYTHON_VLC_LIB_PATH": r"D:\gone\libvlc.dll", "VLC_PLUGIN_PATH": r"D:\gone"}

    announce(found, environ=environ, add_dll_directory=lambda folder: None)

    assert environ["PYTHON_VLC_LIB_PATH"] == str(HERE / "libvlc.dll")
    assert environ["VLC_PLUGIN_PATH"] == str(HERE / "plugins")


def test_a_folder_windows_will_not_search_is_not_a_reason_to_show_nothing() -> None:
    """Worth a line in the log and nothing more: the library may well still
    load, and refusing here would cost a picture that was about to work."""
    from vmd.desktop.libvlc import announce

    found = LibVlc(folder=HERE, dll=HERE / "libvlc.dll", plugins=HERE / "plugins")
    environ: dict[str, str] = {}

    def refuse(folder: str) -> None:
        raise OSError("no such folder")

    announce(found, environ=environ, add_dll_directory=refuse)

    assert environ["PYTHON_VLC_LIB_PATH"] == str(HERE / "libvlc.dll")


# ------------------------------------------------------------------ the header


def a_windows_binary(path: Path, machine: int) -> Path:
    header = bytearray(0x200)
    header[0:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x80)
    header[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", header, 0x84, machine)
    path.write_bytes(bytes(header))
    return path


@pytest.mark.parametrize("machine", [X64, X86, ARM64])
def test_the_bitness_of_a_real_windows_binary_is_read_from_its_header(
    tmp_path: Path, machine: int
) -> None:
    assert machine_of(a_windows_binary(tmp_path / "libvlc.dll", machine)) == machine


def test_a_file_that_is_not_a_windows_binary_reads_as_nothing(tmp_path: Path) -> None:
    junk = tmp_path / "libvlc.dll"
    junk.write_bytes(b"this is not a library")
    assert machine_of(junk) is None


def test_a_file_that_is_not_there_reads_as_nothing(tmp_path: Path) -> None:
    assert machine_of(tmp_path / "nowhere" / "libvlc.dll") is None


# --------------------------------------------- what the import must not do

# Loading VLC in a fresh interpreter, and reporting the one thing that cannot be
# asked of an interpreter that has already imported it. python-vlc looks for the
# library once, at import, and the working directory it changes is changed
# there.
LOADS_VLC = """
import os, sys
before = os.getcwd()
sys.path.insert(0, %(repo)r)
from vmd.desktop.video import load_vlc
try:
    load_vlc()
except Exception as exc:
    print("SKIP", exc)
else:
    print("CWD", before, "->", os.getcwd())
"""


def test_loading_vlc_leaves_the_working_directory_where_it_found_it(tmp_path: Path) -> None:
    """python-vlc's own search `os.chdir`s into the VLC folder, loads the
    library and chdirs back - inside an import, under a running application.

    The console resolves several paths against the working directory, the
    settings file among them, and a chdir that is not undone - an exception
    between the two, a second thread reading a path in the window between them -
    moves all of them at once. Handing python-vlc the library's full path
    returns it before that branch is ever reached, so the whole manoeuvre never
    happens. This is what proves it, in a fresh interpreter, because the chdir
    is at import and this one has imported already.
    """
    import subprocess

    script = tmp_path / "loads_vlc.py"
    script.write_text(LOADS_VLC % {"repo": str(Path(__file__).resolve().parents[1])})
    done = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
    )
    said = [line for line in done.stdout.splitlines() if line.startswith(("CWD", "SKIP"))]
    assert said, done.stderr[-2000:]
    if said[0].startswith("SKIP"):
        pytest.skip(said[0])

    before, _, after = said[0][len("CWD ") :].partition(" -> ")
    assert Path(after) == Path(before), "libVLC moved the working directory"
    assert Path(after) == tmp_path


# ------------------------------------------------------- on this machine, for real


@pytest.mark.integration
def test_libvlc_really_loads_and_really_makes_an_instance() -> None:
    """The only test that proves the plumbing. Everything above is fakes."""
    from vmd.desktop import libvlc

    try:
        found = libvlc.prepare()
    except VlcUnavailable as exc:
        pytest.skip(f"no usable VLC on this machine: {exc}")

    assert found.dll.exists()
    assert found.plugins.is_dir()

    import vlc

    instance = vlc.Instance(["--no-audio", "--no-video-title-show"])
    assert instance is not None
    player = instance.media_player_new()
    assert player is not None
    player.release()
    instance.release()
