"""Finding the VLC that is already on the machine, before anything imports it.

The console draws its picture with libVLC, which is a separate installation with
its own folder, its own version and its own architecture. `import vlc` -
python-vlc - goes looking for it in three places and no others: the
`PYTHON_VLC_LIB_PATH` variable, the `Software\\VideoLAN\\VLC` key **in the one
registry view its own bitness puts it in**, and the process's `PATH`. VLC's
installer does not add itself to `PATH`. So on an ordinary Windows machine the
whole picture rests on one registry value landing in one view.

That is thin enough to break in five ordinary ways, and it broke in the field on
a laptop with a working VLC on it:

* The value is under `WOW6432Node`, because a 32-bit installer wrote it.
* The value is under the user rather than the machine, because VLC was installed
  without an administrator.
* VLC was installed somewhere other than `Program Files\\VideoLAN\\VLC`.
* VLC is 32-bit and this console is 64-bit. A 32-bit library cannot be loaded
  into a 64-bit process at all, and the refusal from Windows reads *could not
  find module libvlc.dll* - the most misleading sentence in this whole system,
  because it sends whoever reads it off to install what they already have.
* The value is left behind by an uninstall and points at a folder that is gone.

So the folder is found here first, deliberately, and only then is python-vlc
imported - by which time it is being told exactly where to look rather than
guessing. Two more things are settled at the same time:

* **The plugins tree.** libVLC without its `plugins` folder starts, answers
  every question cheerfully, and shows a black rectangle for ever. That is a
  worse failure than not starting, because nothing on screen says why. An
  installation missing its plugins is refused here instead.
* **The neighbouring libraries.** `libvlc.dll` needs `libvlccore.dll` beside it,
  and since Python 3.8 a dependency is no longer looked for on `PATH`. The
  folder is added to the search path explicitly.

Everything that decides anything is a plain function over values that are handed
in - a registry reader, an existence check, a header reader - because the seven
ways this goes wrong cannot all be arranged on any one machine, and the machine
this was written on is not the machine it has to work on.
"""

from __future__ import annotations

import logging
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, MutableMapping

from vmd import app_folder

logger = logging.getLogger(__name__)

# The architectures a Windows binary announces in its header. There is no other
# honest way to ask: a 32-bit library and a missing one fail identically when
# they are loaded, and by then the answer costs a crash instead of a sentence.
X86 = 0x14C
X64 = 0x8664
ARM64 = 0xAA64

# Width is what decides whether a library can be loaded at all, and the split
# that matters is 32 against 64. An ARM build in an Intel process would pass this
# and fail at the load; that is caught one layer up, where the import is guarded
# and turned into a sentence, and it is not a machine anything here ships to.
BITS = {X86: 32, X64: 64, ARM64: 64}

LIBRARY = "libvlc.dll"
# The library that libvlc.dll is a thin wrapper around. It is checked for by
# name because its absence is not an error anyone can read: python-vlc ends the
# process rather than raising.
CORE = "libvlccore.dll"
PLUGINS = "plugins"

# Where VLC records itself. Each hive is read in both views, because a 32-bit
# installer writes under `WOW6432Node` and a 64-bit one does not, and python-vlc
# only ever sees the view that matches its own build.
HIVES = (("HKLM", 64), ("HKLM", 32), ("HKCU", 64), ("HKCU", 32))

# Where VLC ends up when nothing recorded it. Named by the variable rather than
# the letter, because `Program Files` is not `C:\Program Files` on every machine
# and the deployment laptop is not this one.
USUAL = (
    ("ProgramFiles", r"VideoLAN\VLC"),
    ("ProgramFiles(x86)", r"VideoLAN\VLC"),
    ("ProgramW6432", r"VideoLAN\VLC"),
    ("LOCALAPPDATA", r"Programs\VideoLAN\VLC"),
)

# What every refusal ends with. The console has to be restarted to pick up a new
# installation, and the operator has no terminal to do anything cleverer in.
RESTART = "Then close and open the console again."
RECORDING = "Recording carries on without a picture."


class VlcUnavailable(RuntimeError):
    """No usable VLC. Carries one sentence meant for whoever is standing at the
    laptop, because that is where it ends up: the pane shows it verbatim."""


@dataclass(frozen=True)
class LibVlc:
    """A VLC installation that has been checked rather than hoped for."""

    folder: Path
    dll: Path
    plugins: Path


def machine_of(path: Path | str) -> int | None:
    """The architecture in a Windows binary's header, or None if it is not one.

    Four bytes at 0x3C say where the PE header starts; two bytes at its start
    plus four say what it was built for. Reading is cheap and cannot fail the
    way loading can - a 32-bit library loaded into this process is a refusal
    from Windows that reads like a missing file.
    """
    try:
        with open(path, "rb") as binary:
            head = binary.read(0x40)
            if len(head) < 0x40 or head[:2] != b"MZ":
                return None
            start = struct.unpack_from("<I", head, 0x3C)[0]
            binary.seek(start)
            signature = binary.read(6)
        if len(signature) < 6 or signature[:4] != b"PE\0\0":
            return None
        return int(struct.unpack_from("<H", signature, 4)[0])
    except OSError:
        return None


