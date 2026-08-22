# USB-stick updates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the air-gapped console from a dedicated USB stick, filled from GitHub by a laptop with nothing installed on it, with a smoke test and a way back.

**Architecture:** A stick holds a plain copy of the new code (`files\`), a SHA-256 `manifest.json`, any wheels the target lacks, and a note each machine writes about itself. The console's Update button starts a **stdlib-only Python applier** in a temp copy of the current tree, which verifies, stops the console, keeps the old code in `previous\<version>\`, copies, syncs libraries offline, smoke-tests, and restores the old version if anything fails. The laptop side is one PowerShell WinForms script that downloads `master.zip`, diffs `uv.lock` against the machine note, and fetches only the missing wheels.

**Tech Stack:** Python 3.12 (stdlib only in `vmd/update/`), PySide6 for the panel, PowerShell 5.1 + WinForms on the laptop, `bin\uv.exe` for library work, pytest + pytest-qt.

**Deviation from the spec, deliberate:** the spec says the applier is `scripts/apply_update.ps1`. It is Python instead (`vmd/update/apply.py`), because this repo tests everything with pytest and has no PowerShell test runner, and because the applier must be callable in a `tmp_path` tree to be tested at all. Task 12 updates the spec to say so. The laptop side stays PowerShell — that machine has nothing installed.

**Run tests with:** `bin\uv.exe run --offline --frozen --no-sync python -m pytest <path> -v`
Everything below writes that as `uv run ... pytest`.

---

### Task 1: The version number

**Files:**
- Create: `VERSION`
- Create: `vmd/update/__init__.py`
- Create: `vmd/update/version.py`
- Create: `tests/test_update_version.py`
- Modify: `vmd/desktop/window.py` (`set_title`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_update_version.py
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


def test_this_repository_carries_a_version(monkeypatch) -> None:
    """The file has to be in the repository itself: it travels inside every
    update, and it is what the offline machine compares against a stick."""
    root = Path(__file__).resolve().parent.parent
    assert read_version(root) is not None, "VERSION is missing from the project root"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_version.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'vmd.update'`

- [ ] **Step 3: Write the version file and the module**

```
# VERSION
1
```

```python
# vmd/update/__init__.py
"""Updating this copy of VMD from a USB stick.

Everything in this package is stdlib only, and that is a rule rather than a
habit: `vmd/update/apply.py` is run by the bundled interpreter with no virtual
environment at all, at the moment when the environment is being replaced. An
import of pydantic here would be an updater that cannot run during an update.
"""
```

```python
# vmd/update/version.py
"""Which version this copy is, and how to say it.

One whole number in a file called VERSION in the project root, bumped when a
change is worth shipping. It travels inside the update, so after an update the
copy's own VERSION file IS its version - there is no second place for it to be
recorded and no way for two places to disagree.

Not a date and not three numbers: the question this has to answer, over a
telephone, to somebody standing at a laptop with no internet, is "is the stick
newer than the machine". 8 > 7 answers it.
"""

from __future__ import annotations

from pathlib import Path

VERSION_FILE = "VERSION"


def read_version(root: Path | str) -> int | None:
    """The version of the copy in `root`, or None when it cannot be read.

    None rather than 0. A folder with no VERSION file is one that predates this
    or one that is half-copied, and calling that 0 would make every stick in
    the world look newer than it - which is exactly the comparison that must
    not be made on a guess.
    """
    path = Path(root) / VERSION_FILE
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def describe(root: Path | str) -> str:
    """What to print on a window title or a form: "VMD 7"."""
    version = read_version(root)
    return f"VMD {version}" if version is not None else "VMD (version unknown)"
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_version.py -v`
Expected: 4 passed

- [ ] **Step 5: Put the version in the window title**

In `vmd/desktop/window.py`, inside `set_title`, the application name is currently the literal `"VMD"`. Replace the place it is composed with the described version. Read the method first; the change is to compute the prefix once in `__init__`:

```python
# vmd/desktop/window.py - near the other imports
from vmd.update.version import describe as describe_version
```

```python
# vmd/desktop/window.py - in set_title, where the window title is built
        # The version is part of the name of the program, not decoration: it is
        # the first thing anybody is asked for when they report something, and
        # on this machine there is no About box, no terminal and no second
        # screen to find it on.
        program = describe_version(self._settings_path.parent)
        self.setWindowTitle(f"{program} - {name}" if name else program)
```

- [ ] **Step 6: Fix the window tests that assert the old title**

Run: `uv run --frozen --no-sync python -m pytest tests/test_desktop_window.py -v -k title`
Expected: failures naming the exact assertions. Update each to expect the version prefix, e.g. `assert window.windowTitle().startswith("VMD 1")`.

- [ ] **Step 7: Run both suites**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_version.py tests/test_desktop_window.py -v`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add VERSION vmd/update tests/test_update_version.py vmd/desktop/window.py tests/test_desktop_window.py
git commit -m "This copy can say which version it is, and the window says it"
```

---

### Task 2: The manifest — what is on the stick, and is it intact

**Files:**
- Create: `vmd/update/manifest.py`
- Create: `tests/test_update_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_update_manifest.py
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


def test_write_puts_the_manifest_beside_the_folder(tmp_path: Path) -> None:
    folder = a_tree(tmp_path)
    target = tmp_path / "manifest.json"
    write(folder, target)
    assert json.loads(target.read_text(encoding="utf-8"))["files"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_manifest.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'vmd.update.manifest'`

- [ ] **Step 3: Write the module**

```python
# vmd/update/manifest.py
"""What is on the stick, and whether all of it arrived.

There is no signature on an update and there is not going to be one: the stick
is carried by hand to a machine in a locked room, and a signing key on a
borrowed laptop is a worse risk than the one it removes. What this replaces is
something else entirely - a stick written to while somebody pulled it out, a
file that did not fit, a folder copied by Explorer that stopped at 94%. Every
one of those produces a tree that looks complete.

So the manifest is a list of every file with its SHA-256, and nothing on the
machine is touched until every one of them has been read back and matched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CHUNK = 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def build(folder: Path | str) -> dict:
    """A manifest for every file under `folder`, deepest path and all."""
    folder = Path(folder)
    entries = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        entries.append(
            {
                # Forward slashes, because this is compared as text on both
                # sides and the format is not Windows'.
                "path": path.relative_to(folder).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    return {"files": entries}


def write(folder: Path | str, target: Path | str) -> dict:
    """Build a manifest for `folder` and write it to `target`."""
    manifest = build(folder)
    Path(target).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def verify(folder: Path | str, manifest: dict) -> list[str]:
    """Every disagreement between `folder` and `manifest`, in sentences.

    Sentences and not booleans: what this returns is shown to somebody standing
    at the machine, and "3 files do not match" with no names has told them
    nothing they can act on. An empty list means the tree is exactly what the
    manifest says it is.
    """
    folder = Path(folder)
    problems: list[str] = []
    listed = set()

    for entry in manifest.get("files", []):
        name = entry["path"]
        listed.add(name)
        path = folder / name
        if not path.is_file():
            problems.append(f"{name} is missing from the stick")
            continue
        if path.stat().st_size != entry["size"]:
            problems.append(f"{name} is the wrong size")
            continue
        if sha256(path) != entry["sha256"]:
            problems.append(f"{name} does not match what the stick says it should be")

    for path in sorted(folder.rglob("*")):
        if path.is_file():
            name = path.relative_to(folder).as_posix()
            if name not in listed:
                problems.append(f"{name} is on the stick but not in its manifest")

    return problems
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_manifest.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add vmd/update/manifest.py tests/test_update_manifest.py
git commit -m "A stick that half arrived is refused, and the files are named"
```

---

### Task 3: The note a machine writes about itself

**Files:**
- Create: `vmd/update/note.py`
- Create: `tests/test_update_note.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_update_note.py
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
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_note.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'vmd.update.note'`

- [ ] **Step 3: Write the module**

```python
# vmd/update/note.py
"""The note this machine leaves on the stick about itself.

The laptop that fills the stick has to know what the offline machine already
has, or it packs every wheel in the lock every time - which for this project is
torch, and torch is over 2 GB. It cannot ask: there is no network between them
and there never will be. So the machine writes it down, on the stick, in the
one place that travels between the two.

Written EARLY in an update - before anything is replaced - so that even an
update that fails teaches the laptop what this machine has. The failed trip is
already wasted; it must not also be uninformative.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from vmd.update.version import read_version

MACHINES = "machines"


def installed_libraries(root: Path | str) -> dict[str, str]:
    """Every library in this copy's .venv, as name -> version.

    Read off the `.dist-info` folders rather than by asking pip or uv, because
    this runs on a machine in the middle of an update, sometimes with no working
    environment at all. A directory listing cannot fail the way a subprocess
    can.

    Names are lowercased and their separators normalised, which is what makes
    them comparable with the names in uv.lock: PySide6_Essentials and
    pyside6-essentials are the same library, and treating them as two is a 90 MB
    wheel copied for nothing.
    """
    site = Path(root) / ".venv" / "Lib" / "site-packages"
    found: dict[str, str] = {}
    if not site.is_dir():
        return found
    for info in site.glob("*.dist-info"):
        stem = info.name[: -len(".dist-info")]
        if "-" not in stem:
            continue
        name, _, version = stem.rpartition("-")
        found[normalise(name)] = version
    return found


def normalise(name: str) -> str:
    """PEP 503: the one spelling of a package name that both sides agree on."""
    return re.sub(r"[-_.]+", "-", name).lower()


def note(root: Path | str, machine: str, when: str) -> dict:
    return {
        "machine": machine,
        "version": read_version(root),
        "libraries": installed_libraries(root),
        "written": when,
    }


def write_note(root: Path | str, stick: Path | str, machine: str, when: str) -> Path:
    """Write this machine's note onto the stick and return where it went.

    One file per machine, named after the machine, so that one stick can serve
    several sites without either of them packing for the other.
    """
    folder = Path(stick) / MACHINES
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{machine}.json"
    path.write_text(json.dumps(note(root, machine, when), indent=1), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_note.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add vmd/update/note.py tests/test_update_note.py
git commit -m "The offline machine writes down what it has, on the stick"
```

---

### Task 4: Finding the stick and deciding what it is

**Files:**
- Create: `vmd/update/stick.py`
- Create: `tests/test_update_stick.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_update_stick.py
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
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_stick.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'vmd.update.stick'`

- [ ] **Step 3: Write the module**

```python
# vmd/update/stick.py
"""Which drive is the update stick, and what it is offering.

The drives are handed in rather than discovered, everywhere except at the one
edge that has to ask Windows. That is what makes this testable at all: a test
can hand it two folders in tmp_path and get back the sentence the operator
would have seen.
"""

from __future__ import annotations

import ctypes
import json
import string
from dataclasses import dataclass
from pathlib import Path

from vmd.update.version import read_version

UPDATE_JSON = "update.json"
MANIFEST_JSON = "manifest.json"
FILES = "files"
WHEELS = "wheels"

DRIVE_REMOVABLE = 2


@dataclass
class StickState:
    """What the Update panel draws, and nothing else.

    `kind` is one of: none, many, damaged, older, same, ready. The panel
    switches on it; `message` is what it prints; `stick` and `version` are only
    meaningful when `kind` is "ready".
    """

    kind: str
    message: str
    stick: Path | None = None
    version: int | None = None


def removable_drives() -> list[Path]:
    """Every removable drive letter Windows currently has.

    The only function in this package that asks the operating system anything,
    and it is kept to three lines for that reason: everything above it takes a
    list of folders and can be tested with folders.
    """
    found: list[Path] = []
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    except AttributeError:
        return found
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        try:
            if kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) == DRIVE_REMOVABLE:
                found.append(Path(root))
        except OSError:
            continue
    return found


def read_update(drive: Path) -> dict | None:
    """The stick's own description of itself, or None if this is not a stick."""
    path = Path(drive) / UPDATE_JSON
    if not path.is_file() or not (Path(drive) / MANIFEST_JSON).is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def look(root: Path | str, drives) -> StickState:
    """What to say about the drives in front of us."""
    sticks = [(Path(drive), read_update(drive)) for drive in drives]
    sticks = [(drive, update) for drive, update in sticks if update is not None]

    if not sticks:
        return StickState("none", "No update stick found.")
    if len(sticks) > 1:
        named = " and ".join(str(drive) for drive, _ in sticks)
        return StickState(
            "many",
            f"There are two update sticks plugged in - {named}. "
            f"Unplug the one you do not want and press Look again.",
        )

    stick, update = sticks[0]
    theirs = update.get("version")
    if not isinstance(theirs, int):
        return StickState(
            "damaged",
            f"The stick in {stick} does not say which version it carries, "
            f"so it cannot be used. Build it again on the laptop.",
        )

    mine = read_version(root)
    if mine is None:
        return StickState(
            "ready",
            f"The stick has VMD {theirs}. This system's own version is unknown, "
            f"so it can be updated but not compared.",
            stick,
            theirs,
        )
    if theirs == mine:
        return StickState(
            "same", f"The stick has VMD {theirs} - the same version this system runs."
        )
    if theirs < mine:
        return StickState(
            "older",
            f"The stick has VMD {theirs} and this system runs VMD {mine}, so there "
            f"is nothing to install. Going back to an older version is the other "
            f"button.",
        )
    return StickState(
        "ready", f"The stick has VMD {theirs}. This system runs VMD {mine}.", stick, theirs
    )
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_stick.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add vmd/update/stick.py tests/test_update_stick.py
git commit -m "Which drive is the update stick, and what it is offering"
```

---

### Task 5: The smoke test the new version has to pass

**Files:**
- Create: `vmd/selftest.py`
- Create: `tests/test_selftest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_selftest.py
"""The check that decides whether an update is kept or thrown away."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def run_selftest(settings: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "vmd.selftest", "--settings", str(settings)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_a_working_copy_passes_and_says_so(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"camera": {"host": "192.0.2.10"}}', encoding="utf-8")

    result = run_selftest(settings)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "selftest ok" in result.stdout


def test_a_settings_file_that_will_not_parse_fails_the_test_with_the_reason(
    tmp_path: Path,
) -> None:
    """The one failure this catches that an import check cannot: an update that
    changed the settings model in a way this machine's own file no longer
    satisfies. The console would open on that and be unusable."""
    settings = tmp_path / "settings.json"
    settings.write_text("{ not json", encoding="utf-8")

    result = run_selftest(settings)

    assert result.returncode != 0
    assert "settings" in (result.stdout + result.stderr).lower()


def test_a_missing_settings_file_is_not_a_failure(tmp_path: Path) -> None:
    """A console that has never been set up has no settings file, and that is
    an ordinary state - `load_settings` answers it with defaults. Failing here
    would make the first update of a fresh install impossible."""
    result = run_selftest(tmp_path / "nothing.json")
    assert result.returncode == 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --frozen --no-sync python -m pytest tests/test_selftest.py -v`
Expected: FAIL, `No module named vmd.selftest`

- [ ] **Step 3: Write the module**

```python
# vmd/selftest.py
"""Does this copy of VMD actually work? One answer, one exit code.

Run by the updater after the new version is on the disk and before the console
is started, and it is the whole of what stands between an operator and a
console that will not open on a machine nobody can fix. Failing it puts the
previous version back.

What it checks is what an update can plausibly break:

  - the console's own modules import, which catches a half-copied tree and a
    library that did not arrive,
  - libVLC can be found, which is reported but not fatal - the console runs
    without a picture and says so, and refusing an update over it would be
    worse than the fault,
  - this machine's settings file still satisfies the model, which catches a
    change to the settings model that this particular file does not meet.

It never touches the camera, the network, the recorder or the disk budget: a
smoke test that can fail for a reason outside the software is a smoke test that
rolls back good updates.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that this copy of VMD works.")
    parser.add_argument("--settings", default="settings.json")
    args = parser.parse_args(argv)

    try:
        import vmd  # noqa: F401
        from vmd.desktop import app, live, settings_tab, window  # noqa: F401
        from vmd.settings import SettingsError, load_settings
    except Exception as failure:  # noqa: BLE001 - the point is to report it
        print(f"selftest failed: the console's own modules do not import: {failure}")
        return 1

    try:
        load_settings(Path(args.settings))
    except SettingsError as failure:
        print(f"selftest failed: this machine's settings file is not valid: {failure}")
        return 1
    except Exception as failure:  # noqa: BLE001
        print(f"selftest failed: the settings file could not be read: {failure}")
        return 1

    # Said, not judged. A console with no VLC opens, records and detects; it
    # shows no live picture and says so in its own words. That is not a reason
    # to throw away an update.
    try:
        from vmd.desktop.libvlc import prepare

        print(f"selftest: VLC found at {prepare().folder}")
    except Exception as failure:  # noqa: BLE001
        print(f"selftest: no live picture on this machine ({failure})")

    print("selftest ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run --frozen --no-sync python -m pytest tests/test_selftest.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add vmd/selftest.py tests/test_selftest.py
git commit -m "One command that answers whether this copy of VMD works"
```

---

### Task 6: Copying, keeping the old copy, and putting it back

**Files:**
- Create: `vmd/update/apply.py`
- Create: `tests/test_update_apply.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_update_apply.py
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
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_apply.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'vmd.update.apply'`

- [ ] **Step 3: Write the file-moving half of the applier**

```python
# vmd/update/apply.py
"""The updater itself: everything that is dangerous, in one place.

Run as its own process, by the interpreter in bin\\python\\, out of a COPY of
this tree in the temporary folder - see `vmd/update/runner.py`. It replaces the
files the console is made of, so it cannot be a thread inside the console, and
it cannot be run from the folder it is rewriting.

Stdlib only. It runs at the moment the environment is being replaced, so an
import of anything from .venv would be an updater that stops working exactly
when it is needed.

The order below is the whole design, and every step of it is there because of
what it prevents:

  verify        a stick that half arrived is refused before anything is touched
  note          what this machine has, written to the stick early, so a failed
                update still teaches the laptop something
  stop          nothing is replaced under a running program
  keep          the old program is copied aside before the first byte is written
  copy          the new program, by whitelist, never the machine's own things
  libraries     only when the lock changed, and only from the stick
  selftest      the new version has to prove it runs
  start / undo  and if it does not, the old one goes back
"""

from __future__ import annotations

import shutil
from pathlib import Path

# What an update is allowed to replace. A list of names rather than a rule,
# because a rule ("everything except...") is one refactor away from copying the
# .venv over itself.
COPY_IN = ("vmd", "scripts", "docs", "VERSION", "pyproject.toml", "uv.lock", "VMD.exe")
COPY_SUFFIXES = (".bat",)

# What is never touched, said out loud so it can be tested and read. Some of it
# is this site's own - its camera, its passwords, its footage - and some of it
# is simply not part of an update.
KEEP_OUT = (
    "settings.json",
    "go2rtc.json",
    "streaming.json",
    "detection.json",
    "cameras",
    "recordings",
    "footage",
    "clips",
    "bin",
    ".venv",
    "Ultralytics",
    "previous",
)

PREVIOUS = "previous"


def what_to_copy(files: Path | str) -> list[str]:
    """The names in the update that this machine will take."""
    files = Path(files)
    taken = []
    for entry in sorted(files.iterdir()):
        if entry.name in KEEP_OUT:
            continue
        if entry.name in COPY_IN or entry.suffix.lower() in COPY_SUFFIXES:
            taken.append(entry.name)
    return taken


def back_up(root: Path | str, version: int | None, names) -> Path:
    """Copy what is about to be overwritten into previous\\<version>\\.

    Only the names being replaced. A backup that also held settings.json would
    be a rollback that puts an old camera password back, which is a fault
    nobody would think to look for.
    """
    root = Path(root)
    kept = root / PREVIOUS / (str(version) if version is not None else "unknown")
    if kept.exists():
        shutil.rmtree(kept)
    kept.mkdir(parents=True)
    for name in names:
        source = root / name
        if not source.exists():
            continue
        if source.is_dir():
            shutil.copytree(source, kept / name)
        else:
            shutil.copy2(source, kept / name)
    return kept


def copy_in(files: Path | str, root: Path | str) -> list[str]:
    """Put the update in place. Returns what was copied."""
    files = Path(files)
    root = Path(root)
    copied = []
    for name in what_to_copy(files):
        source = files / name
        target = root / name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            replace_file(source, target)
        copied.append(name)
    return copied


def replace_file(source: Path, target: Path) -> None:
    """Copy one file over another, even when Windows will not have it replaced.

    VMD.exe is the reason. Windows refuses to overwrite an executable that is
    still open - which it can be moments after the console closed - but it will
    let one be RENAMED: the holder keeps the file it opened, under its new
    name. The leftover is deleted on the next run, when whatever held it has
    gone. `scripts/install.ps1` does the same thing to bin\\uv.exe, for the same
    reason and after the same bug.
    """
    for stale in target.parent.glob(f"{target.name}.old-*"):
        try:
            stale.unlink()
        except OSError:
            pass
    try:
        shutil.copy2(source, target)
        return
    except OSError:
        pass
    aside = target.with_name(f"{target.name}.old-replaced")
    if aside.exists():
        aside.unlink(missing_ok=True)
    target.rename(aside)
    shutil.copy2(source, target)


def restore(kept: Path | str, root: Path | str) -> list[str]:
    """Put a kept copy back over the install. Returns what was restored."""
    kept = Path(kept)
    root = Path(root)
    restored = []
    for entry in sorted(kept.iterdir()):
        target = root / entry.name
        if entry.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(entry, target)
        else:
            replace_file(entry, target)
        restored.append(entry.name)
    return restored
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_apply.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add vmd/update/apply.py tests/test_update_apply.py
git commit -m "Put the new program in place, and keep the one it replaced"
```

---

### Task 7: The run itself — status, log, marker, and the way back

**Files:**
- Modify: `vmd/update/apply.py`
- Modify: `tests/test_update_apply.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_update_apply.py`:

```python
import json

from vmd.update.apply import Report, run
from vmd.update.manifest import write as write_manifest


def a_stick(folder: Path, version: int, files: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copytree(files, folder / "files")
    (folder / "update.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    write_manifest(folder / "files", folder / "manifest.json")
    return folder


def test_a_good_update_lands_and_reports_the_two_versions(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    report = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
                 stop=lambda: None, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    assert report.ok is True
    assert report.moved_from == 7 and report.moved_to == 8
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "new\n"


def test_the_note_is_written_before_anything_is_replaced(tmp_path: Path) -> None:
    """Even a refused update teaches the laptop what this machine has. The trip
    is already wasted; it must not also be uninformative."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))
    (stick / "files" / "VERSION").write_text("9", encoding="utf-8")  # breaks the manifest

    report = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
                 stop=lambda: None, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    assert report.ok is False
    assert "VERSION" in report.message
    assert (stick / "machines" / "WIN-TEST.json").is_file()
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"


def test_a_new_version_that_does_not_run_is_thrown_away(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    report = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
                 stop=lambda: None, sync=lambda *_: (True, ""),
                 selftest=lambda: (False, "ImportError: no module named cv2"))

    assert report.ok is False
    assert "did not start" in report.message
    assert "cv2" in "\n".join(report.output)
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"
    assert (root / "VERSION").read_text(encoding="utf-8") == "7"


def test_libraries_that_will_not_install_undo_the_update_too(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    report = run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
                 stop=lambda: None,
                 sync=lambda *_: (False, "no wheel for numpy 2.2.0 on the stick"),
                 selftest=lambda: (True, ""))

    assert report.ok is False
    assert "numpy" in report.message
    assert (root / "vmd" / "app.py").read_text(encoding="utf-8") == "old\n"


def test_the_marker_is_up_while_it_runs_and_gone_afterwards(tmp_path: Path) -> None:
    """A power cut in the middle leaves the marker behind, and that is how the
    next start knows to offer a way back."""
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))
    seen = {}

    def stop() -> None:
        seen["marker"] = (root / "bin" / "logs" / "update-in-progress.json").is_file()

    run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
        stop=stop, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    assert seen["marker"] is True
    assert not (root / "bin" / "logs" / "update-in-progress.json").exists()


def test_every_step_is_written_where_the_console_can_read_it(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    stick = a_stick(tmp_path / "E", 8, new_files(tmp_path / "files"))

    run(root, stick, machine="WIN-TEST", when="2026-08-22T10:00:00",
        stop=lambda: None, sync=lambda *_: (True, ""), selftest=lambda: (True, ""))

    status = json.loads((root / "bin" / "logs" / "update-status.json").read_text(encoding="utf-8"))
    assert status["finished"] is True and status["ok"] is True
    assert (root / "bin" / "logs" / "update.log").read_text(encoding="utf-8").strip()
```

Add `import shutil` to the test file's imports.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_apply.py -v -k "run or note or marker or step or libraries"`
Expected: FAIL, `ImportError: cannot import name 'Report'`

- [ ] **Step 3: Write the run**

Append to `vmd/update/apply.py`:

```python
import json
from dataclasses import dataclass, field

from vmd.update import manifest as manifest_module
from vmd.update.note import write_note
from vmd.update.stick import FILES, MANIFEST_JSON, UPDATE_JSON
from vmd.update.version import read_version

LOGS = Path("bin") / "logs"
LOG = "update.log"
STATUS = "update-status.json"
MARKER = "update-in-progress.json"


@dataclass
class Report:
    """What happened, in the words the console shows and the log keeps."""

    ok: bool = False
    message: str = ""
    step: str = ""
    moved_from: int | None = None
    moved_to: int | None = None
    output: list[str] = field(default_factory=list)


class Progress:
    """The log and the status file, which are how the console watches this.

    The console cannot watch this process any other way: it is a separate
    program, started detached, that will still be running when the console has
    been killed. So every step is written down as it happens, and the panel
    reads the file.
    """

    def __init__(self, root: Path) -> None:
        self.folder = Path(root) / LOGS
        self.folder.mkdir(parents=True, exist_ok=True)
        self.report = Report()

    def say(self, step: str, line: str = "") -> None:
        self.report.step = step
        if line:
            self.report.output.append(line)
        with open(self.folder / LOG, "a", encoding="utf-8") as handle:
            handle.write(f"{step}{': ' + line if line else ''}\n")
        self.write_status(finished=False)

    def write_status(self, finished: bool) -> None:
        payload = {
            "step": self.report.step,
            "ok": self.report.ok if finished else None,
            "message": self.report.message,
            "from": self.report.moved_from,
            "to": self.report.moved_to,
            "output": self.report.output[-200:],
            "finished": finished,
        }
        (self.folder / STATUS).write_text(json.dumps(payload, indent=1), encoding="utf-8")

    def finish(self, ok: bool, message: str) -> Report:
        self.report.ok = ok
        self.report.message = message
        self.report.step = ""
        self.write_status(finished=True)
        return self.report


def run(root, stick, machine: str, when: str, stop, sync, selftest) -> Report:
    """Apply the update on the stick to the copy in `root`.

    `stop`, `sync` and `selftest` are handed in rather than called directly, and
    that is what makes this testable: the real ones kill processes, run uv and
    start a second interpreter, and none of those belong in a test that is about
    whether the right files end up in the right place. `vmd/update/runner.py`
    supplies the real three.
    """
    root = Path(root)
    stick = Path(stick)
    progress = Progress(root)
    progress.report.moved_from = read_version(root)

    files = stick / FILES
    try:
        update = json.loads((stick / UPDATE_JSON).read_text(encoding="utf-8"))
        listed = json.loads((stick / MANIFEST_JSON).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as failure:
        return progress.finish(False, f"The stick could not be read: {failure}")
    progress.report.moved_to = update.get("version")

    progress.say("checking the stick")
    problems = manifest_module.verify(files, listed)
    if problems:
        for line in problems:
            progress.say("checking the stick", line)
        return progress.finish(
            False,
            f"The stick is damaged: {len(problems)} file(s) do not match "
            f"({problems[0]}). Nothing was changed.",
        )

    # Before anything is replaced. Even a refused update tells the laptop what
    # this machine has.
    progress.say("writing this machine's note onto the stick")
    try:
        write_note(root, stick, machine=machine, when=when)
    except OSError as failure:
        progress.say("writing this machine's note onto the stick", str(failure))

    marker = progress.folder / MARKER
    marker.write_text(json.dumps({"started": when, "to": update.get("version")}), encoding="utf-8")
    try:
        progress.say("stopping the console")
        stop()

        names = what_to_copy(files)
        progress.say("keeping the version that is here now")
        kept = back_up(root, progress.report.moved_from, names)

        progress.say("copying the new version in")
        copy_in(files, root)

        progress.say("installing any new libraries")
        installed, said = sync(stick)
        if not installed:
            progress.say("installing any new libraries", said)
            restore(kept, root)
            return progress.finish(
                False,
                f"The libraries this update needs could not be installed ({said}). "
                f"VMD {progress.report.moved_from} was put back. Nothing was lost.",
            )

        progress.say("checking that the new version runs")
        works, said = selftest()
        if not works:
            progress.say("checking that the new version runs", said)
            restore(kept, root)
            return progress.finish(
                False,
                f"VMD {progress.report.moved_to} did not start, so VMD "
                f"{progress.report.moved_from} was put back. Nothing was lost.",
            )

        return progress.finish(
            True,
            f"Updated to VMD {progress.report.moved_to}. "
            f"The console will start again by itself.",
        )
    finally:
        marker.unlink(missing_ok=True)
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_apply.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add vmd/update/apply.py tests/test_update_apply.py
git commit -m "An update that does not prove itself is put back where it was"
```

---

### Task 8: The three real steps — stopping, syncing, self-testing

**Files:**
- Create: `vmd/update/runner.py`
- Create: `tests/test_update_runner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_update_runner.py
"""The three things the applier does to the machine rather than to files."""

from __future__ import annotations

from pathlib import Path

from vmd.update.runner import sync_command, selftest_command, temp_copy_of


def test_libraries_are_installed_from_the_stick_and_nowhere_else(tmp_path: Path) -> None:
    """--no-index is the whole of it. Without it uv reaches for PyPI, which on
    this machine means a minute of retries and then a failure that reads like a
    broken update rather than a machine with no internet."""
    command = sync_command(root=tmp_path / "VMD", stick=tmp_path / "E")

    assert command[0].endswith("uv.exe")
    assert "--offline" in command
    assert "--no-index" in command
    assert "--find-links" in command
    assert str((tmp_path / "E" / "wheels")) in command
    assert "--extra" in command and "detect" in command


def test_the_selftest_is_run_by_the_project_s_own_interpreter(tmp_path: Path) -> None:
    root = tmp_path / "VMD"
    (root / "bin").mkdir(parents=True)
    command = selftest_command(root=root, settings=root / "settings.json")

    assert command[0].endswith("uv.exe")
    assert command[1:5] == ["run", "--offline", "--frozen", "--no-sync"]
    assert "vmd.selftest" in command


def test_the_updater_runs_from_a_copy_of_itself(tmp_path: Path) -> None:
    """It is about to replace vmd\\, which is where it lives. A program cannot
    be sure of what it will read next while something rewrites it underneath,
    so it is copied out first and run from there."""
    root = tmp_path / "VMD"
    (root / "vmd" / "update").mkdir(parents=True)
    (root / "vmd" / "__init__.py").write_text("", encoding="utf-8")
    (root / "vmd" / "update" / "apply.py").write_text("# applier\n", encoding="utf-8")

    where = temp_copy_of(root, tmp_path / "temp")

    assert (where / "vmd" / "update" / "apply.py").read_text(encoding="utf-8") == "# applier\n"
    assert where != root
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_runner.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'vmd.update.runner'`

- [ ] **Step 3: Write the module**

```python
# vmd/update/runner.py
"""Starting the updater, and the three things it does to the machine.

The console calls `start`. Everything below it exists so that `apply.run` can be
tested without killing a process, running uv or starting an interpreter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from vmd.update.apply import LOGS, STATUS
from vmd.update.stick import WHEELS

TIMEOUT_SECONDS = 1800


def uv_exe(root: Path) -> Path:
    """The uv that travels with this project, and on the offline machine the
    only one there is."""
    return Path(root) / "bin" / "uv.exe"


def sync_command(root: Path, stick: Path) -> list[str]:
    """Install the libraries this update needs, from the stick.

    `--offline` and `--no-index` together, and both are load-bearing: the first
    stops uv consulting the network at all, the second stops it treating PyPI as
    a source it merely cannot reach right now. What is left is the wheels on the
    stick and whatever is already in uv's own cache on this machine.
    """
    return [
        str(uv_exe(root)),
        "sync",
        "--offline",
        "--frozen",
        "--no-index",
        "--find-links",
        str(Path(stick) / WHEELS),
        "--extra",
        "detect",
    ]


def selftest_command(root: Path, settings: Path) -> list[str]:
    return [
        str(uv_exe(root)),
        "run",
        "--offline",
        "--frozen",
        "--no-sync",
        "python",
        "-m",
        "vmd.selftest",
        "--settings",
        str(settings),
    ]


def temp_copy_of(root: Path, into: Path) -> Path:
    """Copy the `vmd` package out of the install, and answer where it went.

    The updater is part of the thing being updated. Running it out of the folder
    it is rewriting is asking Python to import a module out of a file that is
    being replaced under it - which works until the day it does not, on the
    machine where nobody can tell what went wrong.
    """
    into = Path(into)
    if into.exists():
        shutil.rmtree(into, ignore_errors=True)
    into.mkdir(parents=True)
    shutil.copytree(Path(root) / "vmd", into / "vmd")
    return into
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_runner.py -v`
Expected: 3 passed

- [ ] **Step 5: Add the entry point that ties them together**

Append to `vmd/update/runner.py`:

```python
def project_python(root: Path) -> Path | None:
    """The interpreter inside bin\\python\\, which no update replaces."""
    folder = Path(root) / "bin" / "python"
    if not folder.is_dir():
        return None
    found = sorted(folder.glob("*/python.exe"))
    return found[0] if found else None


def start(root: Path, stick: Path, settings: Path) -> tuple[bool, str]:
    """Start the updater as its own process and return at once.

    Detached, because this process is one of the things it is about to stop.
    """
    root = Path(root)
    status = root / LOGS / STATUS
    status.parent.mkdir(parents=True, exist_ok=True)
    status.unlink(missing_ok=True)

    python = project_python(root)
    if python is None:
        return False, "This copy has no interpreter in bin\\python\\, so it cannot update itself."

    where = temp_copy_of(root, Path(os.environ.get("TEMP", ".")) / "vmd-update")
    environment = dict(os.environ, PYTHONPATH=str(where))
    creation = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen(  # noqa: S603 - our own interpreter, our own module
        [
            str(python),
            "-m",
            "vmd.update.main",
            "--root",
            str(root),
            "--stick",
            str(stick),
            "--settings",
            str(settings),
        ],
        cwd=str(where),
        env=environment,
        creationflags=creation,
        close_fds=True,
    )
    return True, ""
```

```python
# vmd/update/main.py
"""What the detached updater process runs.

Thin on purpose: it wires the real stop, sync and selftest into `apply.run`,
and starts the console again afterwards. Everything it decides is decided in
`apply.py`, where it can be tested.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from vmd.update.apply import run
from vmd.update.runner import TIMEOUT_SECONDS, selftest_command, sync_command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a VMD update from a stick.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--stick", required=True)
    parser.add_argument("--settings", required=True)
    args = parser.parse_args(argv)

    root = Path(args.root)
    stick = Path(args.stick)

    def stop() -> None:
        """Stop the console and the streaming server, by the script that already
        knows how. `Stop-ProjectProcesses` has been doing this for install.bat
        since the day a uv sync deleted a .venv that python.exe was running out
        of."""
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f". '{root / 'scripts' / '_common.ps1'}'; Stop-ProjectProcesses '{root}' | Out-Null",
            ],
            capture_output=True,
            timeout=300,
            check=False,
        )

    def sync(where: Path) -> tuple[bool, str]:
        result = subprocess.run(
            sync_command(root, where),
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        return result.returncode == 0, (result.stderr or result.stdout).strip()[-400:]

    def selftest() -> tuple[bool, str]:
        result = subprocess.run(
            selftest_command(root, Path(args.settings)),
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()[-400:]

    report = run(
        root,
        stick,
        machine=os.environ.get("COMPUTERNAME", "unknown"),
        when=datetime.now().isoformat(timespec="seconds"),
        stop=stop,
        sync=sync,
        selftest=selftest,
    )

    # The console goes back up whichever version won. An operator left staring
    # at a desktop with no console is the worst outcome of an update, and it is
    # the one outcome that has nothing to do with whether the update worked.
    exe = root / "VMD.exe"
    starter = str(exe) if exe.is_file() else str(root / "VMD.bat")
    try:
        subprocess.Popen([starter, "--settings", args.settings], cwd=str(root), close_fds=True)
    except OSError:
        pass

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Prove the applier runs on the bare interpreter**

The updater is run by `bin\python\...\python.exe` with no virtual environment.
Anything it imports must be stdlib.

Run: `bin\python\cpython-3.12.9-windows-x86_64-none\python.exe -c "import sys; sys.path.insert(0, '.'); import vmd.update.main; print('bare import ok')"`
Expected: `bare import ok`

If it fails naming pydantic or PySide6, the import that pulled them in is the
bug — `vmd/update/` may not import from `vmd.settings` or `vmd.desktop`.

- [ ] **Step 7: Commit**

```bash
git add vmd/update/runner.py vmd/update/main.py tests/test_update_runner.py
git commit -m "The updater runs out of a copy of itself, on the interpreter no update replaces"
```

---

### Task 9: The Update panel

**Files:**
- Create: `vmd/desktop/update_panel.py`
- Create: `tests/test_desktop_update_panel.py`
- Modify: `vmd/desktop/settings_tab.py` (add the panel at the bottom of the form)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_desktop_update_panel.py
"""The one control on this machine that changes the software."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from vmd.desktop.update_panel import UpdatePanel


def a_stick(folder: Path, version: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "update.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps({"files": []}), encoding="utf-8")
    (folder / "files").mkdir(exist_ok=True)
    return folder


def a_console(folder: Path, version: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "VERSION").write_text(str(version), encoding="utf-8")
    return folder


def build(qtbot, root: Path, drives) -> UpdatePanel:
    panel = UpdatePanel(root=root, settings_path=root / "settings.json", drives=lambda: drives)
    qtbot.addWidget(panel)
    panel.look()
    return panel


def test_it_says_which_version_this_system_is(qtbot, tmp_path: Path) -> None:
    panel = build(qtbot, a_console(tmp_path / "VMD", 7), [])
    assert "VMD 7" in panel.this_system.text()


def test_with_no_stick_it_offers_to_look_again_and_not_to_update(
    qtbot, tmp_path: Path
) -> None:
    panel = build(qtbot, a_console(tmp_path / "VMD", 7), [])
    assert "No update stick" in panel.stick_line.text()
    assert panel.update_button.isEnabled() is False
    assert panel.look_button.isVisible() is True


def test_a_newer_stick_makes_the_update_button_live(qtbot, tmp_path: Path) -> None:
    stick = a_stick(tmp_path / "E", 8)
    panel = build(qtbot, a_console(tmp_path / "VMD", 7), [stick])
    assert "VMD 8" in panel.stick_line.text()
    assert panel.update_button.isEnabled() is True


def test_the_same_version_is_not_offered_as_an_update(qtbot, tmp_path: Path) -> None:
    stick = a_stick(tmp_path / "E", 7)
    panel = build(qtbot, a_console(tmp_path / "VMD", 7), [stick])
    assert panel.update_button.isEnabled() is False
    assert "same version" in panel.stick_line.text()


def test_going_back_is_offered_only_when_there_is_something_to_go_back_to(
    qtbot, tmp_path: Path
) -> None:
    root = a_console(tmp_path / "VMD", 8)
    panel = build(qtbot, root, [])
    assert panel.back_button.isVisible() is False

    (root / "previous" / "7").mkdir(parents=True)
    panel.look()
    assert panel.back_button.isVisible() is True
    assert "VMD 7" in panel.back_button.text()


def test_going_back_asks_first_and_a_no_does_nothing(qtbot, tmp_path, monkeypatch) -> None:
    """One press away from undoing an update somebody has just travelled to
    deliver, on a machine where the way to redo it is another trip."""
    root = a_console(tmp_path / "VMD", 8)
    (root / "previous" / "7").mkdir(parents=True)
    panel = build(qtbot, root, [])
    started = []
    panel.start_rollback = lambda version: started.append(version)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    panel.go_back()

    assert started == []


def test_going_back_after_a_yes_starts_it(qtbot, tmp_path, monkeypatch) -> None:
    root = a_console(tmp_path / "VMD", 8)
    (root / "previous" / "7").mkdir(parents=True)
    panel = build(qtbot, root, [])
    started = []
    panel.start_rollback = lambda version: started.append(version)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    panel.go_back()

    assert started == [7]


def test_an_interrupted_update_is_reported_at_the_next_start(qtbot, tmp_path: Path) -> None:
    """The marker file left by a power cut. Nobody would think to look for it,
    so the panel says it in the one place they will be looking."""
    root = a_console(tmp_path / "VMD", 7)
    logs = root / "bin" / "logs"
    logs.mkdir(parents=True)
    (logs / "update-in-progress.json").write_text('{"to": 8}', encoding="utf-8")

    panel = build(qtbot, root, [])

    assert "interrupted" in panel.stick_line.text().lower()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --frozen --no-sync python -m pytest tests/test_desktop_update_panel.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'vmd.desktop.update_panel'`

- [ ] **Step 3: Write the panel**

```python
# vmd/desktop/update_panel.py
"""The Update button, and the only control on this machine that changes it.

At the bottom of the Settings tab and not behind a fold: on an air-gapped
console this is now the whole of maintenance, and a control somebody has to
know about is a control somebody will not find.

Nothing here applies anything. It reads which version this is, what is on the
stick, and whether there is a version to go back to; the work is done by a
separate process - see `vmd/update/runner.py` - because the files being
replaced include this one.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vmd.update.apply import LOGS, MARKER, STATUS
from vmd.update.runner import start as start_update
from vmd.update.stick import look, removable_drives
from vmd.update.version import describe

logger = logging.getLogger(__name__)

WATCH_MS = 1000


class UpdatePanel(QGroupBox):
    def __init__(
        self,
        root: Path,
        settings_path: Path,
        drives=removable_drives,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Software", parent)
        self._root = Path(root)
        self._settings_path = Path(settings_path)
        self._drives = drives

        outer = QVBoxLayout(self)

        self.this_system = QLabel()
        outer.addWidget(self.this_system)

        line = QHBoxLayout()
        self.stick_line = QLabel()
        self.stick_line.setWordWrap(True)
        line.addWidget(self.stick_line, 1)

        self.look_button = QPushButton("Look again")
        self.look_button.clicked.connect(self.look)
        line.addWidget(self.look_button)

        self.update_button = QPushButton("Update now")
        self.update_button.clicked.connect(self.update_now)
        line.addWidget(self.update_button)
        outer.addLayout(line)

        self.back_button = QPushButton("Go back")
        self.back_button.clicked.connect(self.go_back)
        outer.addWidget(self.back_button)

        self._state = None
        self._watch = QTimer(self)
        self._watch.timeout.connect(self._read_status)

    # ------------------------------------------------------------- describing

    def previous_version(self) -> int | None:
        """The version kept by the last update, or None if there is not one."""
        folder = self._root / "previous"
        if not folder.is_dir():
            return None
        versions = [int(item.name) for item in folder.iterdir() if item.name.isdigit()]
        return max(versions) if versions else None

    def interrupted(self) -> dict | None:
        marker = self._root / LOGS / MARKER
        if not marker.is_file():
            return None
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def look(self) -> None:
        """Read everything and draw it. Cheap enough to call on every show."""
        self.this_system.setText(f"This system: {describe(self._root)}")

        previous = self.previous_version()
        self.back_button.setVisible(previous is not None)
        if previous is not None:
            self.back_button.setText(f"Go back to VMD {previous}")

        stopped = self.interrupted()
        if stopped is not None:
            self.stick_line.setText(
                "An update was interrupted before it finished. The version that "
                "was here has been kept - use Go back if this console is not "
                "behaving."
            )
            self.update_button.setEnabled(False)
            return

        self._state = look(self._root, self._drives())
        self.stick_line.setText(self._state.message)
        self.update_button.setEnabled(self._state.kind == "ready")

    # --------------------------------------------------------------- updating

    def update_now(self) -> None:
        if self._state is None or self._state.kind != "ready":
            return
        started, why = self.start_update(self._state.stick)
        if not started:
            self.stick_line.setText(why)
            return
        self.update_button.setEnabled(False)
        self.look_button.setEnabled(False)
        self.stick_line.setText("Updating. The console will close and start again.")
        self._watch.start(WATCH_MS)

    def start_update(self, stick: Path) -> tuple[bool, str]:
        """Overridden in tests. The real one starts a detached process."""
        return start_update(self._root, stick, self._settings_path)

    def go_back(self) -> None:
        previous = self.previous_version()
        if previous is None:
            return
        answer = QMessageBox.question(
            self,
            "Go back to the previous version?",
            f"Put VMD {previous} back?\n\n"
            f"The console will close and start again. Your settings, cameras and "
            f"recordings are not touched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.start_rollback(previous)

    def start_rollback(self, version: int) -> None:
        """Overridden in tests. Task 10 gives this its own runner entry."""
        from vmd.update.runner import start_rollback

        start_rollback(self._root, version, self._settings_path)

    def _read_status(self) -> None:
        path = self._root / LOGS / STATUS
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if status.get("finished"):
            self._watch.stop()
            self.look_button.setEnabled(True)
            self.look()
        self.stick_line.setText(status.get("message") or status.get("step") or "")
```

- [ ] **Step 4: Add the rollback entry the panel calls**

Append to `vmd/update/runner.py`:

```python
def start_rollback(root: Path, version: int, settings: Path) -> tuple[bool, str]:
    """Put a kept version back, in the same detached way an update is applied."""
    root = Path(root)
    python = project_python(root)
    if python is None:
        return False, "This copy has no interpreter in bin\\python\\."
    where = temp_copy_of(root, Path(os.environ.get("TEMP", ".")) / "vmd-update")
    creation = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen(  # noqa: S603 - our own interpreter, our own module
        [
            str(python),
            "-m",
            "vmd.update.main",
            "--root",
            str(root),
            "--rollback",
            str(version),
            "--settings",
            str(settings),
        ],
        cwd=str(where),
        env=dict(os.environ, PYTHONPATH=str(where)),
        creationflags=creation,
        close_fds=True,
    )
    return True, ""
```

And in `vmd/update/main.py`, make `--stick` optional and add the rollback path:

```python
    parser.add_argument("--stick")
    parser.add_argument("--rollback", type=int)
```

```python
    if args.rollback is not None:
        from vmd.update.apply import Progress, restore

        progress = Progress(root)
        progress.say("putting the previous version back")
        kept = root / "previous" / str(args.rollback)
        if not kept.is_dir():
            progress.finish(False, f"There is no kept copy of VMD {args.rollback}.")
            return 1
        stop()
        restore(kept, root)
        installed, said = sync_from_cache(root)
        progress.finish(
            installed,
            f"VMD {args.rollback} is back."
            if installed
            else f"VMD {args.rollback}'s files are back, but its libraries could not be "
            f"installed from this machine's cache ({said}). Bring a stick with "
            f"VMD {args.rollback} on it.",
        )
        start_console()
        return 0 if installed else 1
```

with, beside the other helpers in `main`:

```python
    def sync_from_cache(where: Path) -> tuple[bool, str]:
        """Going back needs the OLD libraries, and there is no stick for those.

        uv's own cache on this machine is where they are: they were installed
        here once. When it has been cleared this fails, and the message says so
        rather than pretending the rollback was clean.
        """
        result = subprocess.run(
            [str(root / "bin" / "uv.exe"), "sync", "--offline", "--frozen", "--extra", "detect"],
            cwd=str(where),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        return result.returncode == 0, (result.stderr or result.stdout).strip()[-400:]
```

Move the console-starting lines at the end of `main` into a `start_console()`
function so both paths use it.

- [ ] **Step 5: Run the panel tests**

Run: `uv run --frozen --no-sync python -m pytest tests/test_desktop_update_panel.py -v`
Expected: 8 passed

- [ ] **Step 6: Put the panel on the Settings tab**

In `vmd/desktop/settings_tab.py`, after the `tools_box` is added to `layout`
(the "Check the camera" fold, currently the last thing on the form):

```python
        # Last on the form and not behind a fold. On a machine with no internet
        # this is the whole of maintenance, and the operator has to be able to
        # find it without being told where to look.
        self.update_panel = UpdatePanel(
            root=Path(__file__).resolve().parent.parent.parent,
            settings_path=self.settings_path,
        )
        layout.addWidget(self.update_panel)
```

with `from vmd.desktop.update_panel import UpdatePanel` at the top, and in
`load`, after the rest of the form is filled:

```python
        self.update_panel.look()
```

- [ ] **Step 7: Run the settings tab suite**

Run: `uv run --frozen --no-sync python -m pytest tests/test_desktop_settings_tab.py tests/test_desktop_update_panel.py -v`
Expected: all pass. If a test asserting the tab's last widget fails, update it
to expect the Software box.

- [ ] **Step 8: Commit**

```bash
git add vmd/desktop/update_panel.py tests/test_desktop_update_panel.py vmd/desktop/settings_tab.py vmd/update/runner.py vmd/update/main.py
git commit -m "An Update button on the console, and a way back that asks first"
```

---

### Task 10: The laptop that fills the stick

**Files:**
- Create: `scripts/update_stick.ps1`
- Create: `VMD-Update-Stick.bat`
- Create: `tests/test_update_stick_builder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_update_stick_builder.py
"""The laptop side, driven the way the GUI drives it.

PowerShell rather than Python because the laptop has nothing installed on it -
no git, no Python, nothing to keep up to date. It is still tested here: the
script takes a folder instead of a download, so a test can hand it a fake
repository and read what lands on the fake stick.
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
    (folder / "uv.lock").write_text('version = 1\n', encoding="utf-8")
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
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_stick_builder.py -v`
Expected: FAIL, the script does not exist

- [ ] **Step 3: Write the script**

```powershell
# scripts/update_stick.ps1
# =============================================================================
#  Fills the VMD update stick, on a laptop with nothing installed on it.
#
#  Double-click VMD-Update-Stick.bat. It downloads the current code from
#  GitHub, works out which libraries the offline machine is missing, and writes
#  the lot onto the stick with a manifest so that the machine at the other end
#  can prove it all arrived.
#
#  Nothing has to be installed for this to work: no git, no Python. The code
#  comes down as a ZIP over HTTPS, and the two tools it needs for libraries -
#  uv and an interpreter - are put on the stick itself the first time they are
#  wanted.
#
#  -SourceFolder and -NoWheels exist for the tests: they let the whole of this
#  run without a network, against a folder that stands in for the download.
# =============================================================================
param(
    [string]$To,
    [string]$Repository = 'noamsolomon123/vmd',
    [string]$Branch = 'master',
    [string]$SourceFolder,
    [switch]$NoWheels,
    [switch]$Gui
)

$ErrorActionPreference = 'Stop'

# What never leaves the developer's machine, whatever is in the checkout: the
# same rule scripts\offline_kit.ps1 states at length, for the same reason.
$KEEP_BACK = @('.git', '.venv', 'bin', 'recordings', 'footage', 'clips',
               'settings.json', 'go2rtc.json', 'streaming.json', 'detection.json',
               'cameras', 'Ultralytics', 'previous', '.pytest_cache')

function Say($text) { Write-Host $text }

function Get-Source {
    if ($SourceFolder) { return (Resolve-Path $SourceFolder).Path }
    $zip = Join-Path $env:TEMP 'vmd-update.zip'
    $unpacked = Join-Path $env:TEMP 'vmd-update-src'
    Say "Downloading $Repository ($Branch) from GitHub."
    Invoke-WebRequest -Uri "https://codeload.github.com/$Repository/zip/refs/heads/$Branch" `
        -OutFile $zip -UseBasicParsing
    if (Test-Path $unpacked) { Remove-Item $unpacked -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $unpacked -Force
    Remove-Item $zip -Force
    return (Get-ChildItem $unpacked -Directory | Select-Object -First 1).FullName
}

function Copy-Program($source, $target) {
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    foreach ($entry in (Get-ChildItem $source -Force)) {
        if ($KEEP_BACK -contains $entry.Name) { continue }
        if ($entry.PSIsContainer) {
            Copy-Item $entry.FullName (Join-Path $target $entry.Name) -Recurse -Force
        } else {
            Copy-Item $entry.FullName (Join-Path $target $entry.Name) -Force
        }
    }
}

function Get-Manifest($folder) {
    $files = @()
    $prefix = (Resolve-Path $folder).Path.TrimEnd('\') + '\'
    foreach ($file in (Get-ChildItem $folder -Recurse -File -Force | Sort-Object FullName)) {
        $files += [ordered]@{
            # Forward slashes: the machine at the other end compares these as
            # text, and the zip and JSON formats both say so.
            path   = $file.FullName.Substring($prefix.Length).Replace('\', '/')
            size   = $file.Length
            sha256 = (Get-FileHash $file.FullName -Algorithm SHA256).Hash.ToLower()
        }
    }
    return [ordered]@{ files = $files }
}

function Write-Json($object, $path) {
    $json = $object | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

if (-not $To) { throw "Say which drive to write to: -To E:\" }
New-Item -ItemType Directory -Force -Path $To | Out-Null

$source = Get-Source
$version = [int]((Get-Content (Join-Path $source 'VERSION') -Raw).Trim())
Say "This is VMD $version."

$files = Join-Path $To 'files'
Copy-Program $source $files

# What the machines on this stick already have. One file per machine, written
# by the machine itself - see vmd\update\note.py.
$notes = @()
$machinesDir = Join-Path $To 'machines'
if (Test-Path $machinesDir) {
    $notes = @(Get-ChildItem $machinesDir -Filter '*.json' -ErrorAction SilentlyContinue)
}
if ($notes.Count -eq 0) {
    Say "This stick has never been to a VMD machine, so it carries code only."
    Say "If the update needs a new library the console will say so and change nothing."
}

if (-not $NoWheels -and $notes.Count -gt 0) {
    # Filled in by Task 11. Until then the stick carries code, which is what
    # every update but one is.
    Say "Working out which libraries are missing."
}

Write-Json (Get-Manifest $files) (Join-Path $To 'manifest.json')
Write-Json ([ordered]@{
    version = $version
    built   = (Get-Date).ToString('s')
    branch  = $Branch
    source  = $Repository
}) (Join-Path $To 'update.json')

$readme = @"
VMD update stick - VMD $version, built $((Get-Date).ToString('dd MMM yyyy'))

Take this stick to the VMD computer, open the console, go to the Settings tab
and press "Update now" at the bottom.

Do not put anything else on this stick. Everything on it is checked against
manifest.json before it is installed, and anything unexpected stops the update.
"@
[System.IO.File]::WriteAllText((Join-Path $To 'README.txt'),
    ($readme -replace "`r?`n", "`r`n"), (New-Object System.Text.UTF8Encoding($false)))

Say "Stick ready: VMD $version at $To"
exit 0
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_stick_builder.py -v`
Expected: 6 passed

- [ ] **Step 5: Write the door**

```bat
REM VMD-Update-Stick.bat
@echo off
REM ============================================================
REM  Fills the VMD update stick from GitHub.
REM
REM  Copy this file and the scripts folder onto any Windows laptop
REM  that has internet. Nothing needs to be installed.
REM
REM  This file is only the door. The work is in scripts\update_stick.ps1.
REM ============================================================

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_stick.ps1" -Gui %*
set RESULT=%ERRORLEVEL%

echo.
if %RESULT% NEQ 0 (
  echo The stick was not finished. The messages above say why.
)
echo Press any key to close this window.
pause >nul
exit /b %RESULT%
```

- [ ] **Step 6: Commit**

```bash
git add scripts/update_stick.ps1 VMD-Update-Stick.bat tests/test_update_stick_builder.py
git commit -m "Fill the update stick from GitHub, on a laptop with nothing on it"
```

---

### Task 11: The window, and the libraries the machine lacks

**Files:**
- Modify: `scripts/update_stick.ps1`
- Modify: `tests/test_update_stick_builder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_update_stick_builder.py`:

```python
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
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_stick_builder.py -v -k lacks`
Expected: FAIL - `-ListWheelsOnly` is not a parameter

- [ ] **Step 3: Add the diff and the fetch**

In `scripts/update_stick.ps1`, add `[switch]$ListWheelsOnly` to `param`, and
these functions above the main body:

```powershell
# The packages a lock file pins, as name -> version. Read with a regex rather
# than a TOML parser, because a TOML parser is a library and this laptop has
# nothing installed on it. uv.lock's shape is stable and simple: a [[package]]
# table with a name and a version in it.
function Get-LockedPackages($lockPath) {
    $packages = @{}
    if (-not (Test-Path $lockPath)) { return $packages }
    $name = $null
    foreach ($line in (Get-Content $lockPath)) {
        if ($line -match '^\s*\[\[package\]\]') { $name = $null; continue }
        if ($line -match '^\s*name\s*=\s*"([^"]+)"') { $name = $Matches[1]; continue }
        if ($line -match '^\s*version\s*=\s*"([^"]+)"' -and $name) {
            $packages[(Normalise-Name $name)] = $Matches[1]
            $name = $null
        }
    }
    return $packages
}

# PEP 503, and it has to agree with vmd\update\note.py's `normalise`: the
# machine writes pyside6-essentials and the lock says PySide6_Essentials.
function Normalise-Name($name) {
    return ($name -replace '[-_.]+', '-').ToLower()
}

# What no machine on this stick has yet. The union over every machine, because
# one stick may serve two sites and a wheel packed for one costs the other
# nothing.
function Get-MissingPackages($locked, $notes) {
    $missing = @{}
    foreach ($note in $notes) {
        $have = @{}
        try {
            $parsed = Get-Content $note.FullName -Raw | ConvertFrom-Json
            foreach ($property in $parsed.libraries.PSObject.Properties) {
                $have[(Normalise-Name $property.Name)] = [string]$property.Value
            }
        } catch { continue }
        foreach ($name in $locked.Keys) {
            if ($have[$name] -ne $locked[$name]) { $missing[$name] = $locked[$name] }
        }
    }
    return $missing
}

# uv installs an interpreter onto the stick the first time wheels are wanted,
# and that interpreter brings pip with it. So the laptop needs nothing and the
# stick grows one tool it keeps for next time.
function Get-StickPython($stick) {
    $uv = Join-Path $stick 'tools\uv.exe'
    if (-not (Test-Path $uv)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $uv) | Out-Null
        Say "Fetching uv onto the stick (14 MB, once)."
        $zip = Join-Path $env:TEMP 'uv.zip'
        Invoke-WebRequest -UseBasicParsing -OutFile $zip `
            -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip'
        Expand-Archive $zip (Split-Path $uv) -Force
        Remove-Item $zip -Force
    }
    $pythonDir = Join-Path $stick 'tools\python'
    $found = Get-ChildItem $pythonDir -Filter 'python.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $found) {
        Say "Fetching a Python onto the stick (20 MB, once)."
        & $uv python install --install-dir $pythonDir 3.12 | Out-Host
        $found = Get-ChildItem $pythonDir -Filter 'python.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
            Select-Object -First 1
    }
    if (-not $found) { throw "Could not put a Python on the stick." }
    return $found.FullName
}
```

and replace the `if (-not $NoWheels -and $notes.Count -gt 0)` block with:

```powershell
$missing = @{}
if ($notes.Count -gt 0) {
    $locked = Get-LockedPackages (Join-Path $source 'uv.lock')
    $missing = Get-MissingPackages $locked $notes
    foreach ($name in ($missing.Keys | Sort-Object)) { Say "  needs $name==$($missing[$name])" }
    if ($missing.Count -eq 0) { Say "Every library this update needs is already on the machine." }
}

if ($ListWheelsOnly) { exit 0 }

$wheels = Join-Path $To 'wheels'
New-Item -ItemType Directory -Force -Path $wheels | Out-Null
if (-not $NoWheels -and $missing.Count -gt 0) {
    $python = Get-StickPython $To
    foreach ($name in ($missing.Keys | Sort-Object)) {
        $pin = "$name==$($missing[$name])"
        Say "Downloading $pin"
        # --no-deps because the pins come from the lock, which already resolved
        # them: letting pip resolve again would pull versions this machine is
        # not going to install. The platform is stated rather than inherited -
        # the laptop is not the machine this is for.
        & $python -m pip download $pin --no-deps --only-binary=:all: `
            --platform win_amd64 --python-version 3.12 --implementation cp `
            --dest $wheels | Out-Host
        if ($LASTEXITCODE -ne 0) { Say "  could not download $pin" }
    }
}
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run --frozen --no-sync python -m pytest tests/test_update_stick_builder.py -v`
Expected: 7 passed

- [ ] **Step 5: Add the window**

At the end of `scripts/update_stick.ps1`, before `exit 0`, nothing changes. Add
the GUI as a wrapper at the TOP of the main body, so the script is one file that
either shows a window or does the work:

```powershell
if ($Gui) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'VMD update stick'
    $form.Size = New-Object System.Drawing.Size(560, 260)
    $form.StartPosition = 'CenterScreen'

    $label = New-Object System.Windows.Forms.Label
    $label.Text = 'USB drive:'
    $label.Location = New-Object System.Drawing.Point(16, 20)
    $label.AutoSize = $true
    $form.Controls.Add($label)

    $drives = New-Object System.Windows.Forms.ComboBox
    $drives.Location = New-Object System.Drawing.Point(100, 16)
    $drives.Width = 420
    $drives.DropDownStyle = 'DropDownList'
    foreach ($drive in (Get-WmiObject Win32_LogicalDisk -Filter 'DriveType=2')) {
        $version = ''
        $updateJson = Join-Path $drive.DeviceID '\update.json'
        if (Test-Path $updateJson) {
            try { $version = " (VMD $((Get-Content $updateJson -Raw | ConvertFrom-Json).version))" } catch { }
        }
        [void]$drives.Items.Add("$($drive.DeviceID)\$version")
    }
    if ($drives.Items.Count -gt 0) { $drives.SelectedIndex = 0 }
    $form.Controls.Add($drives)

    $status = New-Object System.Windows.Forms.TextBox
    $status.Multiline = $true
    $status.ReadOnly = $true
    $status.ScrollBars = 'Vertical'
    $status.Location = New-Object System.Drawing.Point(16, 60)
    $status.Size = New-Object System.Drawing.Size(504, 110)
    $form.Controls.Add($status)

    $go = New-Object System.Windows.Forms.Button
    $go.Text = 'Build the stick'
    $go.Location = New-Object System.Drawing.Point(400, 180)
    $go.Size = New-Object System.Drawing.Size(120, 30)
    $go.Add_Click({
        $chosen = ($drives.SelectedItem -split ' ')[0]
        if (-not $chosen) { $status.AppendText("Plug the stick in and open this again.`r`n"); return }
        $go.Enabled = $false
        $status.AppendText("Building on $chosen ...`r`n")
        $form.Refresh()
        $output = & powershell -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -To $chosen 2>&1
        foreach ($line in $output) { $status.AppendText("$line`r`n") }
        $go.Enabled = $true
    })
    $form.Controls.Add($go)

    [void]$form.ShowDialog()
    exit 0
}
```

- [ ] **Step 6: Check the window opens**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\update_stick.ps1 -Gui`
Expected: a window with a drive list and a **Build the stick** button. Close it.
This one is checked by eye - a WinForms dialog cannot be asserted on here, and
the work it calls is what the tests cover.

- [ ] **Step 7: Commit**

```bash
git add scripts/update_stick.ps1 tests/test_update_stick_builder.py VMD-Update-Stick.bat
git commit -m "Pack the wheels the machine actually lacks, and a window to press"
```

---

### Task 12: Retire the old updater, ship the new one, write it down

**Files:**
- Delete: `vmd/updater.py`, `tests/test_updater.py`
- Modify: `scripts/offline_kit.ps1`
- Modify: `docs/OFFLINE-SETUP.md`, `INSTALL.md`
- Modify: `docs/superpowers/specs/2026-08-22-offline-updates-design.md`

- [ ] **Step 1: Prove nothing still uses the old updater**

Run: `grep -rn "updater" --include=*.py vmd/ tests/`
Expected: only `vmd/updater.py` and `tests/test_updater.py` themselves. If the
console imports it anywhere, that import goes first.

- [ ] **Step 2: Delete it**

```bash
git rm vmd/updater.py tests/test_updater.py
```

- [ ] **Step 3: Make the kit carry VERSION**

In `scripts/offline_kit.ps1`, add to the `$checks` list, beside the other
things the offline machine cannot do without:

```powershell
    @{ Path = (Join-Path $root 'VERSION');                  What = 'the version number (VERSION)'; Fix = 'run install.bat' }
```

- [ ] **Step 4: Run the kit's own verification**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\offline_kit.ps1 -VerifyOnly`
Expected: a green line reading `the version number (VERSION)`

- [ ] **Step 5: Write the operator's instructions**

Add to `docs/OFFLINE-SETUP.md`, after Part 2:

```markdown
## Part 4 — Updating it later

The offline computer never gets an internet connection. New versions travel on
one USB stick, dedicated to VMD and used for nothing else.

**On the laptop with internet:**

1. Plug in the VMD stick.
2. Double-click `VMD-Update-Stick.bat`.
3. Choose the drive and press **Build the stick**. It downloads the current
   version from GitHub and writes it to the stick, along with any libraries the
   VMD computer does not have yet.

**On the VMD computer:**

1. Plug the stick in.
2. Open the console, go to **Settings**, and look at the **Software** box at the
   bottom. It says which version this system is and which version the stick has.
3. Press **Update now**. The console closes, updates, checks the new version
   actually runs, and opens again. If it does not run, the previous version is
   put back by itself and the box says so.
4. Take the stick back to the laptop. It now carries a note about this machine,
   which is how the next build knows which libraries to pack.

**Going back.** The version that was replaced is kept. The **Go back to VMD N**
button in the same box puts it back; it asks first.

**Nothing on the stick but VMD.** Everything on it is checked against
`manifest.json` before anything is installed, and an unexpected file stops the
update.
```

- [ ] **Step 6: Record the deviation in the spec**

In `docs/superpowers/specs/2026-08-22-offline-updates-design.md`, in the console
section, replace the sentence naming `scripts/apply_update.ps1` with:

```markdown
The console does not apply the update: it starts `vmd/update/main.py` as a
detached process, run by the interpreter in `bin\python\` out of a temporary
copy of the `vmd` package, and watches `bin\logs\update-status.json`. Python
rather than PowerShell as first sketched: the applier has to be exercised
against a fake install tree in a test, and this repository tests with pytest and
has no PowerShell test runner. It is stdlib-only, because it runs while the
environment it would otherwise import from is being replaced.
```

- [ ] **Step 7: Run everything**

Run: `uv run --frozen --no-sync python -m pytest tests -q --ignore=tests/test_desktop_export_integration.py --ignore=tests/test_recorder_integration.py --ignore=tests/test_desktop_video_vlc.py`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add -A vmd tests scripts docs INSTALL.md
git commit -m "Retire the updater that needed the internet, and write down the one that does not"
```

---

### Task 13: One real stick, one real machine

**Files:** none — this is the check that the twelve tasks above add up.

- [ ] **Step 1: Build a stick for real**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\update_stick.ps1 -To <a real USB drive>`
Expected: `Stick ready: VMD <n>`, and the drive holds `files\`, `manifest.json`,
`update.json`, `README.txt`.

- [ ] **Step 2: Make a second copy of the install to update**

```bash
robocopy C:\dev\VMD C:\vmd-update-test /E /XD .git recordings footage previous /NFL /NDL /NP
```

- [ ] **Step 3: Bump the version on the stick so there is something to install**

Edit `files\VERSION` on the stick to one more than the copy's, then rebuild the
manifest so the stick still verifies:

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\update_stick.ps1 -SourceFolder C:\dev\VMD -To <drive> -NoWheels`
after bumping `VERSION` in the source.

- [ ] **Step 4: Update the copy**

Run: `C:\vmd-update-test\VMD.exe` → Settings → Software → **Update now**
Expected: the console closes, `bin\logs\update.log` shows every step, the
console opens again, and the Software box reads the new version.

- [ ] **Step 5: Check the machine's own things were not touched**

Run: `type C:\vmd-update-test\settings.json`
Expected: unchanged. `cameras\` and `recordings\` likewise.

- [ ] **Step 6: Go back**

Press **Go back to VMD N**, answer Yes.
Expected: the console restarts on the old version and the Software box says so.

- [ ] **Step 7: Take the stick to the laptop and look at the note**

Expected: `machines\<computer name>.json` on the stick, holding the version and
the library list.

- [ ] **Step 8: Commit nothing, report what happened**

This task produces no code. If anything above did not behave, that is a bug to
fix in the task that owns it, not here.
