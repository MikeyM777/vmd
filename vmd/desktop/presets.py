"""The two cameras this desk watches, and switching a console between them.

The operator has two identical cameras on one network - 192.168.1.250 and
192.168.1.251 - and until now switching from one to the other meant editing an
address by hand in three places: the camera's own address, and the URL of each
picture. Getting two of the three right is a console that shows a picture it
cannot steer, or steers a camera it cannot show, and that is exactly what
happened.

So a camera is chosen from a list instead of typed, and the second camera is not
typed at all: it is THIS camera's settings with one address changed. The two are
the same model on the same network with the same login and the same stream
paths; the only thing that differs is the last part of the address. Anything
derived that way cannot have two of three places right.

Nothing here is Qt and nothing here starts a process. What it does is answer
"which cameras are there", "which one am I", and "what would the other one's
settings file look like" - which is the part worth testing, and the part that
was got wrong by hand.

Why a console per camera, rather than one console that swaps: everything a
console writes it writes beside its own settings file - go2rtc.json, the
recorder's pid, the segment index, events.db, the remembered window. Two
consoles therefore share nothing and cannot disagree. Swapping the settings
under a running console would mean rewiring all of that live, on the one
program whose job is not stopping. See the header of scripts\\cameras.ps1, which
made the same decision for the same reason.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from vmd.settings import Settings, load_settings, save_settings

logger = logging.getLogger(__name__)

# Where a camera's own settings live, relative to the install root. The same
# layout scripts\cameras.ps1 writes and scripts\_common.ps1 reads, because a
# second layout for the same thing is two answers to "which cameras are there".
CAMERAS = "cameras"

SETTINGS_NAME = "settings.json"

# What marks the folder VMD is installed in, looked for upwards from whichever
# settings file this console was started with. A console run from
# cameras\250\settings.json is three folders down from it.
ROOT_MARKS = ("VMD.exe", "VMD.bat", "pyproject.toml")

# An IPv4 address anywhere in a longer string - an address on its own, or one
# inside rtsp://user@address:554/ch0. Matched rather than parsed because what it
# is found in is a URL the operator typed, and the only part being changed is
# the digits.
_ADDRESS = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")


@dataclass(frozen=True)
class Preset:
    """One camera this console can be pointed at."""

    #: The folder name, which is the last part of the address: "250".
    name: str
    #: The settings file a console for it would be started with.
    settings_path: Path
    #: What it watches, in the operator's words. Empty when it has no title.
    title: str = ""

    def label(self) -> str:
        """What the button says. The number, always - it is what he calls them.

        The title is on the window and the shortcut already; a button in a row
        of two has room for a number and nothing else, and "250" is the name he
        uses out loud.
        """
        return self.name


def install_root(settings_path: Path | str) -> Path | None:
    """The folder VMD is installed in, found by walking up from a settings file.

    Answers None rather than guessing. Every caller uses this to decide where to
    start another console from, and starting one out of the wrong folder is a
    second console pointed at a different install.
    """
    here = Path(settings_path).expanduser().resolve().parent
    for folder in (here, *here.parents):
        if any((folder / mark).exists() for mark in ROOT_MARKS):
            return folder
    return None


def last_part(host: str) -> str:
    """The last group of an address - "250" out of "192.168.1.250".

    What the operator calls the camera, and what the folder is named. A host
    that is not an address answers itself, so a camera reached by name still
    gets a usable folder name rather than an empty one.
    """
    host = (host or "").strip()
    found = _ADDRESS.search(host)
    if found is None:
        return host
    return found.group(4)


def swap_address(text: str, new_host: str) -> str:
    """Put `new_host` in place of whatever address is in `text`.

    Used on the camera's address and on each picture's URL, which is what makes
    the three places impossible to get out of step: they are all rewritten from
    one answer, in one pass, or not at all.
    """
    if not text:
        return text
    replacement = (new_host or "").strip()
    if not replacement:
        return text
    return _ADDRESS.sub(replacement, text, count=1)


def derive(settings: Settings, new_host: str) -> Settings:
    """This camera's settings, pointed at another camera of the same kind.

    A deep copy with every address rewritten - the camera's own, and the URL of
    each picture. Nothing else is touched: the login, the stream paths, the
    detection areas and the disk budget are all properties of how this site is
    set up rather than of which of its two cameras is being watched.

    The title is deliberately left alone here. It is the one field a person has
    to choose, and the dialog that calls this asks for it.
    """
    other = settings.model_copy(deep=True)
    other.camera.host = swap_address(other.camera.host, new_host) or new_host
    for stream in other.camera.streams:
        stream.url = swap_address(stream.url, new_host)
    return other


def presets(root: Path | str) -> list[Preset]:
    """Every camera set up under the install, in the order their names sort.

    A folder without a settings file is not a camera - it is a folder somebody
    made - so it is not offered. The order is by name so that 250 is always
    left of 251 and the two buttons never swap places between starts.
    """
    folder = Path(root) / CAMERAS
    if not folder.is_dir():
        return []
    found: list[Preset] = []
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        settings_file = entry / SETTINGS_NAME
        if not entry.is_dir() or not settings_file.is_file():
            continue
        found.append(Preset(name=entry.name, settings_path=settings_file, title=_title(settings_file)))
    return found


def _title(settings_file: Path) -> str:
    """What that camera calls itself, or "" if it cannot be read.

    Never raises. This runs to draw a row of buttons, and a camera folder with a
    settings file nobody can parse must cost that camera its title and not the
    whole row.
    """
    try:
        return load_settings(settings_file).title or ""
    except Exception:  # noqa: BLE001 - a title is a nicety; the button is not
        logger.debug("could not read a title from %s", settings_file, exc_info=True)
        return ""


def current(settings_path: Path | str, among: list[Preset]) -> Preset | None:
    """Which of them this console is, or None when it is not one of them.

    None is the ordinary state of an install that has never had a second camera
    set up: the console is running out of the root settings.json, which is not
    under cameras\\ at all.
    """
    mine = Path(settings_path).expanduser().resolve()
    for preset in among:
        if preset.settings_path.expanduser().resolve() == mine:
            return preset
    return None


def others(settings_path: Path | str, among: list[Preset]) -> list[Preset]:
    """Every camera except the one this console is showing."""
    mine = current(settings_path, among)
    return [preset for preset in among if preset is not mine]


def suggested_host(settings: Settings, taken: list[Preset]) -> str:
    """A first guess at the other camera's address, for the dialog to show.

    The next address up, because that is how this site is laid out - 250 and 251
    - and because a field that opens with the right answer in it is a field
    nobody has to be talked through over the telephone. It is only a suggestion;
    the dialog lets it be changed, and a site numbered any other way simply
    types over it.
    """
    host = (settings.camera.host or "").strip()
    found = _ADDRESS.search(host)
    if found is None:
        return ""
    last = int(found.group(4))
    used = {preset.name for preset in taken}
    for step in (1, -1):
        candidate = last + step
        if 0 < candidate < 255 and str(candidate) not in used:
            return _ADDRESS.sub(
                f"{found.group(1)}.{found.group(2)}.{found.group(3)}.{candidate}", host, count=1
            )
    return ""


def console_command(root: Path | str, settings_path: Path | str) -> list[str]:
    """What starts another console on that settings file.

    VMD.exe when it has been built and VMD.bat when it has not, which is the
    same order `vmd/update/main.py` starts a console in and the same order
    `scripts/cameras.ps1` writes into a shortcut. A console started this way is
    an ordinary second instance: it shares the program folder and nothing else,
    because everything it writes goes beside the settings file it was given.
    """
    root = Path(root)
    exe = root / "VMD.exe"
    starter = exe if exe.is_file() else root / "VMD.bat"
    return [str(starter), "--settings", str(settings_path)]


def write_preset(root: Path | str, name: str, settings: Settings) -> Path:
    """Put a camera's settings in its own folder and answer where they went.

    The folder is made if it is not there. An existing settings file is NOT
    overwritten - a camera that is already set up has its own detection areas,
    its own disk budget and its own remembered window, and none of that is this
    function's to throw away because somebody pressed a button twice.
    """
    folder = Path(root) / CAMERAS / name
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / SETTINGS_NAME
    if target.exists():
        return target
    save_settings(settings, target)
    return target