def _candidates(
    read_install_dir: Callable[[str, int], str | None],
    environ: Mapping[str, str],
    app_folder: Path | None,
) -> tuple[list[Path], list[Path]]:
    """Every folder worth opening, and the ones worth naming if none of them
    hold anything.

    The order is an order of intent. A folder somebody chose beats a folder
    something happened to write, and both beat a guess.

    The named list is deliberately shorter: `PATH` is searched because a folder
    on it might hold VLC, but reciting forty system folders at an operator is
    not telling them where to look.
    """
    folders: list[Path] = []
    named: list[Path] = []

    def add(folder: Path, tell: bool = True) -> None:
        if folder not in folders:
            folders.append(folder)
            if tell:
                named.append(folder)

    # Whatever anyone set by hand goes first, so a VLC in an unguessable place
    # can still be pointed at. It is checked like everything else: a stale
    # value that no longer resolves must not hide a working installation.
    hand_set = environ.get("PYTHON_VLC_LIB_PATH")
    if hand_set:
        add(Path(hand_set).parent)

    # Then a VLC carried beside the console, which is how it reaches a laptop
    # that has no network to install one from: the folder travels inside the
    # project, on the same stick as everything else, and needs nobody to run an
    # installer as an administrator on the day the camera goes up. It is ahead
    # of the registry and the usual folders on purpose - someone put it there
    # for this machine, where a machine-wide install is only whatever was on
    # the laptop already. It is checked exactly like the rest: shipping a
    # 32-bit one is easier than installing a 32-bit one, because it is copied
    # once, elsewhere, by someone who cannot try it here.
    if app_folder is not None:
        add(Path(app_folder) / "VLC")

    for hive, view in HIVES:
        recorded = read_install_dir(hive, view)
        if recorded:
            add(Path(recorded))

    for variable, tail in USUAL:
        root = environ.get(variable)
        if root:
            add(Path(root) / tail)

    for entry in environ.get("PATH", "").split(os.pathsep):
        if entry.strip():
            add(Path(entry.strip()), tell=False)

    return folders, named


def find_libvlc(
    *,
    read_install_dir: Callable[[str, int], str | None],
    exists: Callable[[Path], bool],
    read_machine: Callable[[Path], int | None],
    environ: Mapping[str, str],
    python_bits: int,
    app_folder: Path | None = None,
) -> LibVlc:
    """The whole search, over inputs that are handed in. Raises `VlcUnavailable`
    with one sentence when there is nothing here this console can use.

    A folder that is right in every way wins outright. Failing that, the
    complaint is the most specific true one: a VLC that is present but of the
    wrong architecture is a two-minute fix and must be named as such, and one
    missing its plugins is a different two-minute fix.
    """
    folders, named = _candidates(read_install_dir, environ, app_folder)

    wrong_bits: list[tuple[Path, int]] = []
    no_plugins: list[Path] = []
    half_there: list[Path] = []

    for folder in folders:
        dll = folder / LIBRARY
        if not exists(dll):
            continue
        bits = BITS.get(read_machine(dll) or 0)
        if bits is None:
            # Not a Windows binary at all, or unreadable. Loading it to find out
            # is exactly the crash this module exists to avoid.
            logger.warning("ignoring %s: it is not a library this console can read", dll)
            continue
        if bits != python_bits:
            wrong_bits.append((folder, bits))
            continue
        # libvlc.dll is a wrapper: everything is in libvlccore.dll beside it,
        # and without that file python-vlc gets far enough to try the load and
        # answers sys.exit(1) - the one exit the console cannot survive
        # cleanly. Measured by copying a VLC folder and leaving that one file
        # out, which is what half-copying a folder onto a stick looks like.
        if not exists(folder / CORE):
            half_there.append(folder)
            continue
        plugins = folder / PLUGINS
        if not exists(plugins):
            no_plugins.append(folder)
            continue
        logger.info("using the VLC in %s", folder)
        return LibVlc(folder=folder, dll=dll, plugins=plugins)

    if half_there:
        folder = half_there[0]
        raise VlcUnavailable(
            f"The VLC in {folder} is missing a file it needs, so it would not "
            f"start. Copy the whole VideoLAN\\VLC folder across, or install VLC "
            f"for Windows ({python_bits}-bit) again. {RESTART} {RECORDING}"
        )

    if no_plugins:
        folder = no_plugins[0]
        raise VlcUnavailable(
            f"The VLC in {folder} is missing its plugins folder, so it would "
            f"open a black picture and never say why. Install VLC for Windows "
            f"({python_bits}-bit) again. {RESTART} {RECORDING}"
        )

    if wrong_bits:
        folder, bits = wrong_bits[0]
        raise VlcUnavailable(
            f"VLC is installed in {folder}, but it is the {bits}-bit version "
            f"and this console needs the {python_bits}-bit one. Install VLC for "
            f"Windows ({python_bits}-bit) over it. {RESTART} {RECORDING}"
        )

    # Where to put it is the ordinary folder, never the first one on the list:
    # that one can be a leftover from an uninstall or a path set by hand years
    # ago, and telling someone to install into a folder that is not there is
    # advice they cannot follow.
    root = environ.get("ProgramFiles")
    home = str(Path(root) / r"VideoLAN\VLC") if root else r"Program Files\VideoLAN\VLC"
    # An environment that says nothing about itself leaves nothing to list, and
    # a sentence with a hole in it is worse than a shorter one.
    places = ", ".join(str(folder) for folder in named) or home
    raise VlcUnavailable(
        f"VLC is not installed on this machine, or it has been removed. Looked "
        f"in {places}. Install VLC for Windows ({python_bits}-bit) into "
        f"{home}. {RESTART} {RECORDING}"
    )


def _install_dir(hive: str, view: int) -> str | None:
    """What the registry says, in one hive and one view, or None.

    Both views are asked for by name. The default view is whichever matches this
    process, which is the assumption that lost the picture in the field.
    """
    if not sys.platform.startswith("win"):  # pragma: no cover - not the platform
        return None
    import winreg

    hives = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
    views = {64: winreg.KEY_WOW64_64KEY, 32: winreg.KEY_WOW64_32KEY}
    try:
        with winreg.OpenKey(
            hives[hive], r"Software\VideoLAN\VLC", 0, winreg.KEY_READ | views[view]
        ) as key:
            value, _ = winreg.QueryValueEx(key, "InstallDir")
    except OSError:
        return None
    return str(value) or None


def _no_dll_directory(folder: str) -> None:  # pragma: no cover - not the platform
    """What adding a search path means where there is no such thing."""
    return None


def announce(
    found: LibVlc,
    environ: MutableMapping[str, str] | None = None,
    add_dll_directory: Callable[[str], object] | None = None,
) -> None:
    """Tell this process where VLC is, in the two languages that get asked.

    python-vlc reads the first two names while it is being imported; libVLC
    reads the third when an instance is made. All three are assigned rather than
    defaulted: one of them left over from an installation that has since been
    removed makes python-vlc give up on the spot, whatever else is on the
    machine, which is this same failure arriving by a second route. A folder set
    by hand is still tried first by the search, so anyone who pointed at a good
    VLC gets their own value written back unchanged.
    """
    environ = os.environ if environ is None else environ
    if add_dll_directory is None:  # pragma: no cover - Windows always has it
        add_dll_directory = getattr(os, "add_dll_directory", _no_dll_directory)

    # PYTHON_VLC_LIB_PATH is not a convenience and must not be removed as one.
    #
    # It is the only branch of python-vlc's search that returns before the
    # Windows one, and the Windows one does this (vlc.py, lines 149-154):
    #
    #     p = os.getcwd()
    #     os.chdir(plugin_path)
    #     dll = ctypes.CDLL(".\\" + libname)
    #     os.chdir(p)
    #
    # There is no try/finally around it. A CDLL that raises - the 32-bit
    # library, a missing dependency, anything - leaves this process sitting in
    # C:\Program Files\VideoLAN\VLC for the rest of its life. The console
    # resolves paths against the working directory when it is not frozen, and
    # settings.json is one of them, so the settings file would then be read
    # from and written to inside the VLC installation. That is a data loss that
    # looks like nothing at all.
    #
    # Setting this variable means that branch is never reached. There is a test
    # that loads VLC in a fresh interpreter and checks the working directory
    # came back where it started; it is guarding this line.
    environ["PYTHON_VLC_LIB_PATH"] = str(found.dll)
    environ["PYTHON_VLC_MODULE_PATH"] = str(found.plugins)
    environ["VLC_PLUGIN_PATH"] = str(found.plugins)

    # Since Python 3.8 a library loaded by full path no longer looks for the
    # ones it depends on along PATH, and libvlc.dll can do nothing without
    # libvlccore.dll beside it. Failing to add the folder is not fatal - the
    # library may well load anyway - and a picture that was about to work must
    # not be thrown away over it.
    try:
        add_dll_directory(str(found.folder))
    except OSError:
        logger.warning("Windows would not search %s for libraries", found.folder)


_found: LibVlc | None = None


def prepare() -> LibVlc:
    """Find VLC and tell this process where it is. Call before `import vlc`.

    The answer is kept: the panes are rebuilt every time the streams change, and
    each rebuild would otherwise re-read the registry and add another folder to
    the library search path. Nothing here is expensive - a handful of registry
    reads and one 64-byte read - but nothing here changes between calls either.
    """
    global _found
    if _found is not None:
        return _found

    found = find_libvlc(
        read_install_dir=_install_dir,
        exists=Path.exists,
        read_machine=machine_of,
        environ=os.environ,
        python_bits=struct.calcsize("P") * 8,
        app_folder=app_folder(),
    )
    announce(found)
    _found = found
    return found
